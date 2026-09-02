"""Job routes.

Handlers stay thin on purpose: translate HTTP into a use-case call, translate the
result back. Every decision worth arguing about — what an admitted job looks like,
which ids it gets — lives in the use case, where it is testable without a client.
"""

from dataclasses import replace
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request, Response

from onevoicecut.adapters.web.app import WebDependencies
from onevoicecut.adapters.web.auth import InvalidCredential
from onevoicecut.adapters.web.schemas import (
    AdmitJobRequest,
    AdmitJobResponse,
    CancelJobResponse,
    JobListItem,
    JobListResponse,
    JobStatusResponse,
    ProgressResponse,
)
from onevoicecut.domain.errors import (
    DiarizationUnsupported,
    JobNotFound,
    JobNotOwned,
    UnsupportedContainer,
    UploadTooLarge,
)
from onevoicecut.domain.ids import InvalidIdError, JobId, OperatorId, make_job_id
from onevoicecut.domain.jobs import JobRecord, derive_progress
from onevoicecut.domain.media import SourceMedia
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.media_source import MediaSourcePort
from onevoicecut.usecases.admit_job import admit_job
from onevoicecut.usecases.cancel_job import cancel_job
from onevoicecut.usecases.ownership import require_owner

# The client's filename travels as metadata, never in the URL — a path parameter
# would invite treating it as one.
FILENAME_HEADER = "x-filename"


def _authorized(request: Request, deps: WebDependencies) -> OperatorId:
    """Resolve the caller's identity, or refuse with the one 401 shape.

    First statement of every handler, so no route can serve a request it never
    authenticated. Every credential failure — missing header, malformed header,
    unknown token — becomes the SAME response: one status, one body, one header.
    Distinguishing the causes would tell a caller which operators exist.
    """
    try:
        return deps.authenticate(request.headers.get("authorization"))
    except InvalidCredential as error:
        raise HTTPException(
            status_code=401,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        ) from error


def _owned(job: JobRecord, operator: OperatorId) -> None:
    """The one 403 translation, shared by every mutating route.

    The use case raises `JobNotOwned`; the adapter maps it. The detail is
    generic on purpose — under the shared listing every job's existence is
    already public, so a refusal on a foreign id reveals nothing new and must
    not name the owner.
    """
    try:
        require_owner(job, operator)
    except JobNotOwned as error:
        raise HTTPException(
            status_code=403, detail="not the owner of this job"
        ) from error


def _load(job_id: str, deps: WebDependencies) -> JobRecord:
    """Validate then load, in that order, on every route that names a job."""
    try:
        return deps.storage.load_job(_validated_job_id(job_id))
    except JobNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


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


def _refuse_if_declared_too_large(request: Request, max_bytes: int) -> None:
    """The cheap half of the size limit, and the only half that costs nothing.

    `Content-Length` is a claim, so this cannot be the whole defence — the writer
    keeps counting in case the claim was false. But when a client honestly
    declares sixteen gigabytes, refusing here is the difference between an instant
    answer and an hour of transfer nobody wanted.

    An absent header means chunked transfer encoding, which is what a browser
    sends for a large file: there is simply nothing to check. An unparseable one
    is treated the same way — it tells us nothing, and it is not evidence of being
    small.
    """
    declared = request.headers.get("content-length")
    if declared is None:
        return
    try:
        length = int(declared)
    except ValueError:
        return
    if length > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"declared {length} bytes, limit is {max_bytes}",
        )


def _verified_media(
    media: SourceMedia,
    *,
    extractor: AudioExtractorPort,
    writer: MediaSourcePort,
) -> SourceMedia:
    """Decide what the file is by looking inside it, and record the answer.

    An extension is a claim by whoever named the file, and it is wrong in both
    directions: it does not stop a text file called `sermon.mp4`, and it would
    reject a real recording someone named `sermon`. So the bytes are probed and
    the probe is believed.

    The rejection worth naming is the second one. A container with no audio
    stream looks entirely fine — it extracts cleanly to a silent track and
    transcribes to an empty sermon, and nothing in the output says why. Catching
    it here means the operator hears about it while they are still standing at
    the upload form.

    A refused file is discarded rather than kept. The retention rule protects the
    operator's uploaded video; this was never accepted as one.
    """
    try:
        probe = extractor.probe(media)
    except UnsupportedContainer as error:
        writer.discard(media)
        raise HTTPException(status_code=415, detail=str(error)) from error

    if not probe.has_audio:
        writer.discard(media)
        raise HTTPException(
            status_code=415,
            detail=f"{probe.container} has no audio stream to transcribe",
        )

    return replace(media, container=probe.container)


