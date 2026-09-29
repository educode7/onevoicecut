"""The jobs module's API: the one place that decides how its five HTTP
operations are wired.

A module API exists because the alternative spreads the same five handler
constructions across every caller — `main.py` today, a second composition root
tomorrow — and two callers wiring admission with different clocks or id
generators is two different admission rules. One function, fed the root's
dependencies, decides it once.

This file is deliberately *not* under `presentation/`. It constructs handlers
(AB-09), so it must be free to reach the adapters a composition root hands it,
and presentation is the one layer that may not: a controller that could build
its own store would be a controller that decides where the data lives.

`main.py` imports this and registers what comes back under `/api/jobs`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.admit_job import (
    AdmitJobHandler,
)
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.cancel_job import (
    CancelJobHandler,
)
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.ingest_media import (
    IngestMediaHandler,
)
from onevoicecut.systems.pipeline.jobs.application.use_cases.queries.get_job import (
    GetJobHandler,
)
from onevoicecut.systems.pipeline.jobs.application.use_cases.queries.list_jobs import (
    ListJobsHandler,
)
from onevoicecut.systems.pipeline.jobs.presentation.controllers.v1.job_controller import (
    JobsController,
)
from onevoicecut.systems.pipeline.jobs.presentation.routes.v1.job_routes import (
    build_router,
)

if TYPE_CHECKING:
    # Type-only, so module wiring never reaches the composition root at import
    # time — the dependency runs the other way, `main` calling this.
    from onevoicecut.main import WebDependencies


def build_jobs_router(deps: WebDependencies) -> APIRouter:
    """The five jobs operations, wired against the root's dependencies.

    Every value comes from `deps` rather than from defaults, which is what
    makes admission's clock, its id generators and its capability guard the
    composition root's decision — a test supplies its own `deps` and gets a
    router that behaves accordingly.
    """
    controller = JobsController(
        admit_handler=AdmitJobHandler(
            storage=deps.storage,
            capabilities=deps.capabilities,
            now=deps.now,
            new_job_id=deps.new_job_id,
            new_media_id=deps.new_media_id,
        ),
        ingest_handler=IngestMediaHandler(
            storage=deps.storage,
            max_upload_bytes=deps.max_upload_bytes,
            media_source_for=deps.media_source_for,
            extractor_for=deps.extractor_for,
            now=deps.now,
        ),
        cancel_handler=CancelJobHandler(storage=deps.storage, now=deps.now),
        get_job_handler=GetJobHandler(storage=deps.storage, now=deps.now),
        list_jobs_handler=ListJobsHandler(storage=deps.storage),
    )
    return build_router(controller=controller, authenticate=deps.authenticate)
