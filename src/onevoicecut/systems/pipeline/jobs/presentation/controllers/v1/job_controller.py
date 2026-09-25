"""The jobs module's controller: HTTP on one side, commands on the other.

The routes own what is genuinely HTTP — the path, the headers, the status code
they return. This owns the translation in between: a request body into a
command, a result back into a response schema, and exactly two refusals
converted on the way:

- a malformed job id answers 404 here. The path parameter is this layer's own
  question, and validating before dispatch is what keeps a caller-supplied
  string from ever reaching a store that would build a path out of it;
- `JobNotOwned` becomes a 403 whose detail never names the owner.

Nothing else is caught. `JobNotFound`, `UploadTooLarge`, `UnsupportedContainer`
and every state refusal are left to rise to the composition root's single table
(`main.py`), so a new domain error gets its status from one row rather than
from a hunt for every controller that might already be translating it.

Ownership itself is never decided here: the handlers call `require_owner`, and
this layer only turns that decision into an HTTP answer (AUTH-10).
"""

from collections.abc import AsyncIterator

from fastapi import HTTPException

from onevoicecut.shared.application.principal import Principal
from onevoicecut.shared.domain.errors import JobNotOwned
from onevoicecut.shared.domain.ids import InvalidIdError, JobId, make_job_id
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
from onevoicecut.systems.pipeline.jobs.application.use_cases.queries.get_job import (
    GetJobHandler,
    GetJobQuery,
)
from onevoicecut.systems.pipeline.jobs.application.use_cases.queries.list_jobs import (
    ListJobsHandler,
    ListJobsQuery,
)
from onevoicecut.systems.pipeline.jobs.presentation.schemas.v1.job_schemas import (
    AdmitJobRequest,
    AdmitJobResponse,
    CancelJobResponse,
    JobListItem,
    JobListResponse,
    JobStatusResponse,
    ProgressResponse,
)

# `JobNotOwned`'s own message names the job and the operator — it is raised
# once, in `require_owner`, where non-HTTP callers want exactly that. The
# HTTP refusal must not: under the shared board a 403 on a foreign id is
# already public knowledge of the id, and the owner is not part of the
# answer. Only this edge answers generically, and `main.py` imports this
# constant for the rows it still maps itself, so the two spellings cannot
# drift apart.
OWNER_REFUSAL_DETAIL = "not the owner of this job"


def validated_job_id(raw: str) -> JobId:
    """Check the id at the door, against the pattern the domain owns.

    The filesystem adapter validates too, and until now that was the only check —
    which made the guarantee a property of one storage backend rather than of the
    boundary. Worse, it held partly by accident of statement order: a hostile id
    died at `load_job` because no such job existed, so a handler that built the
    writer first would have handed it a path outside the data directory and
    nothing would have complained.

    `%2e%2e` is the form that matters. `../..` is normalised away by the client
    and the router before any handler sees it; the percent-encoded version
    survives routing and arrives as `..` in the path parameter.

    A malformed id answers 404, the same as a well-formed unknown one, so the
    store never reveals which ids exist.
    """
    try:
        return make_job_id(raw)
    except InvalidIdError as error:
        raise HTTPException(status_code=404, detail="no such job") from error


class JobsController:
    """One instance per app: it holds the five handlers and nothing else.

    Constructed by `jobs_module_api`, never by a route — presentation
    constructs nothing that decides behaviour (AB-09), and a controller built
    per request would rebuild the handler graph on every poll of the board.
    """

    def __init__(
        self,
        *,
        admit_handler: AdmitJobHandler,
        ingest_handler: IngestMediaHandler,
        cancel_handler: CancelJobHandler,
        get_job_handler: GetJobHandler,
        list_jobs_handler: ListJobsHandler,
    ) -> None:
        self._admit = admit_handler
        self._ingest = ingest_handler
        self._cancel = cancel_handler
        self._get_job = get_job_handler
        self._list_jobs = list_jobs_handler

    def admit(self, principal: Principal, body: AdmitJobRequest) -> AdmitJobResponse:
        """Schema in, command out, response schema back.

        Admission returns before anything expensive happens: it records a
        decision, and the upload and the hours of transcription after it are
        separate, precisely so neither sits inside an HTTP request.
        """
        admission = self._admit.handle(
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

    def listing(self, principal: Principal, *, mine: bool = False) -> JobListResponse:
        """The shared board, projected into rows.

        `mine` is the handler's branch — it narrows the records before they
        arrive here — so this layer only maps what came back. Items stay
        record-derived: no progress, no per-job scan, one directory listing
        per poll.
        """
        jobs = self._list_jobs.handle(
            ListJobsQuery(principal=principal, mine=mine)
        )
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

    def status(self, job_id: str) -> JobStatusResponse:
        """Read-only by construction, which is what makes it safe to poll.

        The principal is deliberately absent: reads are shared, so identity is
        no part of the answer — the route still declares it, because a gate in
        the route table is what the generated 401 check derives from.

        Elapsed time is measured from admission rather than from the moment
        transcription began, which the record does not carry. The difference is
        the upload, so the rate comes out slightly low and the ETA slightly long.
        That is the direction to be wrong in.
        """
        snapshot = self._get_job.handle(
            GetJobQuery(job_id=validated_job_id(job_id))
        )
        return JobStatusResponse(
            job_id=snapshot.job.job_id,
            state=snapshot.job.state,
            engine=snapshot.job.engine,
            speaker_mode=snapshot.job.speaker_mode,
            error=snapshot.job.error,
            progress=(
                None if snapshot.progress is None else ProgressResponse.of(snapshot.progress)
            ),
            owner=snapshot.job.owner,
        )

    def cancel(self, job_id: str, principal: Principal) -> CancelJobResponse:
        """Records the request and answers; it does not wait for the worker.

        Waiting would hold the request open for the length of one chunk — ten
        minutes of sermon — to report something the next status poll gives for
        free. The state coming back is therefore the record's current one,
        which for a running job is still the running state.
        """
        try:
            cancelled = self._cancel.handle(
                CancelJobCommand(
                    job_id=validated_job_id(job_id),
                    principal=principal,
                )
            )
        except JobNotOwned as error:
            raise HTTPException(status_code=403, detail=OWNER_REFUSAL_DETAIL) from error
        return CancelJobResponse(job_id=cancelled.job_id, state=cancelled.state)

    async def upload_media(
        self,
        job_id: str,
        principal: Principal,
        *,
        filename: str,
        content_length: str | None,
        stream: AsyncIterator[bytes],
    ) -> None:
        """Hand the decoded request to the ingest handler and report nothing.

        The three values arriving here are everything the request carried that
        is not an identity: the path id, the percent-decoded filename (metadata
        only, never a path component) and the body as a stream. Whether those
        bytes are welcome is the handler's question.
        """
        try:
            await self._ingest.handle(
                IngestMediaCommand(
                    principal=principal,
                    job_id=validated_job_id(job_id),
                    filename=filename,
                    content_length=content_length,
                    stream=stream,
                )
            )
        except JobNotOwned as error:
            raise HTTPException(status_code=403, detail=OWNER_REFUSAL_DETAIL) from error
