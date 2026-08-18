from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from transcribe.domain.chunking import (
    AudioChunk,
    ChunkPlan,
    ChunkResult,
    ChunkState,
    PlannedChunk,
)
from transcribe.domain.ids import make_job_id

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")


def test_planned_chunk_holds_bounds() -> None:
    chunk = PlannedChunk(index=0, start_s=0.0, end_s=605.0)
    assert chunk.index == 0
    assert chunk.end_s == 605.0


def test_planned_chunk_is_frozen() -> None:
    chunk = PlannedChunk(index=0, start_s=0.0, end_s=605.0)
    with pytest.raises(FrozenInstanceError):
        chunk.index = 1  # type: ignore[misc]


def test_chunk_plan_holds_planned_chunks() -> None:
    plan = ChunkPlan(
        job_id=JOB_ID,
        stride_s=600.0,
        overlap_s=5.0,
        chunks=(PlannedChunk(index=0, start_s=0.0, end_s=605.0),),
    )
    assert len(plan.chunks) == 1


def test_chunk_plan_is_frozen() -> None:
    plan = ChunkPlan(job_id=JOB_ID, stride_s=600.0, overlap_s=5.0, chunks=())
    with pytest.raises(FrozenInstanceError):
        plan.stride_s = 300.0  # type: ignore[misc]


def test_audio_chunk_holds_fields() -> None:
    chunk = AudioChunk(
        job_id=JOB_ID,
        index=0,
        path=Path("jobs/x/chunks/0000.flac"),
        start_s=0.0,
        end_s=605.0,
        size_bytes=4096,
    )
    assert chunk.size_bytes == 4096


def test_audio_chunk_is_frozen() -> None:
    chunk = AudioChunk(
        job_id=JOB_ID,
        index=0,
        path=Path("jobs/x/chunks/0000.flac"),
        start_s=0.0,
        end_s=605.0,
        size_bytes=4096,
    )
    with pytest.raises(FrozenInstanceError):
        chunk.size_bytes = 0  # type: ignore[misc]


def test_chunk_state_has_expected_members() -> None:
    assert {m.value for m in ChunkState} == {"pending", "running", "done", "failed"}


def test_chunk_result_holds_fields() -> None:
    result = ChunkResult(
        job_id=JOB_ID,
        index=0,
        state=ChunkState.DONE,
        segments=(),
        engine_id="fake",
        attempts=1,
        error=None,
        finished_at=1234.5,
    )
    assert result.state is ChunkState.DONE


def test_chunk_result_is_frozen() -> None:
    result = ChunkResult(
        job_id=JOB_ID,
        index=0,
        state=ChunkState.DONE,
        segments=(),
        engine_id="fake",
        attempts=1,
        error=None,
        finished_at=1234.5,
    )
    with pytest.raises(FrozenInstanceError):
        result.attempts = 2  # type: ignore[misc]
