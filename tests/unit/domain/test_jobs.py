from dataclasses import FrozenInstanceError, replace

import pytest

from onevoicecut.domain.ids import make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")


def test_speaker_mode_defaults_to_single() -> None:
    assert SpeakerMode.SINGLE.value == "single"
    assert {m.value for m in SpeakerMode} == {"single", "multi"}


def test_engine_choice_has_no_default_member() -> None:
    assert {m.value for m in EngineChoice} == {"local", "cloud"}


def test_job_state_covers_full_lifecycle() -> None:
    assert {m.value for m in JobState} == {
        "pending",
        "extracting",
        "planned",
        "transcribing",
        "stitching",
        "generating",
        "completed",
        "failed",
        "cancelled",
        "interrupted",
    }


def test_job_record_holds_fields() -> None:
    record = JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=JobState.PENDING,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=100.0,
        updated_at=100.0,
        worker_pid=None,
        error=None,
        owner=None,
    )
    assert record.state is JobState.PENDING


def test_job_record_is_frozen() -> None:
    record = JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=JobState.PENDING,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=100.0,
        updated_at=100.0,
        worker_pid=None,
        error=None,
        owner=None,
    )
    with pytest.raises(FrozenInstanceError):
        record.state = JobState.FAILED  # type: ignore[misc]


class TestJobRecordOwner:
    """The owner is a required fact of every record, not an afterthought.

    `None` is a legal value — pre-change jobs have no owner — but omitting the
    argument is not: a construction site that could silently forget the field
    would be the first rung of an ownership bypass.
    """

    def test_owner_is_a_required_constructor_argument(self) -> None:
        with pytest.raises(TypeError):
            JobRecord(  # type: ignore[call-arg]
                job_id=JOB_ID,
                media_id=MEDIA_ID,
                state=JobState.PENDING,
                speaker_mode=SpeakerMode.SINGLE,
                engine=EngineChoice.LOCAL,
                created_at=100.0,
                updated_at=100.0,
                worker_pid=None,
                error=None,
            )

    def test_owner_accepts_none_for_legacy_records(self) -> None:
        record = JobRecord(
            job_id=JOB_ID,
            media_id=MEDIA_ID,
            state=JobState.PENDING,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=100.0,
            updated_at=100.0,
            worker_pid=None,
            error=None,
            owner=None,
        )
        assert record.owner is None

    def test_owner_is_frozen(self) -> None:
        record = JobRecord(
            job_id=JOB_ID,
            media_id=MEDIA_ID,
            state=JobState.PENDING,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=100.0,
            updated_at=100.0,
            worker_pid=None,
            error=None,
            owner=make_operator_id("maria"),
        )
        with pytest.raises(FrozenInstanceError):
            record.owner = None  # type: ignore[misc]

    def test_replace_carries_owner_through_a_transition(self) -> None:
        """The only record-mutation vehicle must not drop the owner."""
        record = JobRecord(
            job_id=JOB_ID,
            media_id=MEDIA_ID,
            state=JobState.TRANSCRIBING,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=100.0,
            updated_at=100.0,
            worker_pid=4812,
            error=None,
            owner=make_operator_id("maria"),
        )

        interrupted = replace(record, state=JobState.INTERRUPTED, updated_at=200.0)

        assert interrupted.owner == make_operator_id("maria")
        assert interrupted.state is JobState.INTERRUPTED
