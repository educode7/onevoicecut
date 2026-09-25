"""The shared board as a query: every job, attributed, hidden from nobody.

`ListJobs` is the read model behind `GET /api/jobs`. Three properties belong to
the query rather than to its route, because they have to survive the route
being rewritten around it in slice 2e:

- The source is the store's *unscoped* `list_jobs()` — the same listing startup
  reconcile uses. Nothing the store knows can be filtered out here, so the
  board's completeness is structural rather than a promise this handler keeps.
- Every returned record carries its own `owner`, so attribution (VIS-03) is a
  property of the data, and a legacy record's missing owner (VIS-04) surfaces
  as `None` — present, attributed to nobody, hidden from nobody.
- `mine` narrows the tuple this handler returns, *before* any presentation
  concern exists: the tuple is already the answer, and the response schema that
  projects it (2e, then 5b's allow-list) cannot widen it back.

The read writes nothing, the same guarantee `GetJob` carries: the worker is
the sole writer of every record on this board.
"""

from pathlib import Path

from onevoicecut.shared.application.principal import Principal
from onevoicecut.shared.domain.ids import (
    JobId,
    OperatorId,
    make_job_id,
    make_media_id,
    make_operator_id,
)
from onevoicecut.systems.pipeline.jobs.application.use_cases.queries.list_jobs import (
    ListJobsHandler,
    ListJobsQuery,
)
from onevoicecut.shared.domain.speaker import SpeakerMode
from onevoicecut.systems.pipeline.jobs.domain.jobs import (
    EngineChoice,
    JobRecord,
    JobState,
)
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

MARIA = make_operator_id("maria")
DIEGO = make_operator_id("diego")

MARIAS_JOB = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
DIEGOS_JOB = make_job_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")
# Written before owners existed: no owner key, no owner.
LEGACY_JOB = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")

MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRW")
CREATED_AT = 1_700_000_000.0


def a_job(job_id: JobId, *, owner: OperatorId | None) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        media_id=MEDIA_ID,
        state=JobState.COMPLETED,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        worker_pid=None,
        error=None,
        owner=owner,
    )


def _stored(tmp_path: Path) -> FakeTranscriptStoragePort:
    """Two operators' jobs plus a legacy one, with setup calls forgotten.

    The call log is cleared because the last assertion measures what the
    *listing* wrote, and an unfiltered log would make that depend on how the
    board got populated.
    """
    storage = FakeTranscriptStoragePort(tmp_path)
    storage.create_job(a_job(MARIAS_JOB, owner=MARIA))
    storage.create_job(a_job(DIEGOS_JOB, owner=DIEGO))
    storage.create_job(a_job(LEGACY_JOB, owner=None))
    storage.calls.clear()
    return storage


def _list(
    storage: FakeTranscriptStoragePort,
    *,
    identity: OperatorId = MARIA,
    mine: bool = False,
) -> tuple[JobRecord, ...]:
    """The handler shape: dependencies live on the handler, identity on the query."""
    return ListJobsHandler(storage=storage).handle(
        ListJobsQuery(principal=Principal(identity=identity), mine=mine)
    )


def test_the_source_is_the_unscoped_listing_reconcile_uses(tmp_path: Path) -> None:
    """Not a caller-scoped view of the store: what comes back is the store's own
    listing, which is what makes "nothing hidden" structural (VIS-03/VIS-05)."""
    storage = _stored(tmp_path)

    jobs = _list(storage)

    assert jobs == storage.list_jobs()
    assert {job.job_id for job in jobs} == {MARIAS_JOB, DIEGOS_JOB, LEGACY_JOB}


def test_every_returned_job_is_attributed_to_its_owner(tmp_path: Path) -> None:
    """VIS-03: both operators' jobs come back to either of them, each row saying
    whose it is — attribution is the record's own owner, never the caller's
    identity read back to them."""
    storage = _stored(tmp_path)

    attributed = {job.job_id: job.owner for job in _list(storage, identity=DIEGO)}

    assert attributed == {MARIAS_JOB: MARIA, DIEGOS_JOB: DIEGO, LEGACY_JOB: None}


def test_a_legacy_job_is_listed_without_an_owner(tmp_path: Path) -> None:
    """VIS-04: a record persisted before owners existed is present in the
    listing with `owner` as `None` — null attribution, not absence."""
    storage = _stored(tmp_path)

    listed = {job.job_id: job for job in _list(storage)}

    assert LEGACY_JOB in listed
    assert listed[LEGACY_JOB].owner is None


def test_the_mine_filter_narrows_the_tuple_to_the_callers_jobs(
    tmp_path: Path,
) -> None:
    """Server-side and pre-presentation: the handler returns an already-narrowed
    tuple of records, so no presentation layer could present a filtered-out job.
    A foreign job is excluded, and so is the legacy one — an ownerless record
    belongs to the caller by no reading of the word."""
    storage = _stored(tmp_path)

    jobs = _list(storage, identity=MARIA, mine=True)

    assert [job.job_id for job in jobs] == [MARIAS_JOB]
    assert all(job.owner == MARIA for job in jobs)


def test_listing_writes_nothing(tmp_path: Path) -> None:
    """Read access to every job is the point of a shared server; writing to the
    board is nobody's. The read-only GET guarantee, asserted at the layer that
    must hold it."""
    storage = _stored(tmp_path)

    _list(storage, mine=True)

    assert storage.calls == []
