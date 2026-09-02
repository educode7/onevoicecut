"""One sweep of the drain gate: what it starts, and everything it refuses to.

`drain_once` is the only code in the system that starts work. That is the whole
design: with a single spawn decision point, "never exceed the cap" is true by
construction rather than by two code paths happening to agree.

The load-bearing property is that the active count is **derived** every sweep —
listed off disk, filtered by state, filtered by liveness. Nothing is persisted
between sweeps and no counter is kept, so a web process that dies mid-sweep
leaves nothing to repair, and a worker that dies frees its slot the moment the
next sweep looks. A counter would be correct exactly until the first crash, which
on multi-hour jobs is not a rare event but the one being designed for.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import (
    WORKER_BOUND_STATES,
    EngineChoice,
    JobRecord,
    JobState,
    SpeakerMode,
)
from onevoicecut.runtime.app import drain_once, reconcile_interrupted_jobs
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")

# ULIDs are creation-ordered by construction, so these sort oldest-first.
OLDEST = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA1")
MIDDLE = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA2")
NEWEST = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA3")

LIVE_PID = 4812
DEAD_PID = 9999


def all_alive(pid: int) -> bool:
    return True


def none_alive(pid: int) -> bool:
    return False


def only_live_pid(pid: int) -> bool:
    return pid == LIVE_PID


def a_job(
    job_id: JobId, state: JobState, *, pid: int | None = None
) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        media_id=MEDIA_ID,
        state=state,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=1.0,
        worker_pid=pid,
        error=None,
        owner=OWNER,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


@pytest.fixture
def launched() -> list[JobId]:
    return []


def sweep(
    storage: FakeTranscriptStoragePort,
    launched: list[JobId],
    *,
    cap: int = 1,
    is_alive: Callable[[int], bool] = all_alive,
    spawned: set[JobId] | None = None,
) -> tuple[JobId, ...]:
    return drain_once(
        storage,
        max_concurrent_jobs=cap,
        launch=launched.append,
        is_alive=is_alive,
        spawned=set() if spawned is None else spawned,
    )


class TestTheActiveCountIsDerived:
    def test_two_sweeps_over_unchanged_records_decide_identically(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """Nothing is remembered between sweeps, so nothing can drift.

        Each sweep gets its own dedup set here precisely to isolate the
        derivation: if the count were accumulated anywhere durable, the second
        sweep would reach a different answer from the same disk.
        """
        storage.create_job(a_job(OLDEST, JobState.QUEUED))
        storage.create_job(a_job(MIDDLE, JobState.TRANSCRIBING, pid=LIVE_PID))

        first: list[JobId] = []
        second: list[JobId] = []
        sweep(storage, first, cap=2)
        sweep(storage, second, cap=2)

        assert first == second == [OLDEST]

    @pytest.mark.parametrize("state", sorted(WORKER_BOUND_STATES))
    def test_every_worker_bound_state_occupies_a_slot(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId], state: JobState
    ) -> None:
        """Extraction and stitching hold the machine as surely as transcription
        does. Counting only TRANSCRIBING would let a second job start while the
        first was still using ffmpeg."""
        storage.create_job(a_job(OLDEST, state, pid=LIVE_PID))
        storage.create_job(a_job(NEWEST, JobState.QUEUED))

        sweep(storage, launched, cap=1)

        assert launched == []

    def test_a_worker_bound_record_with_a_dead_pid_frees_its_slot(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        """CAP-06: the record is a lie left by a crash, and the queue should not
        wait on a process that no longer exists."""
        storage.create_job(a_job(OLDEST, JobState.TRANSCRIBING, pid=DEAD_PID))
        storage.create_job(a_job(NEWEST, JobState.QUEUED))

        sweep(storage, launched, cap=1, is_alive=none_alive)

        assert launched == [NEWEST]

    def test_a_worker_bound_record_with_no_pid_at_all_frees_its_slot(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        """No pid means the claim never happened — there is nothing to be alive."""
        storage.create_job(a_job(OLDEST, JobState.EXTRACTING, pid=None))
        storage.create_job(a_job(NEWEST, JobState.QUEUED))

        sweep(storage, launched, cap=1)

        assert launched == [NEWEST]


class TestTheCap:
    @pytest.mark.parametrize("cap", [1, 2, 3])
    def test_a_sweep_never_launches_past_the_cap(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId], cap: int
    ) -> None:
        """CAP-04, with more queued work than slots in every case."""
        for job_id in (OLDEST, MIDDLE, NEWEST):
            storage.create_job(a_job(job_id, JobState.QUEUED))

        sweep(storage, launched, cap=cap)

        assert len(launched) == min(cap, 3)

    @pytest.mark.parametrize("active", [1, 2, 3])
    def test_queued_work_waits_while_the_cap_is_full(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId], active: int
    ) -> None:
        """CAP-03: the (N+1)th job stays QUEUED and derived active stays N."""
        running = [OLDEST, MIDDLE, NEWEST][:active]
        for job_id in running:
            storage.create_job(a_job(job_id, JobState.TRANSCRIBING, pid=LIVE_PID))
        waiting = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA9")
        storage.create_job(a_job(waiting, JobState.QUEUED))

        sweep(storage, launched, cap=active)

        assert launched == []
        assert storage.load_job(waiting).state is JobState.QUEUED

    def test_free_slots_are_filled_up_to_the_cap_and_no_further(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        storage.create_job(a_job(OLDEST, JobState.TRANSCRIBING, pid=LIVE_PID))
        storage.create_job(a_job(MIDDLE, JobState.QUEUED))
        storage.create_job(a_job(NEWEST, JobState.QUEUED))

        sweep(storage, launched, cap=2)

        assert launched == [MIDDLE]


class TestOrdering:
    def test_queued_work_starts_oldest_first(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        """CAP-07. Inserted newest-first so insertion order cannot produce this
        answer by accident — the ULID ordering the port promises has to."""
        storage.create_job(a_job(NEWEST, JobState.QUEUED))
        storage.create_job(a_job(MIDDLE, JobState.QUEUED))
        storage.create_job(a_job(OLDEST, JobState.QUEUED))

        sweep(storage, launched, cap=1)

        assert launched == [OLDEST]

    def test_the_whole_queue_drains_in_creation_order(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        storage.create_job(a_job(NEWEST, JobState.QUEUED))
        storage.create_job(a_job(OLDEST, JobState.QUEUED))
        storage.create_job(a_job(MIDDLE, JobState.QUEUED))

        sweep(storage, launched, cap=3)

        assert launched == [OLDEST, MIDDLE, NEWEST]


class TestWhatIsNeverLaunched:
    @pytest.mark.parametrize(
        "state",
        [
            pytest.param(JobState.PENDING, id="pending-has-no-media"),
            pytest.param(JobState.CANCELLED, id="cancelled"),
            pytest.param(JobState.COMPLETED, id="completed"),
            pytest.param(JobState.FAILED, id="failed"),
            pytest.param(JobState.INTERRUPTED, id="interrupted"),
        ],
    )
    def test_only_queued_records_are_candidates(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId], state: JobState
    ) -> None:
        """A PENDING job has no media yet; an INTERRUPTED one is a resume the
        operator has not asked for. Neither is the gate's business."""
        storage.create_job(a_job(OLDEST, state))

        sweep(storage, launched, cap=3)

        assert launched == []

    def test_the_gate_writes_nothing_to_the_record_it_launches(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        """CAP-11: QUEUED → EXTRACTING is the worker's own first claim.

        A gate write here would put two processes on one record at exactly the
        moment the worker is claiming it — the single-writer violation this
        whole arrangement exists to avoid.
        """
        storage.create_job(a_job(OLDEST, JobState.QUEUED))
        storage.calls.clear()

        sweep(storage, launched, cap=1)

        assert launched == [OLDEST]
        assert storage.calls == []

    def test_the_owner_survives_the_gate_untouched(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        """OWN-02 — trivially true while the gate writes nothing, and worth
        locking so it stays that way."""
        storage.create_job(a_job(OLDEST, JobState.QUEUED))

        sweep(storage, launched, cap=1)

        assert storage.load_job(OLDEST).owner == OWNER


class TestTheSpawnedSet:
    """Between the launcher call and the worker's pid claim, the record still
    reads QUEUED. Without a memory of what was already issued, the next sweep
    five seconds later would start a second worker on the same job."""

    def test_an_issued_spawn_is_not_repeated_on_the_next_sweep(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        storage.create_job(a_job(OLDEST, JobState.QUEUED))
        spawned: set[JobId] = set()

        sweep(storage, launched, cap=1, spawned=spawned)
        sweep(storage, launched, cap=1, spawned=spawned)

        assert launched == [OLDEST]

    def test_it_is_not_a_counter_it_does_not_consume_a_slot(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        """The set idempotizes issuance; the cap arithmetic stays derived.

        A job that was launched but has not claimed yet is not worker-bound, so
        it occupies no derived slot. That is deliberate and it is why the set
        must never be treated as "workers running".
        """
        storage.create_job(a_job(OLDEST, JobState.QUEUED))
        storage.create_job(a_job(NEWEST, JobState.QUEUED))
        spawned: set[JobId] = set()

        sweep(storage, launched, cap=2, spawned=spawned)
        sweep(storage, launched, cap=2, spawned=spawned)

        assert launched == [OLDEST, NEWEST]

    def test_an_id_is_pruned_once_its_worker_has_claimed_it(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        storage.create_job(a_job(OLDEST, JobState.QUEUED))
        spawned: set[JobId] = set()
        sweep(storage, launched, cap=1, spawned=spawned)

        storage.update_job(a_job(OLDEST, JobState.EXTRACTING, pid=LIVE_PID))
        sweep(storage, launched, cap=1, spawned=spawned)

        assert spawned == set()

    def test_an_id_is_pruned_once_its_job_is_no_longer_queued(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        """Cancelled after issuance: the set must not hold the id forever, or a
        long-lived web process leaks one entry per cancelled job."""
        storage.create_job(a_job(OLDEST, JobState.QUEUED))
        spawned: set[JobId] = set()
        sweep(storage, launched, cap=1, spawned=spawned)

        storage.update_job(a_job(OLDEST, JobState.CANCELLED))
        sweep(storage, launched, cap=1, spawned=spawned)

        assert spawned == set()


class TestTheReReadBeforeSpawn:
    def test_a_job_cancelled_while_queued_is_never_spawned(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        """CAP-09 / CXL-07's gate half: cancel wins the ordinary race."""
        storage.create_job(a_job(OLDEST, JobState.CANCELLED))

        sweep(storage, launched, cap=1)

        assert launched == []
        assert storage.load_job(OLDEST).state is JobState.CANCELLED

    def test_a_cancel_landing_after_the_listing_still_wins(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        """The re-read is the point, and only a divergence proves it happened.

        Here the listing says QUEUED and the individual read says CANCELLED —
        exactly the window between a sweep enumerating candidates and deciding
        to start one. A gate that trusted the listing would spawn a worker for a
        job the operator stopped.
        """
        storage.create_job(a_job(OLDEST, JobState.QUEUED))
        stale_listing = storage.list_jobs()

        def diverging_list() -> tuple[JobRecord, ...]:
            storage.update_job(a_job(OLDEST, JobState.CANCELLED))
            return stale_listing

        storage.list_jobs = diverging_list  # type: ignore[method-assign]

        sweep(storage, launched, cap=1)

        assert launched == []


class TestReconcileLeavesTheQueueAlone:
    def test_startup_reconcile_does_not_interrupt_queued_jobs(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """CAP-12, locked ahead of the reconcile widening in the next slice.

        A queued job has no worker, so it cannot have a dead one. Sweeping it
        into INTERRUPTED at boot would silently empty the queue on every
        restart — and the operator would see jobs they uploaded turn into
        something that needs re-running, with nothing having gone wrong.
        """
        storage.create_job(a_job(OLDEST, JobState.QUEUED))

        reconcile_interrupted_jobs(storage, now=lambda: 2.0, is_alive=none_alive)

        assert storage.load_job(OLDEST).state is JobState.QUEUED
