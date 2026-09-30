"""The three clip operations, over paths that already carry `/api/v1/jobs`.

The prefix is declared here rather than passed by `main.py` when it registers
the router. FastAPI 0.141.1 wraps a router included under an outer prefix in an
`_IncludedRouter` whose nested `APIRoute.path` keeps only the prefix this router
declared — and the generated auth and ownership gates (AUTH-06's 401, OWN-05's
403) build their request URLs from `route.path`, so a prefix applied from
outside would make every generated gate call a URL the server never serves.
Declaring it here is what makes the route table describe the paths actually
served. This is the same reason `job_routes.py` states it, and the two halves of
one prefix must not disagree.

Routes stay thin on purpose: translate HTTP into a call, hand it to the
controller, translate the result back. What still raises `HTTPException` here
is presentation — where the HTTP answer is the whole point rather than a
translation of a domain refusal — and even that has moved down into the
controller, which is where the clip operations' own refusals live. A
`DomainError` leaving a use case is not caught at all: the composition root maps
it once (`main.py`), so every route answers one table alike.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from onevoicecut.shared.application.principal import Authenticator, Principal
from onevoicecut.shared.presentation.security import make_current_principal
from onevoicecut.systems.pipeline.clips.presentation.controllers.v1.clip_controller import (
    ClipsController,
)
from onevoicecut.systems.pipeline.clips.presentation.schemas.v1.clip_schemas import (
    ClipExportItem,
    ClipExportListResponse,
    ClipExportRequest,
    ClipExportResponse,
)


def build_router(
    *, controller: ClipsController, authenticate: Authenticator
) -> APIRouter:
    """A closure over the controller, with authentication as the one exception.

    The wiring is decided once by the composition root and never varies per
    request, so a closure says exactly that — and keeps the routes free of
    framework-specific injection that would have to be unpicked to test them.
    The controller is passed in rather than built here: presentation constructs
    nothing that decides behaviour, so `clips_module_api` decides what the
    export handler and the two reads are wired with.

    The principal is deliberately NOT in the closure: it is a `Depends`
    dependency declared on every route, because a gate in the route table is
    what the generated 401 check derives from — a route written without
    `principal: CurrentPrincipal` fails that check the day it appears, where a
    forgotten `_authorized(...)` first statement only failed review.
    """
    # Carried here rather than supplied by `include_router`: see the note above
    # and the identical one in the jobs module's `job_routes.py`.
    router = APIRouter(prefix="/api/v1/jobs", tags=["clips"])
    # Written here rather than returned by the factory: only a literal
    # Annotated expression binds as a type annotation, so the factory builds
    # the resolver and this line binds it to this router's dependencies.
    CurrentPrincipal = Annotated[
        Principal, Depends(make_current_principal(authenticate))
    ]

    @router.post(
        "/{job_id}/clips", status_code=202, response_model=ClipExportResponse
    )
    def request_clip(
        job_id: str, body: ClipExportRequest, principal: CurrentPrincipal
    ) -> ClipExportResponse:
        """Writes `PENDING` exports and returns. It does not render.

        `13b-iv` delivers no spawn — see `tasks.md`'s note on `13b.29`. A
        `PENDING` export with no render worker is precisely the queued state,
        the same way a `QUEUED` job with no worker is one.
        """
        return controller.request_clip(principal, job_id, body)

    @router.get(
        "/{job_id}/clips/{clip_id}", response_model=ClipExportListResponse
    )
    def clip_status(
        job_id: str, clip_id: str, principal: CurrentPrincipal
    ) -> ClipExportListResponse:
        """Every profile's export for one clip.

        `principal` is declared but not forwarded: reads are shared, so
        identity is no part of the answer. It is still declared, because the
        generated 401 check derives from the route table and a route that
        forgets it fails the default run the day it appears.
        """
        return controller.clip_status(job_id, clip_id)

    @router.get(
        "/{job_id}/clips/{clip_id}/{profile}", response_model=ClipExportItem
    )
    def clip_profile_status(
        job_id: str, clip_id: str, profile: str, principal: CurrentPrincipal
    ) -> ClipExportItem:
        """Exactly one profile's export, and a distinct 404 when the profile
        is the thing that does not exist."""
        return controller.clip_profile_status(job_id, clip_id, profile)

    return router
