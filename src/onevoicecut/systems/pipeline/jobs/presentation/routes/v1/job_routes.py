"""The five jobs operations, over relative paths.

The router carries `/api/v1/jobs`, declared here rather than supplied by
`main.py` when it registers the router — see the note beside `APIRouter`
below. The prefix and its version segment live in that one argument rather
than in five decorators, so moving the surface to another version is a
single-line change.

Routes stay thin on purpose: translate HTTP into a call, hand it to the
controller, translate the result back. What still raises `HTTPException` here
is presentation — the percent-encoded filename header, the raw body as a
stream — where the HTTP answer is the whole point rather than a translation of
a domain refusal. A `DomainError` leaving a handler is not caught at all: the
composition root maps it once (`main.py`), so every route answers one table
alike.
"""

from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Request, Response

from onevoicecut.shared.application.principal import Authenticator, Principal
from onevoicecut.shared.presentation.security import make_current_principal
from onevoicecut.systems.pipeline.jobs.presentation.controllers.v1.job_controller import (
    JobsController,
)
from onevoicecut.systems.pipeline.jobs.presentation.schemas.v1.job_schemas import (
    AdmitJobRequest,
    AdmitJobResponse,
    CancelJobResponse,
    JobListResponse,
    JobStatusResponse,
)

# The client's filename travels as metadata, never in the URL — a path parameter
# would invite treating it as one.
FILENAME_HEADER = "x-filename"


def _client_filename(raw: str) -> str:
    """Percent-decoded, because HTTP header values are ASCII and the source
    language is not.

    `predicación del domingo.mp4` is the ordinary case here, not an edge case,
    and it cannot travel in a header as written. Decoding is a no-op for a plain
    ASCII name, so a client that sends one unencoded still works.
    """
    return unquote(raw)


def build_router(
    *, controller: JobsController, authenticate: Authenticator
) -> APIRouter:
    """A closure over the controller, with authentication as the one exception.

    The wiring is decided once by the composition root and never varies per
    request, so a closure says exactly that — and keeps the routes free of
    framework-specific injection that would have to be unpicked to test them.
    The controller is passed in rather than built here: presentation constructs
    nothing that decides behaviour, so `jobs_module_api` decides what admission,
    ingest, cancellation and the two reads are wired with.

    The principal is deliberately NOT in the closure: it is a `Depends`
    dependency declared on every route, because a gate in the route table is
    what the generated 401 check derives from — a route written without
    `principal: CurrentPrincipal` fails that check the day it appears, where a
    forgotten `_authorized(...)` first statement only failed review.
    """
    # The prefix lives here rather than being passed to `include_router` by the
    # caller. FastAPI 0.141.1 routes included with an outer prefix are wrapped in
    # an `_IncludedRouter` whose nested `APIRoute.path` keeps only the prefix this
    # router declared — so the generated gates (AUTH-06's 401, OWN-05's 403), which
    # read `route.path` to build their request URLs, would call `/{job_id}/cancel`
    # and get a 404 from a route that is actually served at
    # `/api/v1/jobs/{job_id}/cancel`.
    # Declaring it here is what makes the route table describe the paths the server serves.
    router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])
    # Written here rather than returned by the factory: only a literal
    # Annotated expression binds as a type annotation, so the factory builds
    # the resolver and this line binds it to this router's dependencies.
    CurrentPrincipal = Annotated[
        Principal, Depends(make_current_principal(authenticate))
    ]

    @router.post("", status_code=201, response_model=AdmitJobResponse)
    def admit(principal: CurrentPrincipal, body: AdmitJobRequest) -> AdmitJobResponse:
        """201 with the new job's id, returned before anything expensive
        happens — the admission itself is the controller's."""
        return controller.admit(principal, body)

    @router.get("", response_model=JobListResponse)
    def listing(principal: CurrentPrincipal, mine: bool = False) -> JobListResponse:
        """The shared board: every job, attributed, hidden from nobody.

        One ministry team cuts one church's sermons, so "is Sunday's sermon
        done?" is collaboration, not leakage — read access to every job is the
        point of a shared server, while mutation stays owner-gated on the
        routes that change things.

        `mine` narrows the view against the token identity and nothing else.
        No route accepts an operator identity as a parameter — a
        client-supplied one has nowhere to arrive, so a legacy record (owner
        None) can never match anybody.
        """
        return controller.listing(principal, mine=mine)

    @router.get("/{job_id}", response_model=JobStatusResponse)
    def status(job_id: str, principal: CurrentPrincipal) -> JobStatusResponse:
        """Read-only by construction, which is what makes it safe to poll.

        `principal` is declared but not forwarded: reads are shared, so
        identity is no part of the answer. It is still declared, because the
        generated 401 check derives from the route table and a route that
        forgets it fails the default run the day it appears.
        """
        return controller.status(job_id)

    @router.post("/{job_id}/cancel", response_model=CancelJobResponse)
    def cancel(job_id: str, principal: CurrentPrincipal) -> CancelJobResponse:
        """Records the request and answers. It does not wait for the worker —
        the state coming back is the record's current one, which for a running
        job is still the running state."""
        return controller.cancel(job_id, principal)

    @router.put("/{job_id}/media", status_code=204)
    async def upload_media(
        job_id: str, principal: CurrentPrincipal, request: Request
    ) -> Response:
        """Raw body straight to disk. No multipart, no `UploadFile`.

        The route owns the two things that are genuinely HTTP: the path id and
        the percent-encoded filename header. Whether those bytes are welcome is
        the handler's question, so this dispatches and hands back the status
        code.

        The filename arrives percent-encoded in a header and is recorded as
        metadata. It is never consulted when deciding where anything goes:
        storage decided that before this command was built.
        """
        await controller.upload_media(
            job_id,
            principal,
            filename=_client_filename(request.headers.get(FILENAME_HEADER, "")),
            content_length=request.headers.get("content-length"),
            stream=request.stream(),
        )
        return Response(status_code=204)

    return router
