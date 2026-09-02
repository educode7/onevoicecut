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

import os
import signal
import time
from collections.abc import Callable
from dataclasses import replace

from onevoicecut.domain.chunking import ChunkResult, ChunkState
from onevoicecut.domain.ids import JobId
from onevoicecut.domain.jobs import JobRecord, JobState
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.runtime.app import LivenessProbe, process_is_alive
from onevoicecut.usecases.resume_job import pending_chunks

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
