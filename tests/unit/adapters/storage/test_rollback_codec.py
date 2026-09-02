"""What a *previous* build would make of the records this one writes.

Rolling back is a real operational move — the operator reverts the deploy at
eleven at night because something is wrong — and the records on disk do not roll
back with it. So the question is what the old code does when it reads them, and
there are only two acceptable answers: read it correctly, or refuse loudly.

The two halves come out differently on purpose:

- `owner` is an **unknown key** to the old decoder, which builds its record from
  named fields and never looks at it. A job written by this build reads back
  cleanly, minus the ownership it never knew about.
- `queued` is an **unknown state**, and there is no safe interpretation of it. A
  decoder that guessed — PENDING, say — would hand the old build a job it would
  re-admit or re-run. Refusing is the only honest answer, and it is why the
  rollback procedure has to drain the queue first.

The pre-change decoder below is a deliberate reconstruction, not an import: the
old code is gone, and a test that imported today's decoder would be asserting
nothing at all.
"""

from enum import StrEnum

import pytest

from onevoicecut.adapters.storage.serialization import (
    _member,
    _number,
    _optional_text,
    _optional_whole,
    encode_job,
)
from onevoicecut.adapters.storage.serialization import _loads as loads
from onevoicecut.domain.errors import CorruptedRecord
from onevoicecut.domain.ids import make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")


class PreChangeJobState(StrEnum):
    """The state set exactly as it was before the capacity gate. No `queued`."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    PLANNED = "planned"
    TRANSCRIBING = "transcribing"
    STITCHING = "stitching"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


def pre_change_decode(payload: str) -> dict[str, object]:
    """The old `decode_job`, field for field: no `owner`, no `queued`.

    Returns a plain dict rather than a `JobRecord` because today's entity has an
    `owner` the old build had no field for — constructing one would smuggle this
    change into the thing being tested.
    """
    record = loads(payload)
    return {
        "job_id": record["job_id"],
        "media_id": record["media_id"],
        "state": _member(record, "state", PreChangeJobState),
        "speaker_mode": _member(record, "speaker_mode", SpeakerMode),
        "engine": _member(record, "engine", EngineChoice),
        "created_at": _number(record, "created_at"),
        "updated_at": _number(record, "updated_at"),
        "worker_pid": _optional_whole(record, "worker_pid"),
        "error": _optional_text(record, "error"),
    }


def a_job(state: JobState) -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=state,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=2.0,
        worker_pid=None,
        error=None,
        owner=OWNER,
    )


class TestTheOldBuildReadsFinishedJobsFine:
    @pytest.mark.parametrize(
        "state",
        [
            pytest.param(JobState.COMPLETED, id="completed"),
            pytest.param(JobState.FAILED, id="failed"),
            pytest.param(JobState.CANCELLED, id="cancelled"),
            pytest.param(JobState.PENDING, id="pending"),
            pytest.param(JobState.TRANSCRIBING, id="transcribing"),
        ],
    )
    def test_the_unknown_owner_key_is_simply_not_read(self, state: JobState) -> None:
        """CAP-13 / LEG-08: additive fields cost a rollback nothing.

        This is what the codec's field-explicit shape buys. A decoder that
        rejected unknown keys would make every additive change a one-way door.
        """
        decoded = pre_change_decode(encode_job(a_job(state)))

        assert decoded["state"] == state.value
        assert "owner" not in decoded

    def test_the_owner_really_was_in_the_bytes(self) -> None:
        """Otherwise the test above proves only that nothing was written."""
        assert '"owner": "maria"' in encode_job(a_job(JobState.COMPLETED))


class TestTheOldBuildRefusesAQueuedJob:
    def test_a_queued_record_fails_closed(self) -> None:
        """CAP-14. There is no safe reading of a state the old build has no
        code for: it would either crash somewhere further in, or — worse — be
        guessed into PENDING and re-admitted as work nobody asked for."""
        with pytest.raises(CorruptedRecord):
            pre_change_decode(encode_job(a_job(JobState.QUEUED)))

    def test_the_refusal_names_the_value_so_the_operator_can_act(self) -> None:
        """This message is the whole operational instruction: it says which
        file, and which value, so the answer — drain the queue, or move those
        job directories aside — is obvious rather than archaeological."""
        with pytest.raises(CorruptedRecord) as refusal:
            pre_change_decode(encode_job(a_job(JobState.QUEUED)))

        assert "queued" in str(refusal.value)
