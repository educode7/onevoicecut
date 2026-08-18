"""Structured transcript entities — the canonical internal representation.

The delivered `.txt` artifact is NEVER the source of truth; it is derived
from this structured form.
"""

from dataclasses import dataclass

from transcribe.domain.ids import JobId


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str
    speaker: str | None
    confidence: float | None


@dataclass(frozen=True, slots=True)
class Transcript:
    job_id: JobId
    segments: tuple[TranscriptSegment, ...]
    engine_id: str
    diarized: bool
    language: str = "es"
