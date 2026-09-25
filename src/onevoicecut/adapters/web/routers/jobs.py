"""Job routes.

Routes stay thin on purpose: translate HTTP into a command, dispatch it, translate
the result back. Every decision worth arguing about — what an admitted job looks
like, which ids it gets, whether an upload is welcome — lives on the command
handlers, where it is testable without a client. The three write handlers are
built by the composition root (`main.py`) and handed in, so this module
constructs nothing.

A `DomainError` leaving storage or a handler is not caught here: the composition
root maps it once (`main.py`), so every route answers one table alike. What still
raises `HTTPException` is presentation — a malformed id, a clip state this route
itself decides — where the HTTP answer is the whole point rather than a
translation of a domain refusal.
"""

from typing import Annotated
from urllib.parse import unquote

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from onevoicecut.adapters.web.app import WebDependencies
from onevoicecut.adapters.web.schemas import (
    AdmitJobRequest,
    AdmitJobResponse,
    CancelJobResponse,
    ClipExportItem,
    ClipExportListResponse,
    ClipExportRequest,
    ClipExportResponse,
    JobListItem,
    JobListResponse,
    JobStatusResponse,
    ProgressResponse,
)
from onevoicecut.shared.application.principal import Principal
from onevoicecut.shared.domain.ids import ClipId, InvalidIdError, JobId, OperatorId, make_clip_id, make_job_id
from onevoicecut.shared.presentation.security import make_current_principal
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.admit_job import (
    AdmitJobCommand,
    AdmitJobHandler,
)
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.cancel_job import (
    CancelJobCommand,
    CancelJobHandler,
)
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.ingest_media import (
    IngestMediaCommand,
    IngestMediaHandler,
)
from onevoicecut.systems.pipeline.jobs.domain.jobs import JobRecord, JobState, derive_progress
from onevoicecut.usecases.request_clip_export import request_clip_export
from onevoicecut.systems.pipeline.jobs.domain.ownership import require_owner

