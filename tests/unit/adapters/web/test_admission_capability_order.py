"""Capability refusal ordering is preserved with owner — OWN-11.

Admission gained a required `operator` argument in the fused auth slice; the
guard it gained it after must stay exactly where it was: a capability refusal
still happens before any storage touch, and nothing — no record, owned or
otherwise — exists for a refused submission.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.domain.errors import DiarizationUnsupported
from onevoicecut.domain.ids import (
    JobId,
    MediaId,
    generate_job_id,
    generate_media_id,
)
from onevoicecut.domain.jobs import EngineChoice, SpeakerMode
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DeclaredSupport,
    DiarizationSupport,
)
from onevoicecut.usecases.admit_job import admit_job
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    OPERATOR_A,
    TOKEN_A,
    auth_headers,
    fake_authenticate,
)


# Slice 9b-iii narrowed the admission guard from whole capabilities to the one
# field it reads. The rest of a `TranscriptionCapabilities` cannot be known
# without constructing an engine, and that is what kept this guard disconnected
# from the composition root for three slices.


def _client(
    storage: FakeTranscriptStoragePort, diarization: DiarizationSupport
) -> AsyncClient:
    app = create_app(
        WebDependencies(
            storage=storage,
            authenticate=fake_authenticate,
            capabilities=lambda _engine: _declares(diarization),
        )
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


async def test_an_unsatisfiable_admission_refuses_before_any_storage_touch(
    storage: FakeTranscriptStoragePort,
) -> None:
    """OWN-11: authenticated, requesting MULTI from an engine that cannot
    diarize — the capability error answers 422 with zero records created and
    no storage method called at all."""
    async with _client(storage, DiarizationSupport.UNSUPPORTED) as client:
        response = await client.post(
            "/api/jobs",
            json={"engine": "local", "speaker_mode": "multi"},
            headers=auth_headers(TOKEN_A),
        )

    assert response.status_code == 422
    assert "diarization" in response.json()["detail"]
    assert storage.list_jobs() == ()
    assert storage.calls == []


async def test_a_satisfiable_admission_still_records_the_owner(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Positive control: the guard only moves aside for compatible requests,
    and those still record the authenticated caller as owner."""
    async with _client(storage, DiarizationSupport.AVAILABLE) as client:
        response = await client.post(
            "/api/jobs",
            json={"engine": "local", "speaker_mode": "multi"},
            headers=auth_headers(TOKEN_A),
        )

    assert response.status_code == 201
    jobs = storage.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].owner == OPERATOR_A


def test_the_guard_still_runs_before_any_id_is_minted(tmp_path: Path) -> None:
    """OWN-11's ordering at the use case: the refusal precedes even id
    minting, which is the earliest storage-facing act `admit_job` performs —
    the `operator` argument sits after the guard, where the fused slice put
    it, not before it."""
    storage = FakeTranscriptStoragePort(tmp_path)
    minted_job_ids: list[JobId] = []
    minted_media_ids: list[MediaId] = []

    def recording_job_id() -> JobId:
        job_id = generate_job_id()
        minted_job_ids.append(job_id)
        return job_id

    def recording_media_id() -> MediaId:
        media_id = generate_media_id()
        minted_media_ids.append(media_id)
        return media_id

    with pytest.raises(DiarizationUnsupported):
        admit_job(
            engine=EngineChoice.LOCAL,
            speaker_mode=SpeakerMode.MULTI,
            operator=OPERATOR_A,
            storage=storage,
            capabilities=lambda _e: _declares(DiarizationSupport.REQUIRES_SETUP),
            new_job_id=recording_job_id,
            new_media_id=recording_media_id,
        )

    assert minted_job_ids == []
    assert minted_media_ids == []
    assert storage.list_jobs() == ()


def _declares(diarization: DiarizationSupport) -> DeclaredSupport:
    """Slice 10a-ii added the classification axis to the admission guard, so the
    guard now reads a pair. These cases are about diarization, and declare a
    classifying engine so the other axis never decides the outcome."""
    return DeclaredSupport(
        diarization=diarization,
        non_speech_classification=ClassificationSupport.AVAILABLE,
    )
