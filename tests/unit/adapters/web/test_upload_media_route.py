"""Uploading the sermon: raw body to disk, over HTTP.

The route is thin, so most of what matters was already proven against the writer
directly. What is proven here is the wiring — that the request body reaches the
writer as a stream rather than as a materialised body, and that the media record
lands where a worker in another process will look for it.

The structural test at the bottom carries a claim no request-level test can: that
no multipart path exists to fall back to.
"""

import ast
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

import onevoicecut.adapters.web as web_package
from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.domain.ids import JobId, make_job_id
from onevoicecut.domain.jobs import JobState
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    accepting_extractor,
    auth_headers,
    fake_authenticate,
    unstarted,
)

FORBIDDEN_HELPERS = {"UploadFile", "File", "Form"}


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


@pytest.fixture
async def client(storage: FakeTranscriptStoragePort) -> AsyncIterator[AsyncClient]:
    app = create_app(
        WebDependencies(
            storage=storage,
            authenticate=fake_authenticate,
            max_upload_bytes=4096,
            # The route probes what it stored. Whether ffprobe agrees is a
            # separate claim, proven in the integration tests.
            extractor_for=accepting_extractor,
            start_job=unstarted,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=auth_headers()
    ) as http:
        yield http


async def admitted(client: AsyncClient) -> JobId:
    """Going through `make_job_id` also checks the route returned a usable id."""
    response = await client.post("/api/jobs", json={"engine": "local"})
    return make_job_id(response.json()["job_id"])


async def test_an_upload_is_accepted(client: AsyncClient) -> None:
    job_id = await admitted(client)

    response = await client.put(f"/api/jobs/{job_id}/media", content=b"hola mundo")

    assert response.status_code == 204


async def test_the_bytes_land_where_storage_says(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    job_id = await admitted(client)

    await client.put(f"/api/jobs/{job_id}/media", content=b"hola mundo")

    assert storage.source_path(job_id).read_bytes() == b"hola mundo"


async def test_the_media_record_is_persisted_for_the_worker(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """The worker runs in another process hours later and rebuilds `SourceMedia`
    from this. Without it the upload would be bytes nobody can attribute."""
    job_id = await admitted(client)

    await client.put(f"/api/jobs/{job_id}/media", content=b"hola mundo")

    media = storage.load_media(job_id)
    assert media.size_bytes == 10
    assert media.media_id == storage.load_job(job_id).media_id


async def test_an_accented_filename_survives_the_header(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """HTTP header values are ASCII and the source language is not. Spanish
    filenames are the ordinary case here, so they travel percent-encoded."""
    job_id = await admitted(client)

    await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"x",
        headers={"x-filename": quote("predicación del domingo.mp4")},
    )

    assert storage.load_media(job_id).original_filename == (
        "predicación del domingo.mp4"
    )


async def test_a_plain_ascii_filename_needs_no_encoding(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """Decoding is a no-op for a name with nothing to decode, so a simple client
    that sends one unencoded still works."""
    job_id = await admitted(client)

    await client.put(
        f"/api/jobs/{job_id}/media", content=b"x", headers={"x-filename": "sermon.mp4"}
    )

    assert storage.load_media(job_id).original_filename == "sermon.mp4"


async def test_a_hostile_filename_does_not_move_the_file(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """It is a header, not a path component, and the destination was chosen
    before the request was read."""
    job_id = await admitted(client)

    await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"x",
        headers={"x-filename": "../../etc/passwd"},
    )

    assert storage.source_path(job_id).read_bytes() == b"x"
    assert storage.load_media(job_id).stored_path == storage.source_path(job_id)


async def test_uploading_to_an_unknown_job_is_a_404(client: AsyncClient) -> None:
    response = await client.put(
        "/api/jobs/01HQ3M8XKJ7VNPQR2ZYWB4TCFD/media", content=b"x"
    )

    assert response.status_code == 404


async def test_a_job_id_that_is_not_a_ulid_is_a_404(client: AsyncClient) -> None:
    """It refers to no job, and answering differently would tell a caller which
    ids exist."""
    response = await client.put("/api/jobs/not-an-id/media", content=b"x")

    assert response.status_code == 404


async def test_an_upload_past_the_limit_is_refused(client: AsyncClient) -> None:
    job_id = await admitted(client)

    response = await client.put(f"/api/jobs/{job_id}/media", content=b"x" * 5000)

    assert response.status_code == 413


async def test_uploading_starts_no_transcription(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """The request returns as soon as the bytes are stored and the worker is
    handed off to. Hours of ASR happen in that other process, never inside this
    response — nothing is planned or transcribed by the time it returns."""
    job_id = await admitted(client)

    await client.put(f"/api/jobs/{job_id}/media", content=b"hola")

    assert storage.load_job(job_id).state is JobState.PENDING
    assert storage.load_chunk_plan(job_id) is None
    assert storage.load_transcript(job_id) is None


async def test_a_chunked_body_arrives_intact(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """What a browser actually sends for a large file: no Content-Length, a
    chunked transfer encoding, and the body split across many reads."""

    parts = [bytes([index]) * 256 for index in range(4)]

    async def body() -> AsyncIterator[bytes]:
        for part in parts:
            yield part

    job_id = await admitted(client)

    response = await client.put(f"/api/jobs/{job_id}/media", content=body())

    assert response.status_code == 204
    assert storage.source_path(job_id).read_bytes() == b"".join(parts)


def test_no_multipart_path_exists_anywhere_in_the_web_adapter() -> None:
    """`UploadFile` spools the whole body before a handler sees it, which on a
    multi-hour sermon means writing the file twice or running out of disk. The
    only defence that holds is that the machinery is not imported at all — a
    request-level test cannot prove an absence."""
    package_root = Path(web_package.__file__).parent

    imported: set[str] = set()
    for module in package_root.rglob("*.py"):
        tree = ast.parse(module.read_text(encoding="utf-8"), filename=str(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

    assert FORBIDDEN_HELPERS.isdisjoint(imported)


def test_the_upload_route_reads_the_request_as_a_stream() -> None:
    """Pins the mechanism, not just its absence: `request.stream()` is what makes
    the body arrive in pieces instead of as one object."""
    module = Path(web_package.__file__).parent / "routers" / "jobs.py"

    assert "request.stream()" in module.read_text(encoding="utf-8")
