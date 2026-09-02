"""The per-chunk timeout, enforced from outside the process it applies to.

`TranscriptionRequest.timeout_s` is honoured in-call by adapters that can honour
it. The local one cannot, and says so: once CTranslate2 enters its C++ decode
loop there is no budget Python can enforce from inside it. So a chunk that never
returns holds its worker forever, the job sits at 41 of 87, and nothing in the
system notices — the pid is alive, the record says TRANSCRIBING, and every status
poll agrees. Killing the process from outside is the only enforcement that
exists for that case, and this is where it happens.

**The progress signal is the heartbeat, not a clock of this module's own.** The
worker writes one at the top of every chunk iteration, and a job reaches
TRANSCRIBING only after extraction and planning are done — so for a job in that
state, the age of the heartbeat *is* how long the current chunk has been running.
That is precisely the quantity a per-chunk timeout is defined over. Watching
`results/` mtime instead, as the task originally sketched, would reach around
`TranscriptStoragePort` into the filesystem from the composition root to
reconstruct a signal the port already publishes — and it would measure from the
moment a chunk *finished* rather than the moment the current one *started*.

Two conditions must hold together, and the second is not decoration:

- The heartbeat is older than the per-chunk timeout.
- The job has been in TRANSCRIBING for longer than the per-chunk timeout.

The worker does not refresh its heartbeat during extraction, and extracting a
three-hour recording outlasts a thirty-minute chunk timeout comfortably. Without
the second condition the first sweep after a long extraction would kill a job
that had just started working — turning the input this project exists for into
the case it cannot process.

Writing to the record here is legitimate for the same reason startup reconcile's
write is: by then this module has killed the worker, so nothing else owns it.
"""

import asyncio
import os
import signal
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace

from onevoicecut.domain.chunking import ChunkResult, ChunkState
from onevoicecut.domain.errors import DomainError
from onevoicecut.domain.ids import JobId
from onevoicecut.domain.jobs import (
    TERMINAL_STATES,
    WORKER_BOUND_STATES,
    JobRecord,
    JobState,
)
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.usecases.resume_job import pending_chunks

# Two hours. Sized from the longest gap the loop can produce between heartbeats:
# one chunk retried up to three times under the thirty-minute per-chunk timeout,
# or the extraction phase that precedes the first boundary on multi-hour input.
# It is a constant rather than a setting because it is a property of the liveness
# rule — lower it and healthy jobs get orphaned, raise it and it stops meaning
# anything. Neither is a knob worth handing an operator.
HEARTBEAT_STALE_AFTER_S = 7200.0

LivenessProbe = Callable[[int], bool]


