"""Which chunks a restarted job still owes, and the fact that resume is not a mode.

Running the job again *is* the resume. There is no second code path that only
executes after a crash — which matters because that path would run rarely, be
tested rarely, and be exactly where a bug survives. The loop skips what is already
done, so the first run and the fifth take the same route.

What resume must never do is redo transcription, which is the expensive half.
Re-extracting the audio is cheap by comparison and is done unconditionally: a
three-hour sermon normalizes in minutes, against hours of ASR.
"""

from transcribe.domain.chunking import ChunkPlan, ChunkResult, ChunkState, PlannedChunk
from transcribe.domain.ids import make_job_id
from transcribe.domain.transcript import SegmentKind, TranscriptSegment
from transcribe.usecases.resume_job import pending_chunks

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")


def a_plan(count: int) -> ChunkPlan:
    return ChunkPlan(
        job_id=JOB_ID,
        stride_s=600.0,
        overlap_s=5.0,
        chunks=tuple(
            PlannedChunk(index=i, start_s=i * 600.0, end_s=(i + 1) * 600.0 + 5.0)
            for i in range(count)
        ),
    )


def a_result(index: int, state: ChunkState = ChunkState.DONE) -> ChunkResult:
    return ChunkResult(
        job_id=JOB_ID,
        index=index,
        state=state,
        segments=(
            TranscriptSegment(
                start_s=0.0,
                end_s=1.0,
                text="hola",
                speaker=None,
                confidence=0.9,
                kind=SegmentKind.SPEECH,
            ),
        )
        if state is ChunkState.DONE
        else (),
        engine_id="fake-asr",
        attempts=1,
        error=None,
        finished_at=1.0,
    )


def indices(chunks: tuple[PlannedChunk, ...]) -> list[int]:
    return [chunk.index for chunk in chunks]


def test_a_fresh_job_owes_every_chunk() -> None:
    assert indices(pending_chunks(a_plan(3), ())) == [0, 1, 2]


def test_a_finished_job_owes_nothing() -> None:
    results = tuple(a_result(i) for i in range(3))

    assert pending_chunks(a_plan(3), results) == ()


def test_only_the_chunks_without_a_completed_result_are_owed() -> None:
    """The property resume exists for: 83 completed chunks of a three-hour sermon
    are not transcribed a second time."""
    results = tuple(a_result(i) for i in range(83))

    assert indices(pending_chunks(a_plan(87), results)) == [83, 84, 85, 86]


def test_a_failed_chunk_is_owed_again() -> None:
    """Retrying it is the point of resuming after a transient provider outage."""
    results = (a_result(0), a_result(1, ChunkState.FAILED), a_result(2))

    assert indices(pending_chunks(a_plan(3), results)) == [1]


def test_a_chunk_left_running_is_owed_again() -> None:
    """What a process killed mid-chunk leaves behind. Trusting it would drop the
    chunk silently, and a hole reads as continuous text once stitched."""
    results = (a_result(0), a_result(1, ChunkState.RUNNING))

    assert indices(pending_chunks(a_plan(3), results)) == [1, 2]


def test_a_pending_result_is_owed_again() -> None:
    results = (a_result(0, ChunkState.PENDING),)

    assert indices(pending_chunks(a_plan(2), results)) == [0, 1]


def test_the_owed_chunks_keep_plan_order() -> None:
    """Results come back in whatever order they were written; the work does not."""
    results = (a_result(5), a_result(1), a_result(3))

    assert indices(pending_chunks(a_plan(7), results)) == [0, 2, 4, 6]


def test_a_result_for_a_chunk_that_is_not_planned_is_ignored() -> None:
    """A leftover from an earlier plan must not silently mark current work done."""
    results = (a_result(0), a_result(99))

    assert indices(pending_chunks(a_plan(2), results)) == [1]


def test_the_returned_chunks_are_the_planned_ones_not_copies() -> None:
    """The loop slices from these, so their boundaries must be the plan's — the
    stitcher derives its contested window from the same numbers."""
    plan = a_plan(3)

    owed = pending_chunks(plan, (a_result(0),))

    assert owed == (plan.chunks[1], plan.chunks[2])
