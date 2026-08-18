"""Job record entity and its enums."""

from dataclasses import dataclass
from enum import StrEnum

from transcribe.domain.ids import JobId, MediaId


class SpeakerMode(StrEnum):
    SINGLE = "single"
    MULTI = "multi"


class EngineChoice(StrEnum):
    """No default: required on every create-job request."""

    LOCAL = "local"
    CLOUD = "cloud"


class JobState(StrEnum):
    PENDING = "pending"
    EXTRACTING = "extracting"
    PLANNED = "planned"
    TRANSCRIBING = "transcribing"
    STITCHING = "stitching"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: JobId
    media_id: MediaId
    state: JobState
    speaker_mode: SpeakerMode
    engine: EngineChoice
    created_at: float
    updated_at: float
    worker_pid: int | None
    error: str | None
