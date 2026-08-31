"""Job routes.

Handlers stay thin on purpose: translate HTTP into a use-case call, translate the
result back. Every decision worth arguing about — what an admitted job looks like,
which ids it gets — lives in the use case, where it is testable without a client.
"""

from urllib.parse import unquote

from fastapi import APIRouter, HTTPException, Request, Response

from transcribe.adapters.web.app import WebDependencies
from transcribe.adapters.web.schemas import AdmitJobRequest, AdmitJobResponse
from transcribe.domain.errors import JobNotFound, UploadTooLarge
from transcribe.domain.ids import JobId
from transcribe.usecases.admit_job import admit_job

# The client's filename travels as metadata, never in the URL — a path parameter
# would invite treating it as one.
FILENAME_HEADER = "x-filename"


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
    def admit(body: AdmitJobRequest) -> AdmitJobResponse:
        """Returns before anything expensive happens.

        Admission records a decision. The upload that follows and the hours of
        transcription after it are separate, precisely so neither sits inside an
        HTTP request.
        """
        job = admit_job(
            engine=body.engine,
            speaker_mode=body.speaker_mode,
            storage=deps.storage,
            now=deps.now,
            new_job_id=deps.new_job_id,
            new_media_id=deps.new_media_id,
        )
        return AdmitJobResponse(job_id=job.job_id, state=job.state)

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
        try:
            job = deps.storage.load_job(JobId(job_id))
        except JobNotFound as error:
            raise HTTPException(status_code=404, detail=str(error)) from error

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

        deps.storage.save_media(job.job_id, media)
        return Response(status_code=204)

    return router
