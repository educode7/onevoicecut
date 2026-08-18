"""Chunk planning and per-chunk transcription result entities."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from transcribe.domain.ids import JobId
from transcribe.domain.transcript import TranscriptSegment


@dataclass(frozen=True, slots=True)
class PlannedChunk:
    index: int
    start_s: float
    end_s: float  # includes the overlap tail


@dataclass(frozen=True, slots=True)
class ChunkPlan:
    job_id: JobId
    stride_s: float
    overlap_s: float
    chunks: tuple[PlannedChunk, ...]


@dataclass(frozen=True, slots=True)
class AudioChunk:
    job_id: JobId
    index: int
    path: Path
    start_s: float
    end_s: float
    size_bytes: int


class ChunkState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ChunkResult:
    job_id: JobId
    index: int
    state: ChunkState
    segments: tuple[TranscriptSegment, ...]
    engine_id: str
    attempts: int
    error: str | None
    finished_at: float | None
