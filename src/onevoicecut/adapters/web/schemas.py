"""Request and response shapes for the HTTP boundary.

Separate from the domain entities on purpose. These describe what a browser may
send and what it gets back; `JobRecord` describes what the system knows. Letting
one be the other would make every domain field a public API and every API change
a domain change.

Validation is Pydantic's, and it is doing real work here: `EngineChoice` and
`SpeakerMode` are `StrEnum`, so an unknown value is a 422 at the boundary rather
than a `ValueError` somewhere inside a use case.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from onevoicecut.domain.jobs import EngineChoice, JobProgress, JobState, SpeakerMode


class AdmitJobRequest(BaseModel):
    # Rejects unknown keys instead of ignoring them: a client sending
    # `speakerMode` should be told, not silently given the default.
    model_config = ConfigDict(extra="forbid")

    # No default. Engine choice is content-dependent — private material goes to
    # the local engine — so an omitted engine is a question, not a field to fill
    # in with a guess.
    engine: EngineChoice
    speaker_mode: SpeakerMode = SpeakerMode.SINGLE

    @model_validator(mode="before")
    @classmethod
    def _discard_client_supplied_identity(cls, data: Any) -> Any:
        """A client-supplied operator identity has no effect (OWN-07).

        Removed before validation rather than rejected: `extra="forbid"` stays
        in force for ordinary typos, but a 422 for `operator` would refuse a
        caller the admission their token entitles them to — and honoring it
        would let one operator mint jobs as another.
        """
        if isinstance(data, dict):
            return {key: value for key, value in data.items() if key != "operator"}
        return data


class AdmitJobResponse(BaseModel):
    job_id: str
    state: JobState


class ProgressResponse(BaseModel):
    """Chunk-level, because "running" is the same answer at minute two and minute
    one hundred and eighty."""

    chunks_total: int
    chunks_done: int
    chunks_failed: int
    chunks_remaining: int
    elapsed_s: float
    # `null` until a chunk has finished. An estimate from zero samples is a
    # fabrication, and the operator plans their evening around it.
    eta_s: float | None

    @classmethod
    def of(cls, progress: JobProgress) -> "ProgressResponse":
        return cls(
            chunks_total=progress.chunks_total,
            chunks_done=progress.chunks_done,
            chunks_failed=progress.chunks_failed,
            chunks_remaining=progress.chunks_remaining,
            elapsed_s=progress.elapsed_s,
            eta_s=progress.eta_s,
        )


class JobStatusResponse(BaseModel):
    job_id: str
    state: JobState
    # Chosen at admission and acted on hours later by another process. Without
    # them there is no way to tell a cloud job from a local one while it runs.
    engine: EngineChoice
    speaker_mode: SpeakerMode
    error: str | None
    # `null` before the job is planned: there is no denominator yet, and zero of
    # zero renders as a finished job.
    progress: ProgressResponse | None
    # Additive: every pre-change field keeps its name and meaning (VIS-06).
    # `null` for a record written before owners existed — visible to all,
    # mutable by nobody. A token value never appears here, only a name (AUTH-09).
    owner: str | None


class CancelJobResponse(BaseModel):
    """One status for every cancellation branch, so the client stays flat.

    `state` is what the record carries at response time, which for a running job
    is still the running state — the request has been recorded, the worker has
    not stopped yet. Reporting CANCELLED here would tell the operator the
    machine is free while it is still working, and the shared board would
    disagree on its next poll.
    """

    job_id: str
    state: JobState


class JobListItem(BaseModel):
    """One row of the shared board, record-derived only.

    No progress, because progress costs a per-job plan/results scan and a poll
    of the board must cost one directory listing. How far along a job is remains
    the per-job status read.
    """

    job_id: str
    state: JobState
    # `null` for legacy records (VIS-04): the listing hides nothing, and an
    # ownerless job is everybody's to see and nobody's to change.
    owner: str | None
    engine: EngineChoice
    speaker_mode: SpeakerMode
    created_at: float
    updated_at: float


class JobListResponse(BaseModel):
    """A wrapper object, not a bare array (D10): pagination, totals, whatever
    comes next, join the wrapper additively instead of changing every client's
    shape."""

    jobs: list[JobListItem]
