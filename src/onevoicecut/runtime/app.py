"""Composition root for the web process.

    PYTHONPATH=src uvicorn onevoicecut.runtime.app:get_app --factory

This is where configuration is read, real adapters are constructed, and the two
processes meet. Everything below it takes what it needs as an argument, which is
why the whole system can be driven by tests without an environment.
"""

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path

from fastapi import FastAPI

from onevoicecut.adapters.ffmpeg.extractor import require_binaries
from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.adapters.web.auth import build_authenticator, parse_operator_tokens
from onevoicecut.domain.ids import JobId
from onevoicecut.domain.jobs import WORKER_BOUND_STATES, JobRecord, JobState
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.runtime.settings import Settings

# Re-exported, not merely used: liveness moved to `supervisor.py` when the
# watchdog wiring made `app.py` need the sweep and the sweep need the probe —
# a cycle whose honest resolution is that process supervision does not belong in
# a FastAPI factory. Existing callers import these from here and still can.
from onevoicecut.runtime.supervisor import HEARTBEAT_STALE_AFTER_S as HEARTBEAT_STALE_AFTER_S
from onevoicecut.runtime.supervisor import LivenessProbe as LivenessProbe
from onevoicecut.runtime.supervisor import kill_worker as kill_worker
from onevoicecut.runtime.supervisor import process_is_alive as process_is_alive
from onevoicecut.runtime.supervisor import watchdog_supervisor as watchdog_supervisor
from onevoicecut.runtime.supervisor import worker_is_alive as worker_is_alive

WORKER_MODULE = "onevoicecut.runtime.worker"

# Five seconds between sweeps. That is the worst-case delay between an upload
# finishing and its worker starting on an idle machine — noise against a
# three-hour job, and QUEUED is an honest status to show meanwhile.
DRAIN_SWEEP_INTERVAL_S = 5.0

# A minute between watchdog sweeps, against a timeout measured in tens of
# minutes. It bounds only how late a kill lands; sweeping at the drain's cadence
# would re-list every job on the machine twelve times a minute to re-ask a
# question whose answer changes on the scale of a chunk.
WATCHDOG_SWEEP_INTERVAL_S = 60.0

def _popen(argv: list[str]) -> None:
    """Launched and not waited on: the response returns while the job runs."""
    subprocess.Popen(argv)


def spawn_worker(
    data_dir: Path, *, launch: Callable[[list[str]], None] = _popen
) -> Callable[[JobId], None]:
    """Start the job as a separate process, and do not wait for it.

    A process rather than a task: it can be killed when a three-hour job goes
    wrong, the operating system reaps it, and while it lives it is the only writer
    of that job's record — which is what turns the single-writer rule from an
    agreement into something enforced from outside.

    The environment is inherited, which is how the child finds the package when it
    is run from a source tree with `PYTHONPATH=src`.
    """

    def start(job_id: JobId) -> None:
        # List form, and the id is already ULID-validated by the route before it
        # gets here, so nothing in this argv can be anything but a job id.
        launch(
            [
                sys.executable,
                "-m",
                WORKER_MODULE,
                "--job-id",
                job_id,
                "--data-dir",
                str(data_dir),
            ]
        )

    return start


def reconcile_interrupted_jobs(
    storage: TranscriptStoragePort,
    *,
    now: Callable[[], float],
    is_alive: LivenessProbe = process_is_alive,
) -> tuple[JobId, ...]:
    """Mark jobs whose worker died as INTERRUPTED, at startup only.

    This is the one place outside a worker that writes a job record, and it is
    legitimate precisely because there is no worker: a record saying TRANSCRIBING
    with no live process behind it is a lie left by a crash, and leaving it means
    the operator watches a job that will never move again.

    The liveness check is what keeps the single-writer rule intact. Without it
    this would overwrite the record of a worker that outlived its parent — which
    is the normal case, since the workers are separate processes.

    INTERRUPTED rather than FAILED: nothing went wrong with the work. Every
    committed chunk is still on disk, and re-running the job resumes from the
    first one that is not.

    Scoped to every state a worker can own, not just TRANSCRIBING. The narrower
    rule left a job whose worker died during extraction or stitching stuck in
    that state permanently — and under the capacity gate it also held a slot
    permanently, so one crash at the wrong moment could retire the machine's
    only slot. PENDING awaits an upload, QUEUED belongs to the drain, and
    terminal states are done: none of them has a worker to be dead.
    """
    at = now()
    reconciled: list[JobId] = []
    for job in storage.list_jobs():
        if job.state not in WORKER_BOUND_STATES:
            continue
        if worker_is_alive(job, storage, is_alive=is_alive, now=at):
            continue
        storage.update_job(replace(job, state=JobState.INTERRUPTED, updated_at=at))
        reconciled.append(job.job_id)
    return tuple(reconciled)


