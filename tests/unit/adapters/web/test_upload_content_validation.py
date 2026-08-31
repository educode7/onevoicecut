"""What a file *is*, decided by looking inside it.

An extension is a claim by whoever named the file, and it is the wrong thing to
trust twice over: it does not stop a text file called `sermon.mp4`, and it would
reject a real recording someone named `sermon`. So the ingest path probes the
bytes and believes the answer.

The interesting rejection is not the obviously-broken file. It is the one that
looks fine — a media container with no audio stream, which extracts cleanly to a
silent track and transcribes to nothing.
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


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


def client_for(
    storage: FakeTranscriptStoragePort,
    *,
    probe_result: MediaProbe | None = None,
    probe_error: Exception | None = None,
) -> AsyncClient:
    def extractor(_: TranscriptStoragePort, job_id: JobId) -> AudioExtractorPort:
        return FakeAudioExtractorPort(
            job_id, probe_result=probe_result, probe_error=probe_error
        )

    app = create_app(WebDependencies(storage=storage, extractor_for=extractor))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def client(
    storage: FakeTranscriptStoragePort,
) -> AsyncIterator[AsyncClient]:
    async with client_for(storage) as http:
        yield http


async def admitted(client: AsyncClient) -> JobId:
    response = await client.post("/api/jobs", json={"engine": "local"})
    return make_job_id(response.json()["job_id"])


async def test_the_recorded_container_comes_from_the_probe(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """`unverified` was a placeholder for exactly this moment. Once ffprobe has
    spoken, the media record carries what the file actually is."""
    job_id = await admitted(client)

    await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"pretend-media",
        headers={"x-filename": "sermon.avi"},
    )

    assert storage.load_media(job_id).container == "mov,mp4,m4a"


async def test_a_file_that_is_not_media_is_refused(
    storage: FakeTranscriptStoragePort,
) -> None:
    """A text file named `sermon.mp4`. The extension says one thing and the bytes
    say another, and only the bytes are consulted."""
    async with client_for(
        storage, probe_error=UnsupportedContainer("no media in there")
    ) as client:
        job_id = await admitted(client)

        response = await client.put(
            f"/api/jobs/{job_id}/media",
            content=b"this is plain text",
            headers={"x-filename": "sermon.mp4"},
        )

    assert response.status_code == 415


async def test_a_container_with_no_audio_is_refused(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The rejection that matters, because nothing about it looks wrong. A
    video-only file extracts cleanly to a silent track and transcribes to an
    empty sermon, and the operator would have no idea why."""
    silent = MediaProbe(duration_s=3600.0, container="mov,mp4,m4a", has_audio=False)

    async with client_for(storage, probe_result=silent) as client:
        job_id = await admitted(client)

        response = await client.put(f"/api/jobs/{job_id}/media", content=b"video only")

    assert response.status_code == 415


async def test_a_refused_file_is_discarded(
    storage: FakeTranscriptStoragePort,
) -> None:
    """It was refused, so it is not the operator's sermon. The retention rule
    protects uploaded video, not a file that was never accepted as one."""
    async with client_for(
        storage, probe_error=UnsupportedContainer("no media in there")
    ) as client:
        job_id = await admitted(client)

        await client.put(f"/api/jobs/{job_id}/media", content=b"plain text")

    assert not storage.source_path(job_id).exists()


async def test_a_refused_file_records_no_media(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The worker finds its input through the media record. Writing one for a
    rejected file would point it at something that is not there."""
    async with client_for(
        storage, probe_error=UnsupportedContainer("no media in there")
    ) as client:
        job_id = await admitted(client)

        await client.put(f"/api/jobs/{job_id}/media", content=b"plain text")

    with pytest.raises(Exception):
        storage.load_media(job_id)


async def test_a_refused_upload_leaves_the_job_admitted(
    storage: FakeTranscriptStoragePort,
) -> None:
    """So the operator can simply upload the right file to the same job rather
    than starting over."""
    from transcribe.domain.jobs import JobState

    async with client_for(
        storage, probe_error=UnsupportedContainer("no media in there")
    ) as client:
        job_id = await admitted(client)

        await client.put(f"/api/jobs/{job_id}/media", content=b"plain text")

    assert storage.load_job(job_id).state is JobState.PENDING


async def test_an_extensionless_filename_is_accepted_when_the_bytes_are_media(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """The other half of not trusting extensions: a real recording that nobody
    named properly is still a real recording."""
    job_id = await admitted(client)

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"pretend-media",
        headers={"x-filename": "predicacion-sin-extension"},
    )

    assert response.status_code == 204
    assert storage.load_media(job_id).container == "mov,mp4,m4a"
