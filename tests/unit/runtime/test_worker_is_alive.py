"""One definition of "this worker is alive", used by everything that asks.

Two facts have to agree, and either can veto the other:

- A **live pid with a stale heartbeat** is not alive. That is pid reuse: the
  number belongs to somebody else's process now, and `os.kill(pid, 0)` cheerfully
  says yes. Before this existed, that job was immortal — reconcile looked at it,
  saw a live pid, and left it alone forever.
- A **dead pid with a fresh heartbeat** is not alive either. The heartbeat is a
  record of the past; the process is gone.

Reconcile and the capacity gate both ask this question, and they must never
answer it differently. A gate that thought a slot was busy while reconcile
thought the job was abandoned would mark the record INTERRUPTED and still refuse
to start anything in its place.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.ids import make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.runtime.app import HEARTBEAT_STALE_AFTER_S, worker_is_alive
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")

BEAT_AT = 1_700_000_000.0
LIVE_PID = 4812


def alive(pid: int) -> bool:
    return True


def dead(pid: int) -> bool:
    return False


def a_job(*, pid: int | None = LIVE_PID) -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=JobState.TRANSCRIBING,
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


def test_a_live_pid_with_a_fresh_heartbeat_is_alive(
    storage: FakeTranscriptStoragePort,
) -> None:
    """HARD-04: the ordinary case, and the only combination that passes."""
    storage.write_heartbeat(JOB_ID, at_s=BEAT_AT)

    assert worker_is_alive(a_job(), storage, is_alive=alive, now=BEAT_AT + 60.0)


def test_a_stale_heartbeat_vetoes_a_live_pid(
    storage: FakeTranscriptStoragePort,
) -> None:
    """HARD-05, the pid-reuse case, and the reason this helper exists.

    The process answering to that number is somebody else's. Trusting the pid
    alone left the job visible on the shared board, forever, in a state no
    operator could clear.
    """
    storage.write_heartbeat(JOB_ID, at_s=BEAT_AT)

    assert not worker_is_alive(
        a_job(), storage, is_alive=alive, now=BEAT_AT + HEARTBEAT_STALE_AFTER_S + 1.0
    )


def test_a_dead_pid_vetoes_a_fresh_heartbeat(
    storage: FakeTranscriptStoragePort,
) -> None:
    """HARD-06: the heartbeat records the past; the process is what matters now."""
    storage.write_heartbeat(JOB_ID, at_s=BEAT_AT)

    assert not worker_is_alive(a_job(), storage, is_alive=dead, now=BEAT_AT + 60.0)


def test_a_record_with_no_pid_is_not_alive(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The claim never happened, so there is nothing to be alive."""
    storage.write_heartbeat(JOB_ID, at_s=BEAT_AT)

    assert not worker_is_alive(
        a_job(pid=None), storage, is_alive=alive, now=BEAT_AT + 60.0
    )


def test_no_heartbeat_at_all_is_not_alive(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Fails closed, which also covers every record written before heartbeats
    existed: those jobs reconcile on the next restart rather than persisting."""
    assert not worker_is_alive(a_job(), storage, is_alive=alive, now=BEAT_AT)


def test_asking_writes_nothing(storage: FakeTranscriptStoragePort) -> None:
    """Reconcile and the gate both call this on every record they consider. A
    read that wrote would make simply looking at the queue a mutation."""
    storage.write_heartbeat(JOB_ID, at_s=BEAT_AT)
    storage.calls.clear()

    worker_is_alive(a_job(), storage, is_alive=alive, now=BEAT_AT)

    assert storage.calls == []


class TestTheBound:
    def test_it_is_two_hours(self) -> None:
        """Sized from the worst case the loop can produce: a chunk retried up to
        three times under the thirty-minute per-chunk timeout, or the extraction
        phase before the first boundary. Two hours covers that with margin while
        keeping a hung worker's window bounded."""
        assert HEARTBEAT_STALE_AFTER_S == 7200.0

    def test_it_is_a_constant_not_a_setting(self) -> None:
        """A property of the liveness rule, not an operator preference. Tuning it
        down orphans healthy jobs; tuning it up is indistinguishable from not
        having it. Neither is a knob worth exposing."""
        from onevoicecut.runtime.settings import Settings

        assert not [f for f in Settings.model_fields if "heartbeat" in f.lower()]
        assert not [f for f in Settings.model_fields if "stale" in f.lower()]