def process_is_alive(pid: int) -> bool:
    """Signal 0 is a probe, not a signal, on both platforms this runs on.

    Verified on CPython 3.12 / Windows rather than assumed: `os.kill(pid, 0)`
    returns for a live process, raises `OSError` for a dead one, and — unlike
    every other signal value there — does not terminate anything. `os.kill(pid, 9)`
    on the same platform kills with exit code 9, so the special case is real and
    worth naming.

    A recycled pid reads as alive, and this probe cannot tell the difference.
    That used to be an accepted risk on a single-operator machine; it is not one
    on a shared server, where the orphaned job is somebody else's and sits on the
    board in a state nobody can clear. It is also blind to a worker that is
    running and stuck, which is the more common failure on multi-hour work.

    Both holes are closed one level up: `worker_is_alive` pairs this with the
    heartbeat, and a stale heartbeat vetoes whatever this says. Nothing outside
    that helper should call this directly.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def worker_is_alive(
    job: JobRecord,
    storage: TranscriptStoragePort,
    *,
    is_alive: LivenessProbe = process_is_alive,
    now: float,
) -> bool:
    """The one definition, shared by reconcile and the capacity derivation.

    Both of them ask this question, and if they answered it differently the
    system would deadlock quietly: the gate would hold a slot for a worker
    reconcile had already declared dead, and the queue behind it would never
    move.

    Three facts, and any of them alone is enough to say no:

    - **No pid.** The claim never happened; there is nothing to be alive.
    - **Dead pid.** The heartbeat is a record of the past, and the past does not
      keep a process running.
    - **Stale heartbeat.** This is the pid-reuse veto, and it is why a bare
      `os.kill(pid, 0)` was never sufficient: the number now belongs to some
      other process, which answers the probe perfectly well. It also catches a
      worker that is running and stuck, which the pid check cannot see at all.
    """
    if job.worker_pid is None or not is_alive(job.worker_pid):
        return False
    return storage.heartbeat_is_fresh(
        job.job_id, now_s=now, stale_after_s=HEARTBEAT_STALE_AFTER_S
    )


Killer = Callable[[int], None]


def kill_worker(pid: int) -> None:
    """SIGKILL, not SIGTERM.

    The worker being killed is, by definition, one that is not returning from
    native inference. A signal it has to reach a Python handler to honour is a
    request that specific process cannot service — which is the whole reason
    this sweep exists rather than a cooperative in-call timeout.

    `SIGKILL` is absent on Windows; `signal.SIGTERM` there maps onto
    `TerminateProcess`, which is unconditional in the same way.
    """
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def watchdog_once(
    storage: TranscriptStoragePort,
    *,
    chunk_timeout_s: float,
    now: Callable[[], float] = time.time,
    kill: Killer = kill_worker,
    is_alive: LivenessProbe = process_is_alive,
) -> tuple[JobId, ...]:
    """One sweep: kill every worker stuck on a chunk, and record what it lost.

    Returns the jobs it acted on, so the caller can log them. A job whose worker
    could not be signalled is not in the result and is left exactly as it was —
    the next sweep sees it again, which is the right outcome for a transient
    failure and harmless for a permanent one.
    """
    at = now()
    killed: list[JobId] = []

    for job in storage.list_jobs():
        if not _is_stalled(job, storage, chunk_timeout_s=chunk_timeout_s, at=at):
            continue
        if job.worker_pid is None or not is_alive(job.worker_pid):
            continue
        try:
            kill(job.worker_pid)
        except OSError:
            # Already reaped, or owned by another user. Either way this sweep
            # runs for the life of the process and must reach the jobs behind
            # this one; a raise here would stop the sweep at the first casualty.
            continue
        _record_timeout(job, storage, chunk_timeout_s=chunk_timeout_s, at=at)
        killed.append(job.job_id)

    return tuple(killed)


def reap_exited_workers(
    storage: TranscriptStoragePort,
    *,
    exited: tuple[tuple[JobId, int], ...],
    now: Callable[[], float] = time.time,
) -> tuple[JobId, ...]:
    """Turn "the child is gone" into something the operator can read.

    The parent is the only party that can observe a worker's exit, and it used to
    discard it. Two failures came out of that. An unusable engine makes the
    worker print its reason to stderr and exit 3, leaving the job in QUEUED with
    no explanation anywhere a browser can reach; and a worker that dies after
    claiming its job leaves a worker-bound record with a dead pid, which only
    startup reconcile clears — so the job is stranded until the next restart.

    The classification is by what the record says, not by the exit code, because
    the record is what the next reader acts on:

    - **QUEUED** — never claimed, so nothing else will ever write this record.
      FAILED, naming the exit code, because the operator needs a reason.
    - **Worker-bound** — claimed, then died mid-flight. INTERRUPTED, the
      resumable off-ramp; every committed chunk is still on disk. The same
      conclusion reconcile reaches at boot, reached continuously instead.
    - **Terminal** — the worker wrote its own account before exiting. Replacing
      it with the parent's inference from an exit code would lose the better
      answer.
    """
    at = now()
    reaped: list[JobId] = []

    for job_id, code in exited:
        try:
            job = storage.load_job(job_id)
        except DomainError:
            # Deleted under us. One missing record must not take the reaping of
            # every other exited worker with it.
            continue

        if job.state in TERMINAL_STATES:
            continue
        if job.state is JobState.QUEUED:
            storage.update_job(
                replace(
                    job,
                    state=JobState.FAILED,
                    updated_at=at,
                    error=(
                        f"the worker exited with status {code} without starting "
                        f"the job; see the server log for what it reported"
                    ),
                )
            )
        elif job.state in WORKER_BOUND_STATES:
            storage.update_job(
                replace(job, state=JobState.INTERRUPTED, updated_at=at)
            )
        else:
            # PENDING: no media, nothing was spawned for it.
            continue
        reaped.append(job_id)

    return tuple(reaped)


async def watchdog_supervisor(
    storage: TranscriptStoragePort,
    *,
    chunk_timeout_s: float,
    interval_s: float,
    kill: Killer = kill_worker,
    is_alive: LivenessProbe = process_is_alive,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Sweep forever, and survive a bad sweep.

    A second supervised task beside the drain rather than a branch inside it.
    They answer different questions on different clocks — the drain asks "is
    there a free slot" every five seconds, this asks "has this chunk stopped
    moving" on the scale of the per-chunk timeout — and a drain sweep that raised
    would otherwise take the timeout down with it.

    A sweep that raises is reported and the loop continues, for the same reason
    the drain's does: one unreadable record must not retire the per-chunk timeout
    for the whole machine. A watchdog that died quietly would be worse than one
    that never existed, because the design still promises the timeout.

    `CancelledError` is deliberately not caught: it is shutdown, not a failing
    sweep, and swallowing it would leave a task the event loop cannot stop.
    """
    while True:
        try:
            watchdog_once(
                storage,
                chunk_timeout_s=chunk_timeout_s,
                kill=kill,
                is_alive=is_alive,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - a bad sweep must not end the loop
            print(f"watchdog: sweep failed: {error}", file=sys.stderr)
        await sleep(interval_s)


def _is_stalled(
    job: JobRecord,
    storage: TranscriptStoragePort,
    *,
    chunk_timeout_s: float,
    at: float,
) -> bool:
    """Only TRANSCRIBING, and only past both clocks.

    The per-chunk timeout is defined over chunks, so it applies where there are
    chunks. Extraction and stitching have their own durations and no boundaries
    to measure against — a hang in either is covered by the two-hour heartbeat
    bound that `worker_is_alive` enforces, which is deliberately looser.
    """
    if job.state is not JobState.TRANSCRIBING:
        return False
    if at - job.updated_at <= chunk_timeout_s:
        return False
    return not storage.heartbeat_is_fresh(
        job.job_id, now_s=at, stale_after_s=chunk_timeout_s
    )


def _record_timeout(
    job: JobRecord,
    storage: TranscriptStoragePort,
    *,
    chunk_timeout_s: float,
    at: float,
) -> None:
    """The epitaph the killed worker could not write for itself.

    Without it the job resumes and re-attempts the chunk with nothing recording
    that it timed out, so a chunk that hangs the engine every time loops forever
    and every run looks like a fresh start.

    INTERRUPTED rather than FAILED: nothing is wrong with the job. Every
    committed chunk is on disk and a resume continues from the first one that is
    not. FAILED would say the work is over when it is not, which is the spec's
    "the job MUST NOT be terminated as a whole".
    """
    stalled = _chunk_in_flight(job.job_id, storage)
    if stalled is not None:
        storage.save_chunk_result(stalled)
    storage.update_job(
        replace(
            job,
            state=JobState.INTERRUPTED,
            updated_at=at,
            error=(
                f"chunk timeout: no progress for {chunk_timeout_s:.0f}s; "
                f"the worker was killed and the job can be resumed"
            ),
        )
    )


def _chunk_in_flight(
    job_id: JobId, storage: TranscriptStoragePort
) -> ChunkResult | None:
    """The first planned chunk with no completed result — what the worker held.

    `None` when there is no plan on disk. That should be impossible, since the
    worker writes the plan before it transitions to TRANSCRIBING, but inventing
    an index to blame would put a fabricated result exactly where resume reads
    its work set.
    """
    plan = storage.load_chunk_plan(job_id)
    if plan is None:
        return None
    results = storage.load_chunk_results(job_id)
    pending = pending_chunks(plan, results)
    if not pending:
        return None

    index = pending[0].index
    previous = next((r for r in results if r.index == index), None)
    return ChunkResult(
        job_id=job_id,
        index=index,
        state=ChunkState.FAILED,
        segments=(),
        # What the supervisor actually knows. The adapter's real engine_id
        # (model size and all) lives inside the process this sweep just killed,
        # and a guess at it would be provenance nobody could trust.
        engine_id=str(previous.engine_id if previous else ""),
        attempts=(previous.attempts if previous else 0) + 1,
        error="chunk timeout: the worker made no progress and was killed",
        finished_at=None,
    )
