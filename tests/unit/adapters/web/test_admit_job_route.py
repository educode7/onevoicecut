"""Admitting a job over HTTP: the first real web surface.

Admission is deliberately tiny. It records what the operator chose and returns an
id — it does not touch the media, does not plan, and does not start work. That
separation is what lets the upload of a multi-hour file, and the hours of
transcription after it, happen without an HTTP request waiting on either.

The engine and speaker mode are captured here because this is the only moment the
operator is present to choose them. A worker reading the record hours later has no
one to ask.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.domain.ids import _ULID_PATTERN
from onevoicecut.domain.jobs import EngineChoice, JobState, SpeakerMode
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import auth_headers, fake_authenticate

FIXED_NOW = 1723501234.5


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


@pytest.fixture
async def client(storage: FakeTranscriptStoragePort) -> AsyncIterator[AsyncClient]:
    app = create_app(
        WebDependencies(
            storage=storage, authenticate=fake_authenticate, now=lambda: FIXED_NOW
        )
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers()
    ) as http:
        yield http


async def test_admitting_a_job_returns_its_id(client: AsyncClient) -> None:
    response = await client.post("/api/jobs", json={"engine": "local"})

    assert response.status_code == 201
    assert _ULID_PATTERN.match(response.json()["job_id"])


async def test_the_admitted_job_is_persisted_as_pending(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """PENDING and not EXTRACTING: nothing has been uploaded yet, let alone read."""
    response = await client.post("/api/jobs", json={"engine": "local"})

    job = storage.load_job(response.json()["job_id"])
    assert job.state is JobState.PENDING
    assert job.engine is EngineChoice.LOCAL


async def test_the_speaker_mode_defaults_to_a_single_voice(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """Talking-head is the normal case; diarization is opt-in per job and is never
    inferred from the audio."""
    response = await client.post("/api/jobs", json={"engine": "local"})

    assert storage.load_job(response.json()["job_id"]).speaker_mode is SpeakerMode.SINGLE


async def test_an_interview_job_records_that_choice(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    response = await client.post(
        "/api/jobs", json={"engine": "cloud", "speaker_mode": "multi"}
    )

    job = storage.load_job(response.json()["job_id"])
    assert job.speaker_mode is SpeakerMode.MULTI
    assert job.engine is EngineChoice.CLOUD


async def test_the_engine_must_be_chosen_explicitly(client: AsyncClient) -> None:
    """There is no global default engine. The choice is content-dependent —
    private material goes local — so an omitted engine is a question, not a
    field to fill in with a guess."""
    response = await client.post("/api/jobs", json={})

    assert response.status_code == 422


@pytest.mark.parametrize("engine", ["", "gpu", "LOCAL ", "openai"])
async def test_an_engine_that_is_not_offered_is_refused(
    client: AsyncClient, engine: str
) -> None:
    response = await client.post("/api/jobs", json={"engine": engine})

    assert response.status_code == 422


async def test_an_unknown_speaker_mode_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        "/api/jobs", json={"engine": "local", "speaker_mode": "choir"}
    )

    assert response.status_code == 422


async def test_a_rejected_request_creates_no_job(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    await client.post("/api/jobs", json={"engine": "gpu"})

    assert storage.list_jobs() == ()


async def test_two_admissions_are_two_jobs(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    first = await client.post("/api/jobs", json={"engine": "local"})
    second = await client.post("/api/jobs", json={"engine": "local"})

    assert first.json()["job_id"] != second.json()["job_id"]
    assert len(storage.list_jobs()) == 2


async def test_admission_starts_no_work(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """The request returns before anything expensive happens — there is nothing to
    plan yet, and a multi-hour transcription must never sit inside a response."""
    response = await client.post("/api/jobs", json={"engine": "local"})
    job_id = response.json()["job_id"]

    assert storage.load_chunk_plan(job_id) is None
    assert storage.load_chunk_results(job_id) == ()
    assert storage.load_transcript(job_id) is None


async def test_the_admission_timestamps_come_from_the_injected_clock(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    response = await client.post("/api/jobs", json={"engine": "local"})

    job = storage.load_job(response.json()["job_id"])
    assert job.created_at == FIXED_NOW
    assert job.updated_at == FIXED_NOW
