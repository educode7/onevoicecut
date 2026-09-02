"""Reconcile covers every state a worker can own, not just TRANSCRIBING.

The old rule looked at one state, which meant a job whose worker died during
extraction or stitching was never reclaimed. It stayed EXTRACTING forever, and
under the capacity gate it also held a slot forever — so one crash at the wrong
moment could take the machine's only slot out of service permanently.

That gap was survivable while one person used the machine and could recognise
their own stuck job. On a shared board it is somebody else's job, in a state they
have no way to clear, and nobody can tell whether it is working.

Liveness here is the combined rule, so reconcile and the gate cannot disagree
about who is running.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import (
    TERMINAL_STATES,
    WORKER_BOUND_STATES,
    EngineChoice,
    JobRecord,
    JobState,
    SpeakerMode,
)
from onevoicecut.runtime.app import HEARTBEAT_STALE_AFTER_S, reconcile_interrupted_jobs
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")
JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA1")

BEAT_AT = 1_700_000_000.0
FRESH_NOW = BEAT_AT + 60.0
STALE_NOW = BEAT_AT + HEARTBEAT_STALE_AFTER_S + 1.0
LIVE_PID = 4812


def alive(pid: int) -> bool:
    return True


def dead(pid: int) -> bool:
    return False


def a_job(state: JobState, *, pid: int | None = LIVE_PID, owner: str | None = OWNER) -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=state,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=1.0,
        worker_pid=pid,
        error=None,
        owner=owner,  # type: ignore[arg-type]
    )


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


def beating(storage: FakeTranscriptStoragePort) -> None:
    storage.write_heartbeat(JOB_ID, at_s=BEAT_AT)


class TestDeadWorkersAreReclaimed:
    @pytest.mark.parametrize("state", sorted(WORKER_BOUND_STATES))
    def test_every_worker_bound_state_is_reconciled(
        self, storage: FakeTranscriptStoragePort, state: JobState
    ) -> None:
        """HARD-07. Extraction and stitching are as abandonable as transcription.

        The old TRANSCRIBING-only rule is exactly why this is parametrized off
        the domain set rather than a list: a sixth worker-bound state added later
        joins this test the day it is defined.
        """
        storage.create_job(a_job(state))
        beating(storage)

        reconciled = reconcile_interrupted_jobs(
            storage, now=lambda: FRESH_NOW, is_alive=dead
        )

        assert reconciled == (JOB_ID,)
        assert storage.load_job(JOB_ID).state is JobState.INTERRUPTED

    def test_a_stale_heartbeat_reconciles_even_with_a_live_pid(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """HARD-05's reconcile half: the pid-reuse case finally gets cleared.

        Under the old rule this job was immortal — the probe said the pid was
        alive, so reconcile skipped it on every startup, forever.
        """
        storage.create_job(a_job(JobState.EXTRACTING))
        beating(storage)

        reconciled = reconcile_interrupted_jobs(
            storage, now=lambda: STALE_NOW, is_alive=alive
        )

        assert reconciled == (JOB_ID,)

    def test_a_legacy_ownerless_record_reconciles_the_same_way(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """LEG-06: reconcile never reads `owner`. A pre-change job is nobody's
        to mutate over HTTP, but a crash still has to be cleaned up."""
        storage.create_job(a_job(JobState.STITCHING, owner=None))
        beating(storage)

        assert reconcile_interrupted_jobs(
            storage, now=lambda: FRESH_NOW, is_alive=dead
        ) == (JOB_ID,)

    def test_the_owner_survives_reconciliation(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        storage.create_job(a_job(JobState.PLANNED))
        beating(storage)

        reconcile_interrupted_jobs(storage, now=lambda: FRESH_NOW, is_alive=dead)

        assert storage.load_job(JOB_ID).owner == OWNER


class TestLiveWorkersAreLeftAlone:
    @pytest.mark.parametrize("state", sorted(WORKER_BOUND_STATES))
    def test_a_live_worker_is_untouched(
        self, storage: FakeTranscriptStoragePort, state: JobState
    ) -> None:
        """HARD-08. The dangerous direction: reconcile writing over a record a
        live worker owns is the single-writer violation this whole arrangement
        is built to prevent, and it would land mid-transcription."""
        storage.create_job(a_job(state))
        beating(storage)
        before = storage.load_job(JOB_ID)
        storage.calls.clear()

        reconciled = reconcile_interrupted_jobs(
            storage, now=lambda: FRESH_NOW, is_alive=alive
        )

        assert reconciled == ()
        assert storage.load_job(JOB_ID) == before
        assert storage.calls == []


class TestEverythingElseIsOutOfScope:
    @pytest.mark.parametrize(
        "state",
        [
            pytest.param(JobState.PENDING, id="pending-awaits-upload"),
            pytest.param(JobState.QUEUED, id="queued-belongs-to-the-drain"),
            pytest.param(JobState.INTERRUPTED, id="already-interrupted"),
            *[pytest.param(s, id=str(s)) for s in sorted(TERMINAL_STATES)],
        ],
    )
    def test_states_no_worker_owns_are_never_touched(
        self, storage: FakeTranscriptStoragePort, state: JobState
    ) -> None:
        """HARD-09, and CAP-12 preserved under the extension.

        QUEUED is the one that would hurt: sweeping it into INTERRUPTED at boot
        would silently empty the queue on every restart, turning jobs the
        operator uploaded into something needing a re-run.
        """
        storage.create_job(a_job(state, pid=None))
        storage.calls.clear()

        assert reconcile_interrupted_jobs(
            storage, now=lambda: FRESH_NOW, is_alive=dead
        ) == ()
        assert storage.calls == []


class TestItLeavesTheFilesystemAlone:
    def test_the_heartbeat_file_is_not_removed(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """D5: nobody cleans it up. After reconciliation it is inert — liveness
        is only asked about worker-bound states — and a cleaner would be a second
        writer for no correctness gain."""
        storage.create_job(a_job(JobState.TRANSCRIBING))
        beating(storage)

        reconcile_interrupted_jobs(storage, now=lambda: FRESH_NOW, is_alive=dead)

        assert storage.heartbeat_at(JOB_ID) == BEAT_AT

    def test_a_job_directory_with_extra_files_still_lists_as_one_job(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """LEG-09 listing half: files inside a job directory are never mistaken
        for jobs, which is what makes the heartbeat harmless to older builds."""
        storage.create_job(a_job(JobState.TRANSCRIBING))
        beating(storage)
        storage.request_cancellation(JOB_ID)

        assert [job.job_id for job in storage.list_jobs()] == [JOB_ID]
