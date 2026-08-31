"""Composition root for the web process.

    PYTHONPATH=src uvicorn transcribe.runtime.app:get_app --factory

This is where configuration is read, real adapters are constructed, and the two
processes meet. Everything below it takes what it needs as an argument, which is
why the whole system can be driven by tests without an environment.
"""

import os
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI

from transcribe.adapters.ffmpeg.extractor import require_binaries
from transcribe.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from transcribe.adapters.web.app import WebDependencies, create_app
from transcribe.domain.ids import JobId
from transcribe.domain.jobs import JobState
from transcribe.ports.transcript_storage import TranscriptStoragePort
from transcribe.runtime.settings import Settings

WORKER_MODULE = "transcribe.runtime.worker"

LivenessProbe = Callable[[int], bool]


def process_is_alive(pid: int) -> bool:
    """Signal 0 is a probe, not a signal, on both platforms this runs on.

    Verified on CPython 3.12 / Windows rather than assumed: `os.kill(pid, 0)`
    returns for a live process, raises `OSError` for a dead one, and — unlike
    every other signal value there — does not terminate anything. `os.kill(pid, 9)`
    on the same platform kills with exit code 9, so the special case is real and
    worth naming.

    A recycled pid reads as alive. On a single-operator machine, within the window
    between a crash and the next startup, that is a risk worth taking against the
    alternative of a stronger liveness check nobody maintains.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


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
    """
    reconciled: list[JobId] = []
    for job in storage.list_jobs():
        if job.state is not JobState.TRANSCRIBING:
            continue
        if job.worker_pid is not None and is_alive(job.worker_pid):
            continue
        storage.update_job(
            replace(job, state=JobState.INTERRUPTED, updated_at=now())
        )
        reconciled.append(job.job_id)
    return tuple(reconciled)


def build_dependencies(settings: Settings) -> WebDependencies:
    return WebDependencies(
        storage=FilesystemTranscriptStorage(settings.data_dir),
        max_upload_bytes=settings.max_upload_bytes,
        start_job=spawn_worker(settings.data_dir),
    )


def build_app(deps: WebDependencies) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Both checks happen before the first request rather than at first use.
        # A missing ffmpeg discovered an hour into a job, or a stale TRANSCRIBING
        # left by yesterday's crash, are both things the operator should learn
        # about at boot.
        require_binaries()
        reconcile_interrupted_jobs(deps.storage, now=deps.now)
        yield

    return create_app(deps, lifespan=lifespan)


def get_app() -> FastAPI:
    """Built on call, not at import.

    `uvicorn transcribe.runtime.app:get_app --factory` reads the environment when
    it starts the server; a module-level app would read it whenever anything
    imported this module, including a test collecting it.
    """
    return build_app(build_dependencies(Settings()))  # type: ignore[call-arg]
