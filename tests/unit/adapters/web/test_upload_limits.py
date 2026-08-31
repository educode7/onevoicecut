"""Two size checks, because one of them can be lied to.

`Content-Length` is a claim by the client. Trusting it alone means a header saying
"1 KB" can deliver sixteen gigabytes; ignoring it means reading sixteen gigabytes
before deciding not to want them. So both: reject on the claim when the claim is
already too big, and keep counting in case it was false.

The second check is the one that has to clean up after itself, because by the time
it fires there is a partial sermon on disk.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from transcribe.adapters.web.app import WebDependencies, create_app
from transcribe.domain.ids import JobId, make_job_id
from transcribe.ports.media_source import MediaSourcePort
from transcribe.ports.transcript_storage import TranscriptStoragePort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import accepting_extractor, unstarted

LIMIT = 4096


class RefusingMediaSource:
    """Fails the test if the writer is reached at all.

    The precheck's whole value is that nothing downstream runs, and "nothing ran"
    is only observable by making the next thing explode.
    """

    async def store(
        self,
        media_id: object,
        filename: str,
        stream: AsyncIterator[bytes],
        max_bytes: int,
    ) -> object:
        raise AssertionError("the writer was reached despite an oversized header")


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


@pytest.fixture
async def client(storage: FakeTranscriptStoragePort) -> AsyncIterator[AsyncClient]:
    app = create_app(
        WebDependencies(
            storage=storage,
            max_upload_bytes=LIMIT,
            extractor_for=accepting_extractor,
         start_job=unstarted,)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


@pytest.fixture
async def guarded_client(
    storage: FakeTranscriptStoragePort,
) -> AsyncIterator[AsyncClient]:
    def refuse(_: TranscriptStoragePort, __: JobId) -> MediaSourcePort:
        return RefusingMediaSource()  # type: ignore[return-value]

    app = create_app(
        WebDependencies(
            storage=storage,
            max_upload_bytes=LIMIT,
            media_source_for=refuse,
            extractor_for=accepting_extractor,
         start_job=unstarted,)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def admitted(client: AsyncClient) -> JobId:
    response = await client.post("/api/jobs", json={"engine": "local"})
    return make_job_id(response.json()["job_id"])


async def test_an_honestly_oversized_upload_is_refused_before_it_is_read(
    guarded_client: AsyncClient,
) -> None:
    """Declared too large, so nothing is read and no file is opened. On a
    sixteen-gigabyte mistake this is the difference between an instant answer and
    an hour of wasted transfer."""
    job_id = await admitted(guarded_client)

    response = await guarded_client.put(
        f"/api/jobs/{job_id}/media", content=b"x" * (LIMIT + 1)
    )

    assert response.status_code == 413


async def test_an_upload_exactly_at_the_limit_is_accepted(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """The boundary belongs on the allowed side: a limit of N means N is fine."""
    job_id = await admitted(client)

    response = await client.put(f"/api/jobs/{job_id}/media", content=b"x" * LIMIT)

    assert response.status_code == 204
    assert storage.load_media(job_id).size_bytes == LIMIT


async def test_a_lying_content_length_is_still_caught(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """The header says it fits; the body does not. Only a running counter over the
    bytes actually received can tell."""

    async def oversized_body() -> AsyncIterator[bytes]:
        for _ in range(8):
            yield b"x" * 1024

    job_id = await admitted(client)

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content=oversized_body(),
        headers={"content-length": "10"},
    )

    assert response.status_code == 413


async def test_a_body_with_no_declared_length_is_still_limited(
    client: AsyncClient,
) -> None:
    """Chunked transfer encoding sends no `Content-Length` at all, which is what a
    browser does for a large file. The precheck simply has nothing to check."""

    async def endless() -> AsyncIterator[bytes]:
        for _ in range(100):
            yield b"x" * 1024

    job_id = await admitted(client)

    response = await client.put(f"/api/jobs/{job_id}/media", content=endless())

    assert response.status_code == 413


async def test_a_refused_upload_leaves_nothing_on_disk(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """A truncated sermon often still probes as valid media, so a leftover would
    be transcribed as though it were the whole service."""

    async def oversized_body() -> AsyncIterator[bytes]:
        for _ in range(8):
            yield b"x" * 1024

    job_id = await admitted(client)

    await client.put(f"/api/jobs/{job_id}/media", content=oversized_body())

    assert not storage.source_path(job_id).exists()
    assert list(storage.job_dir(job_id).glob("*.part")) == []


async def test_a_refused_upload_records_no_media(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """The worker reads the media record to find its input. Recording one for an
    upload that never completed would point it at a file that is not there."""
    job_id = await admitted(client)

    await client.put(f"/api/jobs/{job_id}/media", content=b"x" * (LIMIT + 1))

    with pytest.raises(Exception):
        storage.load_media(job_id)


async def test_a_malformed_content_length_does_not_bypass_the_limit(
    client: AsyncClient,
) -> None:
    """An unparseable header is not a small one. It simply tells us nothing, and
    the running counter still applies."""

    async def oversized_body() -> AsyncIterator[bytes]:
        for _ in range(8):
            yield b"x" * 1024

    job_id = await admitted(client)

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content=oversized_body(),
        headers={"content-length": "not-a-number"},
    )

    assert response.status_code in (400, 413)
