"""Progress is computed by looking at what exists, never by counting as you go.

A counter incremented in memory diverges from the truth the moment a process
dies — which is the exact case this system is built for. Deriving it from the
persisted results against the persisted plan means progress after a crash is
already correct, with no recovery code to write and none to get wrong.

The second rule is about honesty rather than correctness: there is no ETA until
at least one chunk has finished. A number invented from zero samples is worse
than no number, because the operator will plan their evening around it.
"""

import pytest

from onevoicecut.domain.chunking import ChunkPlan, ChunkResult, ChunkState, PlannedChunk
from onevoicecut.domain.ids import make_job_id
from onevoicecut.domain.jobs import JobProgress, derive_progress
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
STARTED_AT = 1000.0


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
    segments = (
        (
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
        else ()
    )
    return ChunkResult(
        job_id=JOB_ID,
        index=index,
        state=state,
        segments=segments,
        engine_id="fake-asr",
        attempts=1,
        error=None if state is ChunkState.DONE else "boom",
        finished_at=STARTED_AT + 60.0,
    )


def done_results(count: int) -> tuple[ChunkResult, ...]:
    return tuple(a_result(i) for i in range(count))


def progress_of(
    plan: ChunkPlan, results: tuple[ChunkResult, ...], *, now: float
) -> JobProgress:
    """Narrows away the `None` that only the unplanned-job case can produce."""
    progress = derive_progress(plan, results, started_at=STARTED_AT, now=now)
    assert progress is not None
    return progress


def test_progress_counts_what_is_on_disk_against_the_plan() -> None:
    progress = progress_of(a_plan(87), done_results(10), now=STARTED_AT + 600.0)

    assert progress.chunks_total == 87
    assert progress.chunks_done == 10


def test_a_job_with_no_results_yet_reports_zero_of_its_total() -> None:
    progress = progress_of(a_plan(87), (), now=STARTED_AT + 5.0)

    assert (progress.chunks_done, progress.chunks_total) == (0, 87)


def test_there_is_no_eta_until_a_chunk_has_actually_finished() -> None:
    """Zero samples is not a small sample. An invented estimate is worse than
    none, because the operator plans their evening around it."""
    progress = progress_of(a_plan(87), (), now=STARTED_AT + 300.0)

    assert progress.eta_s is None


def test_the_eta_extrapolates_from_the_chunks_that_did_finish() -> None:
    """10 of 87 in 600s, so 77 remaining at 60s each."""
    progress = progress_of(a_plan(87), done_results(10), now=STARTED_AT + 600.0)

    assert progress.eta_s == pytest.approx(77 * 60.0)


def test_a_finished_job_has_an_eta_of_zero_not_none() -> None:
    """`None` means "cannot say yet". Nothing left to do is a different answer."""
    progress = progress_of(a_plan(3), done_results(3), now=STARTED_AT + 180.0)

    assert progress.eta_s == pytest.approx(0.0)


def test_failed_chunks_are_counted_apart_from_completed_ones() -> None:
    """Both are finished being attempted; only one produced transcript. Folding
    them together would report a job as complete when a chunk of the sermon is
    missing."""
    results = (a_result(0), a_result(1, ChunkState.FAILED), a_result(2))

    progress = progress_of(a_plan(3), results, now=STARTED_AT + 180.0)

    assert progress.chunks_done == 2
    assert progress.chunks_failed == 1
    assert progress.chunks_remaining == 0


def test_a_failed_chunk_still_counts_as_time_spent() -> None:
    """It consumed the machine for as long as a successful one, sometimes longer
    after retries. Excluding it from the rate would flatter the estimate."""
    results = (a_result(0), a_result(1, ChunkState.FAILED))

    progress = progress_of(a_plan(10), results, now=STARTED_AT + 200.0)

    assert progress.eta_s == pytest.approx(8 * 100.0)


def test_an_unfinished_chunk_is_not_progress() -> None:
    """A RUNNING result is what a process that died mid-chunk leaves behind."""
    results = (a_result(0), a_result(1, ChunkState.RUNNING))

    progress = progress_of(a_plan(3), results, now=STARTED_AT + 100.0)

    assert progress.chunks_done == 1
    assert progress.chunks_remaining == 2


def test_elapsed_time_is_reported() -> None:
    progress = progress_of(a_plan(3), (a_result(0),), now=STARTED_AT + 45.0)

    assert progress.elapsed_s == pytest.approx(45.0)


def test_progress_survives_a_crash_without_any_recovery_step() -> None:
    """The whole reason it is derived. The same results read by a fresh process
    give the same answer — there is no in-memory counter to rebuild."""
    plan = a_plan(87)
    results = done_results(30)

    first = progress_of(plan, results, now=STARTED_AT + 900.0)
    after_restart = progress_of(plan, results, now=STARTED_AT + 900.0)

    assert first == after_restart
    assert first.chunks_done == 30


def test_an_unplanned_job_has_no_chunk_progress() -> None:
    """Before planning there is no denominator. Reporting 0 of 0 would read as a
    finished job."""
    assert derive_progress(None, (), started_at=STARTED_AT, now=STARTED_AT) is None
