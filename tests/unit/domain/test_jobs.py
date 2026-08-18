from dataclasses import FrozenInstanceError

import pytest

from transcribe.domain.ids import make_job_id, make_media_id
from transcribe.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode

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
    )
    with pytest.raises(FrozenInstanceError):
        record.state = JobState.FAILED  # type: ignore[misc]