def drain_once(
    storage: TranscriptStoragePort,
    *,
    max_concurrent_jobs: int,
    launch: Callable[[JobId], None],
    spawned: set[JobId],
    is_alive: LivenessProbe = process_is_alive,
    now: Callable[[], float] = time.time,
) -> tuple[JobId, ...]:
    """One sweep of the gate: start queued work up to the cap, and nothing else.

    This is the only code in the system that starts a job. Upload used to do it
    too, and two spawn decision points meant two concurrent uploads could each
    decide a slot was free and each spawn — over the cap, with no single line to
    blame. One decision point, serialized in the event loop, makes the cap true
    by construction.

    **The active count is derived, never counted.** Every sweep lists the store,
    keeps the worker-bound records, and asks the operating system which of their
    pids still exist. Nothing is persisted between sweeps, so a web process that
    dies mid-sweep leaves nothing to repair, and a worker that dies frees its
    slot the moment the next sweep looks. A counter would be correct right up
    until the first crash — which, for multi-hour jobs, is the case being
    designed for rather than an edge one.

    **The gate writes no record.** QUEUED → EXTRACTING is the worker's own first
    claim. A write here would put the web process on a record at exactly the
    moment its worker is claiming it.

    `spawned` is the caller's memory of issued-but-unclaimed launches, and it is
    emphatically not a worker count: between the launcher call and the worker's
    pid write the record still reads QUEUED, so without it the next sweep would
    start a second process on the same job. It holds only within one web
    lifetime; after a restart the records are the truth, and a job whose worker
    died before claiming is correctly started again.
    """
    records = storage.list_jobs()

    # The same `worker_is_alive` reconcile uses, deliberately. Two derivations
    # of "is this worker running" would eventually disagree, and the failure is
    # silent: the gate holds a slot for a worker reconcile already buried, and
    # the queue behind it stops moving with nothing reporting why.
    at = now()
    active = sum(
        1
        for job in records
        if job.state in WORKER_BOUND_STATES
        and worker_is_alive(job, storage, is_alive=is_alive, now=at)
    )

    # Drop ids the records have moved past, so a long-lived process does not
    # accumulate one entry per job it ever started.
    still_queued = {
        job.job_id for job in records if job.state is JobState.QUEUED
    }
    spawned &= still_queued

    launched: list[JobId] = []
    for job in records:
        if active >= max_concurrent_jobs:
            break
        if job.state is not JobState.QUEUED or job.job_id in spawned:
            continue
        # Re-read before starting anything. The listing is a snapshot, and the
        # cancel that landed while this sweep was walking it is precisely the
        # one worth catching — a worker started here would transcribe a job the
        # operator already stopped.
        if storage.load_job(job.job_id).state is not JobState.QUEUED:
            continue
        launch(job.job_id)
        spawned.add(job.job_id)
        launched.append(job.job_id)
        active += 1

    return tuple(launched)


