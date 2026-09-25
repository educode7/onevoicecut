"""Reading one job: the record, its derived progress, and nothing written.

`GET /api/jobs/{id}` is the poll an operator leaves open for the length of a
three-hour sermon, so the query behind it owes two properties that the HTTP
tests cannot see from outside: the progress it reports must be *the* domain
derivation over what is on disk rather than a second computation beside it, and
the read must write nothing at all — the worker is the sole writer of the job
record, and a polling reader that touched it would be the other half of the
race the single-writer rule exists to avoid.

The refusals are AUTH-14's handler half. Malformed and unknown must answer
alike, as `JobNotFound` — the domain error the composition root maps to 404 —
and a malformed id must be refused *before* the store is asked. A handler that
validated after loading would already have handed storage a path component
nobody checked, which is exactly the accident the route-level validation was
written to remove.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.chunking import (
    ChunkPlan,
    ChunkResult,
    ChunkState,
    PlannedChunk,
)
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment
from onevoicecut.shared.domain.errors import JobNotFound
from onevoicecut.shared.domain.ids import (
    JobId,
    make_job_id,
    make_media_id,
    make_operator_id,
)
from onevoicecut.systems.pipeline.jobs.application.use_cases.queries.get_job import (
    GetJobHandler,
    GetJobQuery,
    JobSnapshot,
)
from onevoicecut.shared.domain.speaker import SpeakerMode
from onevoicecut.systems.pipeline.jobs.domain.jobs import (
    EngineChoice,
    JobRecord,
    JobState,
    derive_progress,
)
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
# Well-formed and therefore accepted by the id pattern, but nothing stored
# under it — the unknown case, as distinct from the malformed one below.
UNKNOWN_JOB_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFF"
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")
OWNER = make_operator_id("maria")
CREATED_AT = 1_700_000_000.0
NOW = CREATED_AT + 600.0

MALFORMED_IDS = [
    "not-a-ulid",
    "..",
    "01ARZ3NDEKTSV4RRFFQ69G5FA",  # 25 characters — one short of a ULID
]


def frozen_clock() -> float:
    return NOW


def a_job() -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=JobState.TRANSCRIBING,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        worker_pid=4812,
        error=None,
        owner=OWNER,
    )


def a_plan(count: int) -> ChunkPlan:
    return ChunkPlan(
        job_id=JOB_ID,
        stride_s=600.0,
        overlap_s=5.0,
        chunks=tuple(
            PlannedChunk(index=i, start_s=i * 600.0, end_s=(i + 1) * 600.0)
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
        ),
        engine_id="fake-asr",
        attempts=1,
        error=None,
        finished_at=NOW,
    )


def _stored(tmp_path: Path) -> FakeTranscriptStoragePort:
    """A job sitting in storage, with admission's calls forgotten.

    The call log is cleared because every assertion here measures what the
    *read* did, and an unfiltered log would make a zero-write claim depend on
    how the job got there.
    """
    storage = FakeTranscriptStoragePort(tmp_path)
    storage.create_job(a_job())
    storage.calls.clear()
    return storage


def _get(storage: FakeTranscriptStoragePort, raw: str = str(JOB_ID)) -> JobSnapshot:
    """The handler shape: dependencies live on the handler, the request on the
    query — the same split the command handlers bought in slice 2c."""
    return GetJobHandler(storage=storage, now=frozen_clock).handle(
        GetJobQuery(job_id=raw)
    )


def test_the_progress_is_the_domain_derivation_over_what_is_on_disk(
    tmp_path: Path,
) -> None:
    """Verbatim wrapping, proven by equality with the derivation itself rather
    than by a second copy of its arithmetic — a handler that recomputed progress
    would drift from the domain the day the domain changed, and no assertion on
    the numbers alone would notice."""
    storage = _stored(tmp_path)
    plan = a_plan(87)
    results = tuple(a_result(index) for index in range(10))
    storage.save_chunk_plan(JOB_ID, plan)
    for result in results:
        storage.save_chunk_result(result)
    storage.calls.clear()

    snapshot = _get(storage)

    assert snapshot.job == a_job()
    assert snapshot.progress == derive_progress(
        plan, results, started_at=CREATED_AT, now=NOW
    )
    # The equality above would also hold for two `None`s, so the shape is
    # asserted separately: this must be a real, non-trivial progress.
    assert snapshot.progress is not None
    assert (snapshot.progress.chunks_total, snapshot.progress.chunks_done) == (87, 10)


def test_a_job_that_has_not_been_planned_reports_no_progress(
    tmp_path: Path,
) -> None:
    """`None`, not zero of zero — which would render as a finished job."""
    storage = _stored(tmp_path)

    snapshot = _get(storage)

    assert snapshot.job == a_job()
    assert snapshot.progress is None


def test_an_unknown_job_is_a_domain_not_found(tmp_path: Path) -> None:
    """The store's refusal travels unchanged: `JobNotFound` is what the
    composition root maps to 404, so the query adds no second answer."""
    storage = _stored(tmp_path)

    with pytest.raises(JobNotFound):
        _get(storage, UNKNOWN_JOB_ID)


@pytest.mark.parametrize("raw", MALFORMED_IDS)
def test_a_malformed_id_is_refused_before_the_store_is_touched(
    tmp_path: Path, raw: str
) -> None:
    """AUTH-14's handler half: 404 ahead of any filesystem access, and one
    answer for every caller-supplied shape so no id reveals which ones exist.

    The storage here is deliberately *trusting* — it hands back a record for
    any id it is given — so a query that leaned on the store's own validation
    would return a snapshot instead of refusing. Only validation inside the
    query can make this pass.
    """
    loaded: list[JobId] = []

    class TrustingStorage(FakeTranscriptStoragePort):
        def load_job(self, job_id: JobId) -> JobRecord:
            loaded.append(job_id)
            return a_job()

    storage = TrustingStorage(tmp_path)
    storage.calls.clear()

    with pytest.raises(JobNotFound):
        _get(storage, raw)

    assert loaded == []


def test_reading_writes_nothing(tmp_path: Path) -> None:
    """The read-only GET guarantee, at the layer that must hold it.

    The route tests assert it over HTTP; this asserts it over the query so the
    guarantee survives the route being rewritten around it in slice 2e."""
    storage = _stored(tmp_path)
    storage.save_chunk_plan(JOB_ID, a_plan(3))
    storage.save_chunk_result(a_result(0))
    storage.calls.clear()

    _get(storage)

    assert storage.calls == []
    assert storage.state_history(JOB_ID) == []
