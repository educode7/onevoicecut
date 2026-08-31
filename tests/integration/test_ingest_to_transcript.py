"""Upload a real file over HTTP, poll it, and read the transcript off disk.

The one test where almost nothing is a double. Real HTTP through the ASGI stack,
real filesystem storage, real ffmpeg extracting and slicing real audio, real JSON
written and read back by different code. Only the ASR engine is fake, because a
default suite that downloaded model weights would stop being run — and what the
engine says is not what this test is about.

Every piece here has passed its own tests. What has never been exercised is the
seam between them: that the id the route mints is the one the worker loads, that
the file the writer stored is the one ffmpeg opens, that the plan the worker
persists is the one the status route counts against.
"""

import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.domain.ids import JobId, make_job_id
from onevoicecut.domain.jobs import EngineChoice, JobState
from onevoicecut.runtime.engine_resolver import EngineResolver
from onevoicecut.runtime.worker import run_job
from tests.fakes.transcription import FakeTranscriptionPort

pytestmark = pytest.mark.integration

SERMON_SECONDS = 3


@pytest.fixture
def sermon(tmp_path: Path, ffmpeg_available: None) -> bytes:
    """A real container with a real audio stream, synthesized rather than
    committed — `.gitignore` excludes media, so a checked-in fixture would look
    present and silently never run."""
    path = tmp_path / "sermon.mp4"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={SERMON_SECONDS}",
            "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={SERMON_SECONDS}",
            "-shortest", "-c:a", "aac", "-c:v", "mpeg4", "-y", str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return path.read_bytes()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
async def client(data_dir: Path) -> AsyncIterator[AsyncClient]:
    """The worker runs in-process and synchronously.

    A real subprocess is what production does and what `spawn_worker` builds; here
    it would make the test poll and sleep for no extra coverage — the worker's own
    command-line entry point is already proven in `test_worker_entrypoint.py`.
    What matters is that the same `run_job` sees what the web process wrote.
    """
    storage = FilesystemTranscriptStorage(data_dir)

    def run_worker_now(job_id: JobId) -> None:
        run_job(
            job_id,
            data_dir,
            resolver=EngineResolver({EngineChoice.LOCAL: FakeTranscriptionPort}),
        )

    app = create_app(WebDependencies(storage=storage, start_job=run_worker_now))
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def ingest(client: AsyncClient, sermon: bytes) -> JobId:
    admitted = await client.post("/api/jobs", json={"engine": "local"})
    assert admitted.status_code == 201
    job_id = make_job_id(admitted.json()["job_id"])

    uploaded = await client.put(
        f"/api/jobs/{job_id}/media",
        content=sermon,
        headers={"x-filename": quote("predicación del domingo.mp4")},
    )
    assert uploaded.status_code == 204
    return job_id


async def status_of(client: AsyncClient, job_id: JobId) -> dict[str, Any]:
    response = await client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    return payload


async def test_a_sermon_uploaded_over_http_becomes_a_transcript(
    client: AsyncClient, sermon: bytes, data_dir: Path
) -> None:
    job_id = await ingest(client, sermon)

    assert (await status_of(client, job_id))["state"] == JobState.COMPLETED

    exported = FilesystemTranscriptStorage(data_dir).job_dir(job_id) / "transcript.txt"
    assert exported.read_text(encoding="utf-8") == "hola mundo"


async def test_the_status_route_counts_the_plan_the_worker_persisted(
    client: AsyncClient, sermon: bytes
) -> None:
    """Two processes agreeing about the same file. The worker wrote the plan; the
    route counted results against it without either knowing about the other."""
    job_id = await ingest(client, sermon)

    progress = (await status_of(client, job_id))["progress"]

    assert progress["chunks_total"] >= 1
    assert progress["chunks_done"] == progress["chunks_total"]
    assert progress["chunks_remaining"] == 0


async def test_ffmpeg_really_extracted_and_sliced_the_upload(
    client: AsyncClient, sermon: bytes, data_dir: Path
) -> None:
    """Not a fake anywhere on this path: the bytes the route stored are the bytes
    ffmpeg opened, and it produced a normalized track and a chunk from them."""
    job_id = await ingest(client, sermon)

    job_dir = FilesystemTranscriptStorage(data_dir).job_dir(job_id)
    assert (job_dir / "audio.flac").stat().st_size > 0
    assert (job_dir / "chunks" / "0000.flac").stat().st_size > 0


async def test_the_id_the_route_minted_is_the_one_the_worker_used(
    client: AsyncClient, sermon: bytes, data_dir: Path
) -> None:
    """The seam no unit test can cover: a ULID generated in one process, written
    into a path, and read back by another."""
    job_id = await ingest(client, sermon)

    storage = FilesystemTranscriptStorage(data_dir)
    transcript = storage.load_transcript(job_id)
    assert transcript is not None
    assert transcript.job_id == job_id


async def test_the_accented_filename_survived_the_whole_round_trip(
    client: AsyncClient, sermon: bytes, data_dir: Path
) -> None:
    """Percent-encoded through a header, decoded, written as JSON, read back by
    the worker's process."""
    job_id = await ingest(client, sermon)

    media = FilesystemTranscriptStorage(data_dir).load_media(job_id)
    assert media.original_filename == "predicación del domingo.mp4"
    assert media.container != "unverified"


async def test_the_finished_job_leaves_no_working_files(
    client: AsyncClient, sermon: bytes, data_dir: Path
) -> None:
    """No `.part` from the upload, no `.tmp` from any commit. Both would be
    invisible until a disk filled up."""
    job_id = await ingest(client, sermon)

    job_dir = FilesystemTranscriptStorage(data_dir).job_dir(job_id)
    assert list(job_dir.rglob("*.part")) == []
    assert list(job_dir.rglob("*.tmp")) == []


async def test_a_second_sermon_is_a_separate_job(
    client: AsyncClient, sermon: bytes, data_dir: Path
) -> None:
    """Per-job isolation, end to end rather than against a dict."""
    first = await ingest(client, sermon)
    second = await ingest(client, sermon)

    storage = FilesystemTranscriptStorage(data_dir)
    assert first != second
    assert storage.load_transcript(first) is not None
    assert storage.load_transcript(second) is not None
    assert {job.job_id for job in storage.list_jobs()} == {first, second}
