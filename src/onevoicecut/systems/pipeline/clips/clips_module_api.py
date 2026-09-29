"""The clips module's API: the one place that decides how its three HTTP
operations are wired.

A module API exists because the alternative spreads the same handler
constructions across every caller — `main.py` today, a second composition root
tomorrow — and two callers wiring the export handler with different id
generators or different registries is two different clip-export rules. One
function, fed the root's dependencies, decides it once.

This file is deliberately *not* under `presentation/`. It constructs a handler
(AB-09), so it must be free to reach the adapters a composition root hands it,
and presentation is the one layer that may not: a controller that could build
its own store would be a controller that decides where the data lives.

The three operations carry `/api/jobs` on their own router, so `main.py` adds
no prefix when it registers them — see `routes/v1/clip_routes.py` for why the
prefix cannot be supplied from outside.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from onevoicecut.systems.pipeline.clips.application.use_cases.commands.request_clip_export import (
    RequestClipExportHandler,
)
from onevoicecut.systems.pipeline.clips.presentation.controllers.v1.clip_controller import (
    ClipsController,
)
from onevoicecut.systems.pipeline.clips.presentation.routes.v1.clip_routes import (
    build_router,
)

if TYPE_CHECKING:
    from onevoicecut.main import WebDependencies


def build_clips_router(deps: WebDependencies) -> APIRouter:
    """The three clip operations, wired against the root's dependencies.

    Every value comes from `deps` rather than from a default, which is what
    makes the clip-id generator and the two registries the composition root's
    decision — a test supplies its own `deps` and gets a router that behaves
    accordingly. The two stores are the same object handed to both: one storage
    facade satisfies `JobStore` and `ClipStore` structurally, which is exactly
    what the narrow interfaces were split for.
    """
    controller = ClipsController(
        request_clip_handler=RequestClipExportHandler(
            storage=deps.storage,
            new_clip_id=deps.new_clip_id,
            script_targets=deps.script_targets,
            render_profiles=deps.render_profiles,
        ),
        job_store=deps.storage,
        clip_store=deps.storage,
    )
    return build_router(controller=controller, authenticate=deps.authenticate)
