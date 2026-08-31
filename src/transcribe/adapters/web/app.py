"""The FastAPI application, built from injected dependencies.

`create_app(deps)` rather than a module-level `app`: the composition root decides
what storage and which clock, and a test points the same application at a
`tmp_path`. A module-level singleton would read its own configuration at import
time, which is the one thing that makes a web adapter untestable without a real
data directory.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import FastAPI

from transcribe.domain.ids import (
    JobId,
    MediaId,
    generate_job_id,
    generate_media_id,
)
from transcribe.ports.transcript_storage import TranscriptStoragePort

# 16 GiB. Multi-hour video is the normal input here, so this bounds what one
# upload may consume rather than describing a typical file.
DEFAULT_MAX_UPLOAD_BYTES = 16 * 1024**3


@dataclass(frozen=True, slots=True)
class WebDependencies:
    storage: TranscriptStoragePort
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    now: Callable[[], float] = time.time
    new_job_id: Callable[[], JobId] = field(default=generate_job_id)
    new_media_id: Callable[[], MediaId] = field(default=generate_media_id)


def create_app(deps: WebDependencies) -> FastAPI:
    from transcribe.adapters.web.routers.jobs import build_jobs_router

    app = FastAPI(title="transcribe", docs_url=None, redoc_url=None)
    app.include_router(build_jobs_router(deps))
    return app
