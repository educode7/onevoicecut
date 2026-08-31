"""Composition root for the web process.

    PYTHONPATH=src uvicorn transcribe.runtime.app:get_app --factory

This is where configuration is read, real adapters are constructed, and the two
processes meet. Everything below it takes what it needs as an argument, which is
why the whole system can be driven by tests without an environment.
"""

import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from transcribe.adapters.ffmpeg.extractor import require_binaries
from transcribe.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from transcribe.adapters.web.app import WebDependencies, create_app
from transcribe.domain.ids import JobId
from transcribe.runtime.settings import Settings

WORKER_MODULE = "transcribe.runtime.worker"


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


def build_dependencies(settings: Settings) -> WebDependencies:
    return WebDependencies(
        storage=FilesystemTranscriptStorage(settings.data_dir),
        max_upload_bytes=settings.max_upload_bytes,
        start_job=spawn_worker(settings.data_dir),
    )


def build_app(deps: WebDependencies) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # Checked before the first request rather than at first use. ffmpeg
        # missing is discoverable in milliseconds at boot; discovered instead an
        # hour into a job, it costs the operator that hour.
        require_binaries()
        yield

    return create_app(deps, lifespan=lifespan)


def get_app() -> FastAPI:
    """Built on call, not at import.

    `uvicorn transcribe.runtime.app:get_app --factory` reads the environment when
    it starts the server; a module-level app would read it whenever anything
    imported this module, including a test collecting it.
    """
    return build_app(build_dependencies(Settings()))  # type: ignore[call-arg]
