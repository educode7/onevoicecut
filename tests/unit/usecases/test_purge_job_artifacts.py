"""The purge seam carries ownership — OWN-06 and the purge arm of OWN-05.

The seam is still a request, not an action: no route exists for it and no
policy consumes it yet. What this change adds is the contract a future caller
must satisfy — the authenticated operator is part of the request itself, and
the gate it faces is the same `require_owner` every other mutation faces, so
a non-owner request dies before any effect an eventual policy could have.
"""

import pytest

from onevoicecut.domain.errors import JobNotOwned
from onevoicecut.domain.ids import (
    OperatorId,
    make_job_id,
    make_media_id,
    make_operator_id,
)
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.usecases.ownership import require_owner
from onevoicecut.usecases.purge_job_artifacts import (
    PurgeableArtifact,
    PurgeJobArtifacts,
)

OPERATOR_A = make_operator_id("a")
OPERATOR_B = make_operator_id("b")
JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")


def a_job(owner: OperatorId | None) -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
        media_id=make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE"),
        state=JobState.COMPLETED,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=1.0,
        worker_pid=None,
        error=None,
        owner=owner,
    )


def test_the_seam_requires_the_operator_identity() -> None:
    """OWN-06: the contract demands the caller now, so the eventual route
    needs no signature surgery. A request built without an operator fails at
    construction, not at use."""
    with pytest.raises(TypeError):
        PurgeJobArtifacts(job_id=JOB_ID)  # type: ignore[call-arg]


def test_the_seam_records_the_operator_it_was_given() -> None:
    request = PurgeJobArtifacts(job_id=JOB_ID, operator=OPERATOR_A)

    assert request.operator == OPERATOR_A
    assert request.keep == frozenset(PurgeableArtifact)


def test_a_non_owner_purge_request_is_refused_before_any_effect() -> None:
    """OWN-05 purge arm / OWN-06: the seam's gate is the shared rule. Operator
    B's request raises the ownership error — the same error the upload route
    translates to 403 — with the request left unexecuted."""
    job = a_job(OPERATOR_A)
    request = PurgeJobArtifacts(job_id=job.job_id, operator=OPERATOR_B)

    with pytest.raises(JobNotOwned):
        require_owner(job, request.operator)

    # The refusal precedes any effect: the request is frozen, the record is
    # untouched, and nothing else exists that a purge could have changed.
    assert request == PurgeJobArtifacts(job_id=job.job_id, operator=OPERATOR_B)
    assert job == a_job(OPERATOR_A)


def test_an_owner_purge_request_passes_the_gate() -> None:
    """The owner proceeds: the gate raises nothing, and the request carries
    everything a future policy needs — job, caller, and what survives."""
    job = a_job(OPERATOR_A)
    request = PurgeJobArtifacts(
        job_id=job.job_id,
        operator=OPERATOR_A,
        keep=frozenset({PurgeableArtifact.NORMALIZED_AUDIO}),
    )

    require_owner(job, request.operator)

    assert request.keep == frozenset({PurgeableArtifact.NORMALIZED_AUDIO})


def test_an_ownerless_legacy_job_refuses_purge_from_everyone() -> None:
    """D1's uniform rule reaches the seam: a pre-change record with no owner
    is purgeable by nobody, exactly as it is uploadable by nobody."""
    job = a_job(None)
    request = PurgeJobArtifacts(job_id=job.job_id, operator=OPERATOR_A)

    with pytest.raises(JobNotOwned):
        require_owner(job, request.operator)
