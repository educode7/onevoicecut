"""Chunk planning entities, per-chunk transcription results, and resume's derivation over them."""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from onevoicecut.shared.domain.ids import JobId
from onevoicecut.systems.pipeline.transcripts.domain.transcript import TranscriptSegment


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


def pending_chunks(
    plan: ChunkPlan, results: tuple[ChunkResult, ...]
) -> tuple[PlannedChunk, ...]:
    """The planned chunks with no completed result, in plan order.

    Order comes from the plan rather than from the results, because results are
    written in whatever order chunks finished — a retry can commit chunk 7 after
    chunk 11 — while the work must still proceed forward through the sermon.

    A result whose index is not in the plan is ignored rather than trusted. It is
    a leftover from an earlier plan, and letting it discharge current work would
    mark a chunk done that this plan never ran.
    """
    completed = {
        result.index for result in results if result.state is ChunkState.DONE
    }
    return tuple(chunk for chunk in plan.chunks if chunk.index not in completed)
