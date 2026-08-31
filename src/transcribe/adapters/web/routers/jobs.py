"""Job routes.

Handlers stay thin on purpose: translate HTTP into a use-case call, translate the
result back. Every decision worth arguing about — what an admitted job looks like,
which ids it gets — lives in the use case, where it is testable without a client.
"""

from fastapi import APIRouter

from transcribe.adapters.web.app import WebDependencies
from transcribe.adapters.web.schemas import AdmitJobRequest, AdmitJobResponse
from transcribe.usecases.admit_job import admit_job


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

    return router
