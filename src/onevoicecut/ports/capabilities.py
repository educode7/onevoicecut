"""Adapter capability declaration.

A field belongs here only if a use case must read it to (a) reject or warn about
a job before work starts, or (b) compute a chunk plan.

The "or warn" half is a deliberate, recorded widening of the original rule (see
design.md): rejection and warning are the same structural read — the use case
must consult the adapter before work begins — and they differ only in severity.
"""

from dataclasses import dataclass
from enum import StrEnum


class DiarizationSupport(StrEnum):
    UNSUPPORTED = "unsupported"  # engine can never diarize (e.g. OpenAI Whisper API)
    REQUIRES_SETUP = "requires_setup"  # engine could, this install cannot yet
    AVAILABLE = "available"


class ClassificationSupport(StrEnum):
    """Whether the engine can tell the spoken message from music and singing.

    An independent axis from diarization: an engine may support either, both, or
    neither. Never infer one from the other.
    """

    UNSUPPORTED = "unsupported"  # no VAD/hallucination control; output is all UNCERTAIN
    AVAILABLE = "available"


@dataclass(frozen=True, slots=True)
class TranscriptionCapabilities:
    engine_id: str
    diarization: DiarizationSupport
    # Required, with no default: an adapter that never states whether it can tell
    # speech from music is a gap the admission check cannot reason about.
    non_speech_classification: ClassificationSupport
    max_chunk_bytes: int | None
    max_chunk_duration_s: float | None