def _client_filename(raw: str) -> str:
    """Percent-decoded, because HTTP header values are ASCII and the source
    language is not.

    `predicación del domingo.mp4` is the ordinary case here, not an edge case, and
    it cannot travel in a header as written. Decoding is a no-op for a plain ASCII
    name, so a client that sends one unencoded still works.
    """
    return unquote(raw)


def build_jobs_router(deps: WebDependencies) -> APIRouter:
    """A closure over the dependencies rather than FastAPI's `Depends`.

    The wiring is decided once by the composition root and never varies per
    request, so a closure says exactly that — and keeps the handlers free of
    framework-specific injection that would have to be unpicked to test them.
    """
    router = APIRouter(prefix="/api/jobs", tags=["jobs"])

    @router.post("", status_code=201, response_model=AdmitJobResponse)
    def admit(body: AdmitJobRequest, request: Request) -> AdmitJobResponse:
        """Returns before anything expensive happens.

        Admission records a decision. The upload that follows and the hours of
        transcription after it are separate, precisely so neither sits inside an
        HTTP request.
        """
        operator = _authorized(request, deps)
        try:
            job = admit_job(
                engine=body.engine,
                speaker_mode=body.speaker_mode,
                operator=operator,
                storage=deps.storage,
                now=deps.now,
                new_job_id=deps.new_job_id,
                new_media_id=deps.new_media_id,
                capabilities=deps.capabilities,
            )
        except DiarizationUnsupported as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return AdmitJobResponse(job_id=job.job_id, state=job.state)

    @router.get("", response_model=JobListResponse)
    def listing(request: Request, mine: bool = False) -> JobListResponse:
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
        operator = _authorized(request, deps)
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
    def status(job_id: str, request: Request) -> JobStatusResponse:
        """Read-only by construction, which is what makes it safe to poll.

        The worker is the sole writer of the job record. This reads the record,
        reads the plan, counts the results and computes — there is nothing to race
        against because nothing is written.

        Elapsed time is measured from admission rather than from the moment
        transcription began, which the record does not carry. The difference is
        the upload, so the rate comes out slightly low and the ETA slightly long.
        That is the direction to be wrong in.
        """
        _authorized(request, deps)
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
    def cancel(job_id: str, request: Request) -> CancelJobResponse:
        """Records the request and answers. It does not wait for the worker.

        Waiting would hold the request open for the length of one chunk — ten
        minutes of sermon — to report something the next status poll gives for
        free. The state coming back is therefore the record's current one, which
        for a running job is still the running state.

        Ownership is checked here as well as inside the use case. The use case
        must refuse a stranger on its own — it is callable without a route — and
        the handler must produce the 403 before the branch is taken, so the
        duplication is two different jobs, not one done twice.
        """
        operator = _authorized(request, deps)
        job = _load(job_id, deps)
        _owned(job, operator)

        cancelled = cancel_job(
            job.job_id, operator=operator, storage=deps.storage, now=deps.now
        )
        return CancelJobResponse(job_id=cancelled.job_id, state=cancelled.state)

    @router.put("/{job_id}/media", status_code=204)
    async def upload_media(job_id: str, request: Request) -> Response:
        """Raw body straight to disk. No multipart, no `UploadFile`.

        `request.stream()` hands over chunks as they arrive off the socket, so the
        writer never holds the file — which is the only way a multi-hour upload
        works at all. FastAPI's `UploadFile` would spool the whole body first, and
        a test asserts that neither it nor `File`/`Form` appears anywhere in this
        adapter.

        The filename arrives percent-encoded in a header and is recorded as
        metadata. It is never consulted when deciding where anything goes: storage
        decided that before this handler ran.
        """
        operator = _authorized(request, deps)
        job = _load(job_id, deps)
        # Ownership is decided before the writer exists: a non-owner's request
        # never opens a partial file, never accepts a byte.
        _owned(job, operator)

        _refuse_if_declared_too_large(request, deps.max_upload_bytes)

        writer = deps.media_source_for(deps.storage, job.job_id)
        try:
            media = await writer.store(
                job.media_id,
                _client_filename(request.headers.get(FILENAME_HEADER, "")),
                request.stream(),
                deps.max_upload_bytes,
            )
        except UploadTooLarge as error:
            raise HTTPException(status_code=413, detail=str(error)) from error

        verified = _verified_media(
            media, extractor=deps.extractor_for(deps.storage, job.job_id), writer=writer
        )
        # Saved before the worker is started, never after: the worker's first act
        # is to read this record, and a race here would have it looking for a
        # media file the web process had not finished describing.
        deps.storage.save_media(job.job_id, verified)
        deps.start_job(job.job_id)
        return Response(status_code=204)

    return router
