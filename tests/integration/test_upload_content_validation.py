"""Does ffprobe actually reject the things we assume it rejects?

The unit tests drive a fake whose answer is configured, which proves the route
does the right thing *given* an answer. It cannot prove the answer. These run the
real binary, because "a text file named sermon.mp4 is refused" is a claim about
ffmpeg's behaviour and nothing else.

The video-only case is the one worth the fixture cost. It is the rejection that
looks like success everywhere else: a real container, a real duration, and no
audio to transcribe.
"""

import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from transcribe.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from transcribe.adapters.web.app import WebDependencies, create_app
from transcribe.domain.ids import JobId, make_job_id

pytestmark = pytest.mark.integration


def unstarted(job_id: JobId) -> None:
    """No worker here. These tests are about what ffprobe decides, and spawning a
    process would make them about something else."""


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemTranscriptStorage:
    return FilesystemTranscriptStorage(tmp_path)


@pytest.fixture
async def client(
    storage: FilesystemTranscriptStorage,
) -> AsyncIterator[AsyncClient]:
    """No fake extractor: this is the point of the file."""
    app = create_app(WebDependencies(storage=storage, start_job=unstarted))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def admitted(client: AsyncClient) -> JobId:
    response = await client.post("/api/jobs", json={"engine": "local"})
    return make_job_id(response.json()["job_id"])


def synthesize(path: Path, *, args: list[str]) -> bytes:
    """Generated with `-f lavfi`, never checked in.

    `.gitignore` excludes media, so a committed fixture would look present and
    silently never run — the deviation already recorded for slice 3a.
    """
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", *args, str(path)],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return path.read_bytes()


async def test_a_text_file_named_mp4_is_refused(
    client: AsyncClient, ffmpeg_available: None
) -> None:
    """The extension claims media; the bytes are a shopping list."""
    job_id = await admitted(client)

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content="esto no es un video".encode(),
        headers={"x-filename": "sermon.mp4"},
    )

    assert response.status_code == 415


async def test_real_media_is_accepted_and_its_container_recorded(
    client: AsyncClient,
    storage: FilesystemTranscriptStorage,
    tmp_path: Path,
    ffmpeg_available: None,
) -> None:
    payload = synthesize(
        tmp_path / "tone.mp4",
        args=[
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-shortest", "-c:a", "aac", "-c:v", "mpeg4", "-y",
        ],
    )
    job_id = await admitted(client)

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content=payload,
        # Percent-encoded because header values are ASCII — the same constraint
        # that shaped the route, exercised here against a real filename.
        headers={"x-filename": quote("predicación.mp4")},
    )

    assert response.status_code == 204
    assert storage.load_media(job_id).container != "unverified"


async def test_a_video_with_no_audio_track_is_refused(
    client: AsyncClient,
    storage: FilesystemTranscriptStorage,
    tmp_path: Path,
    ffmpeg_available: None,
) -> None:
    """Real media, real duration, nothing to transcribe. Accepting it would
    produce an empty sermon and no explanation."""
    payload = synthesize(
        tmp_path / "silent.mp4",
        args=[
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-c:v", "mpeg4", "-y",
        ],
    )
    job_id = await admitted(client)

    response = await client.put(f"/api/jobs/{job_id}/media", content=payload)

    assert response.status_code == 415
    assert not storage.source_path(job_id).exists()