async def drain_supervisor(
    storage: TranscriptStoragePort,
    *,
    max_concurrent_jobs: int,
    launch: Callable[[JobId], None],
    is_alive: LivenessProbe = process_is_alive,
    interval_s: float = DRAIN_SWEEP_INTERVAL_S,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Sweep forever, and survive a bad sweep.

    Running on a timer rather than on request is what makes the queue drain at
    three in the morning, when a multi-hour job finishes and frees the slot the
    next one has been waiting for. A request-triggered drain would leave that
    slot cold until somebody opened the page.

    A sweep that raises is reported and the loop continues. The alternative is a
    supervisor that dies on one unreadable record while the web process keeps
    accepting uploads and answering 204 — every queued job on the machine
    stranded, and nothing saying so. The queue on disk is the truth; the next
    sweep retries against it.

    `CancelledError` is deliberately not caught: it is shutdown, not a failing
    sweep, and swallowing it would leave a task the event loop cannot stop.
    """
    spawned: set[JobId] = set()
    while True:
        try:
            drain_once(
                storage,
                max_concurrent_jobs=max_concurrent_jobs,
                launch=launch,
                spawned=spawned,
                is_alive=is_alive,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - a bad sweep must not end the loop
            print(f"drain: sweep failed: {error}", file=sys.stderr)
        await sleep(interval_s)


@dataclass(frozen=True, slots=True)
class DrainConfig:
    """Everything the supervisor needs, kept off `WebDependencies` on purpose.

    The web adapter no longer knows how to start work, and that is the point of
    the capacity gate — so the launcher must not sit on the object every route
    handler receives. A handler that can reach a launcher is one refactor away
    from calling it.
    """

    launch: Callable[[JobId], None]
    max_concurrent_jobs: int
    is_alive: LivenessProbe = process_is_alive
    interval_s: float = DRAIN_SWEEP_INTERVAL_S


@dataclass(frozen=True, slots=True)
class WatchdogConfig:
    """Separate from `DrainConfig` because they are separate decisions.

    One is capacity, the other is enforcement, and their intervals differ by
    three orders of magnitude. Folding them together would tie a thirty-minute
    judgement to a five-second cadence.
    """

    chunk_timeout_s: float
    # A minute between sweeps. The timeout is measured in tens of minutes, so
    # this only bounds how late the kill is, and sweeping harder would re-list
    # every job on the machine for a question whose answer changes slowly.
    interval_s: float = WATCHDOG_SWEEP_INTERVAL_S
    kill: Callable[[int], None] = kill_worker
    is_alive: LivenessProbe = process_is_alive


def build_dependencies(settings: Settings) -> WebDependencies:
    # Parsing the token map is the composition root's one authentication act.
    # It refuses an empty or malformed map before anything can serve a request —
    # a server must never come up with authentication disabled or ambiguous.
    authenticate = build_authenticator(parse_operator_tokens(settings.operator_tokens))
    return WebDependencies(
        storage=FilesystemTranscriptStorage(settings.data_dir),
        authenticate=authenticate,
        max_upload_bytes=settings.max_upload_bytes,
    )


def build_app(
    deps: WebDependencies,
    *,
    drain: DrainConfig | None = None,
    watchdog: WatchdogConfig | None = None,
) -> FastAPI:
    """`None` for either builds an app that serves routes and starts nothing.

    That is what route tests want, and it is explicit rather than implied by a
    forgotten argument — the composition root always supplies both, and a test
    asserts that it does.
    """

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # These happen before the first request rather than at first use. A
        # missing ffmpeg discovered an hour into a job, or a stale TRANSCRIBING
        # left by yesterday's crash, are both things the operator should learn
        # about at boot.
        require_binaries()
        # Before the drain, always: reconcile turns records left by dead workers
        # into INTERRUPTED, which is what frees their derived slots. Sweeping
        # first would count processes that no longer exist and make queued work
        # wait behind them.
        reconcile_interrupted_jobs(deps.storage, now=deps.now)

        supervisors: list[asyncio.Task[None]] = []
        if drain is not None:
            supervisors.append(
                asyncio.create_task(
                    drain_supervisor(
                        deps.storage,
                        max_concurrent_jobs=drain.max_concurrent_jobs,
                        launch=drain.launch,
                        is_alive=drain.is_alive,
                        interval_s=drain.interval_s,
                    )
                )
            )
        if watchdog is not None:
            supervisors.append(
                asyncio.create_task(
                    watchdog_supervisor(
                        deps.storage,
                        chunk_timeout_s=watchdog.chunk_timeout_s,
                        interval_s=watchdog.interval_s,
                        kill=watchdog.kill,
                        is_alive=watchdog.is_alive,
                    )
                )
            )

        try:
            yield
        finally:
            # A task outliving its app would keep spawning workers — or killing
            # them — against a data directory this process is finished with.
            for supervisor in supervisors:
                supervisor.cancel()
            for supervisor in supervisors:
                with suppress(asyncio.CancelledError):
                    await supervisor

    return create_app(deps, lifespan=lifespan)


def get_app() -> FastAPI:
    """Built on call, not at import.

    `uvicorn onevoicecut.runtime.app:get_app --factory` reads the environment when
    it starts the server; a module-level app would read it whenever anything
    imported this module, including a test collecting it.
    """
    settings = Settings()  # type: ignore[call-arg]
    return build_app(
        build_dependencies(settings),
        drain=DrainConfig(
            launch=spawn_worker(settings.data_dir),
            max_concurrent_jobs=settings.max_concurrent_jobs,
        ),
        watchdog=WatchdogConfig(chunk_timeout_s=settings.chunk_timeout_s),
    )
