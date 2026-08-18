"""Adapter capability declaration.

A field belongs here only if a use case must read it to (a) reject a job
before work starts, or (b) compute a chunk plan.
"""

from dataclasses import dataclass
from enum import StrEnum


class DiarizationSupport(StrEnum):
    UNSUPPORTED = "unsupported"  # engine can never diarize (e.g. OpenAI Whisper API)
    REQUIRES_SETUP = "requires_setup"  # engine could, this install cannot yet
    AVAILABLE = "available"


@dataclass(frozen=True, slots=True)
class TranscriptionCapabilities:
    engine_id: str
    diarization: DiarizationSupport
    max_chunk_bytes: int | None
    max_chunk_duration_s: float | None
