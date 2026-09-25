"""Clip routes: the three operations that remain in the web adapter.

The five jobs operations moved to the module that owns them
(`systems/pipeline/jobs/presentation/`); these stay until the clips module
does the same, so both halves of one HTTP prefix exist as sibling routers that
each carry `/api/jobs` and register with `main.py` adding nothing.

Both carry the prefix themselves on purpose: FastAPI 0.141.1 wraps a router
included under an outer prefix in an `_IncludedRouter` whose nested
`APIRoute.path` omits that prefix, and the generated auth and ownership gates
build their request URLs from `route.path` — so a prefix applied from outside
would make every generated gate call a URL the server never serves.

Routes stay thin on purpose: translate HTTP into a call, hand it to the use
case, translate the result back. Every decision worth arguing about — what a
clip export looks like, whether an upload is welcome — lives where it can be
tested without a client. A `DomainError` leaving storage or a use case is not
caught here: the composition root maps it once (`main.py`), so every route
answers one table alike. What still raises `HTTPException` is presentation —
a malformed id, a clip state this route itself decides — where the HTTP answer
is the whole point rather than a translation of a domain refusal.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from onevoicecut.adapters.web.app import WebDependencies
from onevoicecut.adapters.web.schemas import (
    ClipExportItem,
    ClipExportListResponse,
    ClipExportRequest,
    ClipExportResponse,
)
from onevoicecut.shared.application.principal import Principal
from onevoicecut.shared.domain.ids import ClipId, InvalidIdError, OperatorId, make_clip_id
from onevoicecut.shared.presentation.security import make_current_principal
from onevoicecut.systems.pipeline.jobs.domain.jobs import JobRecord, JobState
from onevoicecut.systems.pipeline.jobs.presentation.controllers.v1.job_controller import (
    validated_job_id,
)
from onevoicecut.usecases.request_clip_export import request_clip_export
from onevoicecut.systems.pipeline.jobs.domain.ownership import require_owner


def _owned(job: JobRecord, operator: OperatorId) -> None:
    """The ownership gate every mutating route calls before it changes anything.

    The use case raises `JobNotOwned`; nothing catches it here — the composition
    root maps it to a 403 whose detail never names the owner (see `main.py`).
    The check stays at the route as well as inside the use case because the
    handler must refuse before the branch is taken: a stranger must not open a
    partial file or learn the state of somebody else's job.
    """
    require_owner(job, operator)


def _load(job_id: str, deps: WebDependencies) -> JobRecord:
    """Validate then load, in that order, on every route that names a job.

    The validation is the one the jobs controller owns, shared rather than
    restated: one function, one rule about what a job id looks like, and one
    answer (404) for a malformed id and an unknown one.

    `JobNotFound` from the store rises to the composition root's table (404).
    """
    return deps.storage.load_job(validated_job_id(job_id))


def _validated_clip_id(raw: str) -> ClipId:
    """The second id every clip route names, checked the same way `job_id` is.

    Malformed and unknown answer identically (404), so no route reveals which
    clip ids exist -- the same reasoning `validated_job_id` states in full.
    """
    try:
        return make_clip_id(raw)
    except InvalidIdError as error:
        raise HTTPException(status_code=404, detail="no such clip") from error


def build_clip_router(deps: WebDependencies) -> APIRouter:
    """A closure over the dependencies, with authentication as the one exception.

    The wiring is decided once by the composition root and never varies per
    request, so a closure says exactly that — and keeps the routes free of
    framework-specific injection that would have to be unpicked to test them.
    The principal is deliberately NOT in the closure: it is a `Depends`
    dependency declared on every route, because a gate in the route table is
    what the generated 401 check derives from — a route written without
    `principal: CurrentPrincipal` fails that check the day it appears, where a
    forgotten `_authorized(...)` first statement only failed review.
    """
    # Carried here rather than supplied by `include_router`: see the same note
    # in the jobs module's `job_routes.py` — the generated ownership and auth
    # gates read `route.path` and must see the prefix the server serves.
    router = APIRouter(prefix="/api/jobs", tags=["clips"])
    # Written here rather than returned by the factory: only a literal
    # Annotated expression binds as a type annotation, so the factory builds
    # the resolver and this line binds it to this router's dependencies.
    CurrentPrincipal = Annotated[
        Principal, Depends(make_current_principal(deps.authenticate))
    ]

    @router.post(
        "/{job_id}/clips", status_code=202, response_model=ClipExportResponse
    )
    def request_clip(
        job_id: str, body: ClipExportRequest, principal: CurrentPrincipal
    ) -> ClipExportResponse:
        """Writes `PENDING` exports and returns. It does not render.

        `13b-iv` delivers no spawn -- see `tasks.md`'s note on `13b.29`. A
        `PENDING` export with no render worker is precisely the queued state,
        the same way a `QUEUED` job with no worker is one.
        """
        operator = principal.identity
        job = _load(job_id, deps)
        _owned(job, operator)

        if job.state is not JobState.COMPLETED:
            raise HTTPException(
                status_code=409,
                detail=f"job is {job.state}, which has no clip candidates to "
                f"export from",
            )

        clip_id, profiles = request_clip_export(
            job.job_id,
            body.candidate_index,
            body.targets,
            storage=deps.storage,
            new_clip_id=deps.new_clip_id,
            script_targets=deps.script_targets,
            render_profiles=deps.render_profiles,
        )

        return ClipExportResponse(
            clip_id=clip_id, profiles=tuple(profile.name for profile in profiles)
        )

    @router.get(
        "/{job_id}/clips/{clip_id}", response_model=ClipExportListResponse
    )
    def clip_status(
        job_id: str, clip_id: str, principal: CurrentPrincipal
    ) -> ClipExportListResponse:
        """Every profile's export, never just one -- a single-object response
        would have to pick a profile to report and be wrong about the rest.

        Read-only, like a job's status: nothing here has a worker to race
        against.
        """
        job = _load(job_id, deps)
        exports = deps.storage.load_clip_exports(
            job.job_id, _validated_clip_id(clip_id)
        )
        if not exports:
            raise HTTPException(status_code=404, detail="no such clip")
        return ClipExportListResponse(
            exports=[ClipExportItem.of(export) for export in exports]
        )

    @router.get(
        "/{job_id}/clips/{clip_id}/{profile}", response_model=ClipExportItem
    )
    def clip_profile_status(
        job_id: str, clip_id: str, profile: str, principal: CurrentPrincipal
    ) -> ClipExportItem:
        """Resolves to exactly one export. A clip id alone never identifies a
        single rendered file, so an unknown profile on a known clip is a
        distinct refusal from an unknown clip -- both 404, for different
        reasons a caller can tell apart by the message.
        """
        job = _load(job_id, deps)
        exports = deps.storage.load_clip_exports(
            job.job_id, _validated_clip_id(clip_id)
        )
        if not exports:
            raise HTTPException(status_code=404, detail="no such clip")
        for export in exports:
            if export.profile == profile:
                return ClipExportItem.of(export)
        raise HTTPException(
            status_code=404, detail=f"clip has no {profile!r} export"
        )

    return router
