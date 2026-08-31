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

from transcribe.domain.jobs import EngineChoice, JobState, SpeakerMode


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
