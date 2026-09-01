"""Ownership: the caller admitted is the only one who mutates.

Authentication says who is calling; this says what they may touch. One rule —
`require_owner` — serves every mutating path, and `owner=None` (a legacy job)
fails it for everyone: visible to all, mutable by nobody.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.errors import DomainError, JobNotOwned
from onevoicecut.domain.ids import (
    OperatorId,
    make_job_id,
    make_media_id,
    make_operator_id,
)
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.usecases.admit_job import admit_job
from onevoicecut.usecases.ownership import require_owner
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

OPERATOR_A = make_operator_id("a")
OPERATOR_B = make_operator_id("b")


def an_owned_job(owner: OperatorId | None = OPERATOR_A) -> JobRecord:
    return JobRecord(
        job_id=make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD"),
        media_id=make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE"),
        state=JobState.PENDING,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=1.0,
        worker_pid=None,
        error=None,
        owner=owner,
    )


def test_job_not_owned_is_a_domain_error() -> None:
    assert issubclass(JobNotOwned, DomainError)


def test_the_owner_passes_the_check() -> None:
    require_owner(an_owned_job(OPERATOR_A), OPERATOR_A)


def test_a_different_operator_is_refused() -> None:
    """OWN-05's shared rule: the refusal is one domain error, raised by the use
    case, translated by the adapter — never decided per route."""
    with pytest.raises(JobNotOwned):
        require_owner(an_owned_job(OPERATOR_A), OPERATOR_B)


def test_an_ownerless_legacy_job_is_mutable_by_nobody() -> None:
    """D1's uniform rule: `owner=None` is never equal to an operator, so legacy
    jobs get the same refusal as foreign ones — no special case to audit."""
    with pytest.raises(JobNotOwned):
        require_owner(an_owned_job(None), OPERATOR_A)


def test_admission_records_the_authenticated_caller_as_owner(
    tmp_path: Path,
) -> None:
    """OWN-01: the persisted record carries exactly the caller's identity."""
    storage = FakeTranscriptStoragePort(tmp_path)

    job = admit_job(
        engine=EngineChoice.LOCAL,
        speaker_mode=SpeakerMode.SINGLE,
        operator=OPERATOR_A,
        storage=storage,
    )

    assert job.owner == OPERATOR_A
    assert storage.load_job(job.job_id).owner == OPERATOR_A


def test_the_persisted_record_carries_the_name_never_the_token(
    tmp_path: Path,
) -> None:
    """AUTH-09 record half: only the operator name is persisted. The token never
    reaches this layer, and the bytes prove it stayed out."""
    storage = FilesystemTranscriptStorage(tmp_path)
    token = "t-a-sekrit-value"

    job = admit_job(
        engine=EngineChoice.LOCAL,
        speaker_mode=SpeakerMode.SINGLE,
        operator=OPERATOR_A,
        storage=storage,
    )

    record_bytes = (storage.job_dir(job.job_id) / "job.json").read_bytes()
    assert b'"owner": "a"' in record_bytes
    assert token.encode() not in record_bytes
