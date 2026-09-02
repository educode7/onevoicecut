"""Cancellation classifies over every state, and *who writes* is the whole point.

The single-writer rule makes this delicate. While a worker lives it owns the job
record, so the web process may only drop a signal in the control file and must
write nothing itself. When no worker exists there is nobody to race and the web
process records the terminal state directly.

Getting that branch wrong does not fail loudly — it corrupts a running job's
record from another process, hours into a transcription nobody wants to repeat.
So every state is asserted rather than sampled, and the two parametrized sets are
read from the domain so a state added later cannot quietly escape coverage.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from onevoicecut.domain.errors import JobNotFound, JobNotOwned
from onevoicecut.domain.ids import (
    OperatorId,
    make_job_id,
    make_media_id,
    make_operator_id,
)
from onevoicecut.domain.jobs import (
    TERMINAL_STATES,
    WORKER_BOUND_STATES,
    EngineChoice,
    JobRecord,
    JobState,
    SpeakerMode,
)
from onevoicecut.usecases.cancel_job import cancel_job
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")
OWNER = make_operator_id("maria")
STRANGER = make_operator_id("diego")
ADMITTED_AT = 1_700_000_000.0
NOW = 1_700_000_500.0

UNBOUND_STATES = [JobState.PENDING, JobState.QUEUED, JobState.INTERRUPTED]


def frozen_clock() -> float:
    return NOW


def _stored(
    tmp_path: Path, state: JobState, *, owner: OperatorId | None = OWNER
) -> FakeTranscriptStoragePort:
    """A job sitting in one state, with admission's calls forgotten.

    The call log is cleared because these tests measure what *cancellation*
    wrote, and an unfiltered log would make every zero-write assertion depend on
    how the job got there.
    """
    storage = FakeTranscriptStoragePort(tmp_path)
    storage.create_job(
        JobRecord(
            job_id=JOB_ID,
            media_id=MEDIA_ID,
            state=state,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=ADMITTED_AT,
            updated_at=ADMITTED_AT,
            worker_pid=None,
            error=None,
            owner=owner,
        )
    )
    storage.calls.clear()
    return storage


def _cancel(
    storage: FakeTranscriptStoragePort,
    *,
    operator: OperatorId = OWNER,
    now: Callable[[], float] = frozen_clock,
) -> JobRecord:
    return cancel_job(JOB_ID, operator=operator, storage=storage, now=now)


class TestWorkerBoundCancellation:
    """A worker owns the record: signal only, write nothing (CXL-03)."""

    @pytest.mark.parametrize("state", sorted(WORKER_BOUND_STATES))
    def test_signals_through_the_control_file_alone(
        self, state: JobState, tmp_path: Path
    ) -> None:
        storage = _stored(tmp_path, state)

        _cancel(storage)

        assert storage.calls == ["request_cancellation:True"]

    @pytest.mark.parametrize("state", sorted(WORKER_BOUND_STATES))
    def test_leaves_the_record_exactly_as_the_worker_left_it(
        self, state: JobState, tmp_path: Path
    ) -> None:
        storage = _stored(tmp_path, state)

        _cancel(storage)

        stored = storage.load_job(JOB_ID)
        assert stored.state is state
        assert stored.updated_at == ADMITTED_AT

    def test_returns_the_record_unchanged_so_the_route_reports_the_truth(
        self, tmp_path: Path
    ) -> None:
        """200 comes back immediately; the state is still the running one.

        The alternative — reporting CANCELLED before the worker has stopped —
        would have the operator close the tab on a job still burning an hour of
        CPU, and the shared board would contradict itself on the next poll.
        """
        storage = _stored(tmp_path, JobState.TRANSCRIBING)

        job = _cancel(storage)

        assert job.state is JobState.TRANSCRIBING


class TestUnboundCancellation:
    """No worker exists, so the web process records the outcome itself."""

    @pytest.mark.parametrize("state", UNBOUND_STATES)
    def test_records_the_terminal_state(self, state: JobState, tmp_path: Path) -> None:
        storage = _stored(tmp_path, state)

        job = _cancel(storage)

        assert job.state is JobState.CANCELLED
        assert storage.load_job(JOB_ID).state is JobState.CANCELLED

    @pytest.mark.parametrize("state", UNBOUND_STATES)
    def test_also_writes_the_control_file(
        self, state: JobState, tmp_path: Path
    ) -> None:
        """Belt and braces, and for QUEUED it is load-bearing.

        A queued job may still be spawned by a drain that read the record a
        moment before this wrote it. The control file is what that worker sees
        at its first chunk boundary, so the race resolves into zero work rather
        than a full transcription of a cancelled job.
        """
        storage = _stored(tmp_path, state)

        _cancel(storage)

        assert "request_cancellation:True" in storage.calls

    @pytest.mark.parametrize("state", UNBOUND_STATES)
    def test_stamps_the_injected_clock(self, state: JobState, tmp_path: Path) -> None:
        storage = _stored(tmp_path, state)

        _cancel(storage)

        assert storage.load_job(JOB_ID).updated_at == NOW

    def test_does_not_reassign_the_owner(self, tmp_path: Path) -> None:
        storage = _stored(tmp_path, JobState.PENDING)

        _cancel(storage)

        assert storage.load_job(JOB_ID).owner == OWNER


class TestTerminalCancellation:
    """Already finished: nothing to stop, so nothing is touched (CXL-06)."""

    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
    def test_writes_nothing_at_all(self, state: JobState, tmp_path: Path) -> None:
        storage = _stored(tmp_path, state)

        _cancel(storage)

        assert storage.calls == []

    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
    def test_reports_the_state_the_job_already_had(
        self, state: JobState, tmp_path: Path
    ) -> None:
        """Idempotent rather than 409.

        A second cancel is what a double-click sends. Refusing it would force
        every client to special-case "already done" to distinguish a real error
        from the outcome it was asking for anyway.
        """
        storage = _stored(tmp_path, state)

        job = _cancel(storage)

        assert job.state is state


class TestCancellationAuthorization:
    """The shared ownership gate, checked before any branch is taken."""

    def test_non_owner_is_refused_with_nothing_touched(self, tmp_path: Path) -> None:
        storage = _stored(tmp_path, JobState.TRANSCRIBING)

        with pytest.raises(JobNotOwned):
            _cancel(storage, operator=STRANGER)

        assert storage.calls == []
        assert storage.load_job(JOB_ID).state is JobState.TRANSCRIBING

    def test_a_legacy_ownerless_job_is_cancellable_by_nobody(
        self, tmp_path: Path
    ) -> None:
        """Readable by all, mutable by none — no special case in this use case."""
        storage = _stored(tmp_path, JobState.TRANSCRIBING, owner=None)

        with pytest.raises(JobNotOwned):
            _cancel(storage)

        assert storage.calls == []

    def test_ownership_is_checked_before_the_state_branch(
        self, tmp_path: Path
    ) -> None:
        """Even the no-op branch refuses a stranger.

        Terminal cancellation writes nothing either way, so an ownership check
        placed after the branch would look correct in every observable respect —
        right up until someone reorders the branches.
        """
        storage = _stored(tmp_path, JobState.COMPLETED)

        with pytest.raises(JobNotOwned):
            _cancel(storage, operator=STRANGER)

    def test_unknown_job_is_not_found(self, tmp_path: Path) -> None:
        storage = FakeTranscriptStoragePort(tmp_path)

        with pytest.raises(JobNotFound):
            _cancel(storage)
