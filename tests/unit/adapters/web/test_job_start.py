"""When a job actually starts, and when it must not.

The upload is the trigger. Admission cannot be — there is nothing to transcribe
yet — and nothing else in the system polls for work, so if this handoff does not
happen the job sits PENDING forever while every response says success.

Which is why there is no benign default. An app wired without a starter accepts
uploads and quietly transcribes nothing, and the operator would have a job id, a
204, and no reason to suspect anything.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from transcribe.adapters.web.app import WebDependencies, create_app
from transcribe.domain.errors import UnsupportedContainer
from transcribe.domain.ids import JobId, make_job_id
from transcribe.domain.media import MediaProbe
from transcribe.ports.audio_extractor import AudioExtractorPort
from transcribe.ports.transcript_storage import TranscriptStoragePort
from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import accepting_extractor

LIMIT = 4096


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


@pytest.fixture
def started() -> list[JobId]:
    return []


def client_for(
    storage: FakeTranscriptStoragePort,
    started: list[JobId],
    *,
    probe_error: Exception | None = None,
) -> AsyncClient:
    def extractor(_: TranscriptStoragePort, job_id: JobId) -> AudioExtractorPort:
        return FakeAudioExtractorPort(job_id, probe_error=probe_error)

    app = create_app(
        WebDependencies(
            storage=storage,
            max_upload_bytes=LIMIT,
            extractor_for=extractor if probe_error else accepting_extractor,
            start_job=started.append,
        )
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def client(
    storage: FakeTranscriptStoragePort, started: list[JobId]
) -> AsyncIterator[AsyncClient]:
    async with client_for(storage, started) as http:
        yield http


async def admitted(client: AsyncClient) -> JobId:
    response = await client.post("/api/jobs", json={"engine": "local"})
    return make_job_id(response.json()["job_id"])


async def test_admitting_a_job_starts_nothing(
    client: AsyncClient, started: list[JobId]
) -> None:
    """There is no media yet. Starting here would spawn a worker with nothing to
    read."""
    await admitted(client)

    assert started == []


async def test_a_completed_upload_starts_the_job(
    client: AsyncClient, started: list[JobId]
) -> None:
    job_id = await admitted(client)

    await client.put(f"/api/jobs/{job_id}/media", content=b"media")

    assert started == [job_id]


async def test_the_media_record_exists_before_the_worker_is_started(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The worker's first act is to read that record. Starting it first would be
    a race against the process that is still describing the upload."""
    observed: list[bool] = []

    def check_then_start(job_id: JobId) -> None:
        try:
            storage.load_media(job_id)
            observed.append(True)
        except Exception:
            observed.append(False)

    app = create_app(
        WebDependencies(
            storage=storage,
            extractor_for=accepting_extractor,
            start_job=check_then_start,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        job_id = await admitted(client)
        await client.put(f"/api/jobs/{job_id}/media", content=b"media")

    assert observed == [True]


async def test_an_oversized_upload_starts_nothing(
    client: AsyncClient, started: list[JobId]
) -> None:
    job_id = await admitted(client)

    await client.put(f"/api/jobs/{job_id}/media", content=b"x" * (LIMIT + 1))

    assert started == []


async def test_a_file_that_is_not_media_starts_nothing(
    storage: FakeTranscriptStoragePort, started: list[JobId]
) -> None:
    """The refusal already discarded the file. Spawning a worker for it would
    give it nothing to read and a job to fail."""
    async with client_for(
        storage, started, probe_error=UnsupportedContainer("not media")
    ) as client:
        job_id = await admitted(client)
        await client.put(f"/api/jobs/{job_id}/media", content=b"plain text")

    assert started == []


async def test_an_upload_to_an_unknown_job_starts_nothing(
    client: AsyncClient, started: list[JobId]
) -> None:
    await client.put("/api/jobs/01HQ3M8XKJ7VNPQR2ZYWB4TCFF/media", content=b"media")

    assert started == []


async def test_an_app_with_no_starter_refuses_rather_than_silently_doing_nothing(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The failure this default exists to prevent: uploads accepted, nothing
    transcribed, and every response saying success."""
    app = create_app(
        WebDependencies(storage=storage, extractor_for=accepting_extractor)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        job_id = await admitted(client)
        response = await client.put(f"/api/jobs/{job_id}/media", content=b"media")

    assert response.status_code == 500


async def test_a_video_with_no_audio_starts_nothing(
    storage: FakeTranscriptStoragePort, started: list[JobId]
) -> None:
    silent = MediaProbe(duration_s=3600.0, container="mov,mp4,m4a", has_audio=False)

    def extractor(_: TranscriptStoragePort, job_id: JobId) -> AudioExtractorPort:
        return FakeAudioExtractorPort(job_id, probe_result=silent)

    app = create_app(
        WebDependencies(
            storage=storage, extractor_for=extractor, start_job=started.append
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        job_id = await admitted(client)
        await client.put(f"/api/jobs/{job_id}/media", content=b"video only")

    assert started == []