# The client's filename travels as metadata, never in the URL — a path parameter
# would invite treating it as one.
FILENAME_HEADER = "x-filename"


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

    `JobNotFound` from the store rises to the composition root's table (404);
    the malformed-id refusal below stays here, because it is this route's own
    question about the path parameter rather than a domain refusal.
    """
    return deps.storage.load_job(_validated_job_id(job_id))


def _validated_job_id(raw: str) -> JobId:
    """Check the id at the door, against the pattern the domain owns.

    The filesystem adapter validates too, and until now that was the only check —
    which made the guarantee a property of one storage backend rather than of the
    route. Worse, it held partly by accident of statement order: a hostile id died
    at `load_job` because no such job existed, so a handler that built the writer
    first would have handed it a path outside the data directory and nothing would
    have complained.

    `%2e%2e` is the form that matters. `../..` is normalised away by the client and
    the router before any handler sees it; the percent-encoded version survives
    routing and arrives as `..` in the path parameter.

    A malformed id answers 404, the same as a well-formed unknown one, so the store
    never reveals which ids exist.
    """
    try:
        return make_job_id(raw)
    except InvalidIdError as error:
        raise HTTPException(status_code=404, detail="no such job") from error


def _validated_clip_id(raw: str) -> ClipId:
    """The second id every clip route names, checked the same way `job_id` is.

    Malformed and unknown answer identically (404), so no route reveals which
    clip ids exist -- the same reasoning `_validated_job_id` states in full.
    """
    try:
        return make_clip_id(raw)
    except InvalidIdError as error:
        raise HTTPException(status_code=404, detail="no such clip") from error


def _client_filename(raw: str) -> str:
    """Percent-decoded, because HTTP header values are ASCII and the source
    language is not.

    `predicación del domingo.mp4` is the ordinary case here, not an edge case, and
    it cannot travel in a header as written. Decoding is a no-op for a plain ASCII
    name, so a client that sends one unencoded still works.
    """
    return unquote(raw)


def build_jobs_router(
    deps: WebDependencies,
    *,
    admit_handler: AdmitJobHandler,
    ingest_handler: IngestMediaHandler,
    cancel_handler: CancelJobHandler,
) -> APIRouter:
    """A closure over the dependencies, with authentication as the one exception.

    The wiring is decided once by the composition root and never varies per
    request, so a closure says exactly that — and keeps the routes free of
    framework-specific injection that would have to be unpicked to test them.
    The three write handlers are passed in rather than built here: presentation
    constructs nothing, so the root decides what admission, ingest and
    cancellation are wired with and this module only dispatches to them.
    The principal is deliberately NOT in the closure: it is a `Depends`
    dependency declared on every route, because a gate in the route table is
    what the generated 401 check derives from — a route written without
    `principal: CurrentPrincipal` fails that check the day it appears, where a
    forgotten `_authorized(...)` first statement only failed review.
    """
    router = APIRouter(prefix="/api/jobs", tags=["jobs"])
    # Written here rather than returned by the factory: only a literal
    # Annotated expression binds as a type annotation, so the factory builds
    # the resolver and this line binds it to this router's dependencies.
    CurrentPrincipal = Annotated[
        Principal, Depends(make_current_principal(deps.authenticate))
    ]

    @router.post("", status_code=201, response_model=AdmitJobResponse)
    def admit(principal: CurrentPrincipal, body: AdmitJobRequest) -> AdmitJobResponse:
        """Returns before anything expensive happens.

        Admission records a decision. The upload that follows and the hours of
        transcription after it are separate, precisely so neither sits inside an
        HTTP request.
        """
        admission = admit_handler.handle(
            AdmitJobCommand(
                principal=principal,
                engine=body.engine,
                speaker_mode=body.speaker_mode,
            )
        )
        return AdmitJobResponse(
            job_id=admission.job.job_id,
            state=admission.job.state,
            warnings=admission.warnings,
        )

    @router.get("", response_model=JobListResponse)
    def listing(principal: CurrentPrincipal, mine: bool = False) -> JobListResponse:
        """The shared board: every job, attributed, hidden from nobody.

        One ministry team cuts one church's sermons, so "is Sunday's sermon
        done?" is collaboration, not leakage — read access to every job is the
        point of a shared server, while mutation stays owner-gated on the
        routes that change things.

        The listing rides the same unscoped `list_jobs()` startup reconcile
        uses, which is what makes "nothing hidden" structural rather than a
        promise this handler keeps: the route cannot scope by caller what the
        store never scoped. Items are record-derived only — one directory
        listing per poll, no per-job plan/results scans; progress remains the
        per-job status read.

        `mine` narrows the view, never the store's: a boolean resolved against
        the token identity and nothing else. No route accepts an operator
        identity as a parameter — a client-supplied one has nowhere to arrive,
        so a legacy record (owner None) can never match anybody.
        """
        operator = principal.identity
        jobs = deps.storage.list_jobs()
        if mine:
            jobs = tuple(job for job in jobs if job.owner == operator)
        return JobListResponse(
            jobs=[
                JobListItem(
                    job_id=job.job_id,
                    state=job.state,
                    owner=job.owner,
                    engine=job.engine,
                    speaker_mode=job.speaker_mode,
                    created_at=job.created_at,
                    updated_at=job.updated_at,
                )
                for job in jobs
            ]
        )

    @router.get("/{job_id}", response_model=JobStatusResponse)
    def status(job_id: str, principal: CurrentPrincipal) -> JobStatusResponse:
        """Read-only by construction, which is what makes it safe to poll.

        The worker is the sole writer of the job record. This reads the record,
        reads the plan, counts the results and computes — there is nothing to race
        against because nothing is written.

        Elapsed time is measured from admission rather than from the moment
        transcription began, which the record does not carry. The difference is
        the upload, so the rate comes out slightly low and the ETA slightly long.
        That is the direction to be wrong in.
        """
        job = _load(job_id, deps)
        progress = derive_progress(
            deps.storage.load_chunk_plan(job.job_id),
            deps.storage.load_chunk_results(job.job_id),
            started_at=job.created_at,
            now=deps.now(),
        )
        return JobStatusResponse(
            job_id=job.job_id,
            state=job.state,
            engine=job.engine,
            speaker_mode=job.speaker_mode,
            error=job.error,
            progress=None if progress is None else ProgressResponse.of(progress),
            owner=job.owner,
        )

    @router.post("/{job_id}/cancel", response_model=CancelJobResponse)
    def cancel(job_id: str, principal: CurrentPrincipal) -> CancelJobResponse:
        """Records the request and answers. It does not wait for the worker.

        Waiting would hold the request open for the length of one chunk — ten
        minutes of sermon — to report something the next status poll gives for
        free. The state coming back is therefore the record's current one, which
        for a running job is still the running state.

        Ownership is checked inside the handler rather than here: the domain rule
        runs before the state branch is taken, so a stranger learns nothing about
        which branch their job would have fallen into — and the refusal exists for
        a caller with no route at all, which is the same reason the rule itself
        lives in `domain/ownership.py`.
        """
        cancelled = cancel_handler.handle(
            CancelJobCommand(
                job_id=_validated_job_id(job_id),
                principal=principal,
            )
        )
        return CancelJobResponse(job_id=cancelled.job_id, state=cancelled.state)

    @router.put("/{job_id}/media", status_code=204)
    async def upload_media(
        job_id: str, principal: CurrentPrincipal, request: Request
    ) -> Response:
        """Raw body straight to disk. No multipart, no `UploadFile`.

        The route owns the two things that are genuinely HTTP: the path id and
        the percent-encoded filename header. Everything that decides whether
        those bytes are welcome lives on the ingest handler, so this dispatches
        and hands back the status code.

        The filename arrives percent-encoded in a header and is recorded as
        metadata. It is never consulted when deciding where anything goes: storage
        decided that before this command was built.
        """
        await ingest_handler.handle(
            IngestMediaCommand(
                principal=principal,
                job_id=_validated_job_id(job_id),
                filename=_client_filename(request.headers.get(FILENAME_HEADER, "")),
                content_length=request.headers.get("content-length"),
                stream=request.stream(),
            )
        )
        return Response(status_code=204)

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

        Read-only, like `status`: nothing here has a worker to race against.
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
