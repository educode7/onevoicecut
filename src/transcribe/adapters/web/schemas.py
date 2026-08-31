"""Request and response shapes for the HTTP boundary.

Separate from the domain entities on purpose. These describe what a browser may
send and what it gets back; `JobRecord` describes what the system knows. Letting
one be the other would make every domain field a public API and every API change
a domain change.

Validation is Pydantic's, and it is doing real work here: `EngineChoice` and
`SpeakerMode` are `StrEnum`, so an unknown value is a 422 at the boundary rather
than a `ValueError` somewhere inside a use case.
"""

from pydantic import BaseModel, ConfigDict

from transcribe.domain.jobs import EngineChoice, JobProgress, JobState, SpeakerMode


class AdmitJobRequest(BaseModel):
    # Rejects unknown keys instead of ignoring them: a client sending
    # `speakerMode` should be told, not silently given the default.
    model_config = ConfigDict(extra="forbid")

    # No default. Engine choice is content-dependent — private material goes to
    # the local engine — so an omitted engine is a question, not a field to fill
    # in with a guess.
    engine: EngineChoice
    speaker_mode: SpeakerMode = SpeakerMode.SINGLE


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
