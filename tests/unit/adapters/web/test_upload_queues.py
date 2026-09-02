"""Upload queues the job. It never spawns one.

This replaces the old contract, where a completed upload called the starter
directly. That gave the system two places capable of starting work — the upload
handler and, later, the drain supervisor — and two concurrent uploads could each
derive "a slot is free" and each spawn, putting the machine over its cap with no
single line of code to blame.

So the upload's entire contribution to starting work is one record write:
PENDING → QUEUED. The supervisor is the only code that ever calls a launcher.
The cost is up to one sweep of latency before a worker starts, which against a
three-hour job is noise — and QUEUED is an honest thing to show on the shared
board meanwhile.

What survives unchanged is everything about *not* queueing: an upload that was
too large, was not media, or had no audio stream must leave the job exactly where
it was, because a queued job is a job a supervisor will pick up.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.domain.errors import UnsupportedContainer
from onevoicecut.domain.ids import JobId, make_job_id
from onevoicecut.domain.jobs import JobState
from onevoicecut.domain.media import MediaProbe
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    accepting_extractor,
    auth_headers,
    fake_authenticate,
)

LIMIT = 4096


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


@pytest.fixture
def launched() -> list[JobId]:
    """Anything the upload path might spawn. It must stay empty forever."""
    return []


def client_for(
    storage: FakeTranscriptStoragePort,
    launched: list[JobId],
    *,
    probe_error: Exception | None = None,
    probe_result: MediaProbe | None = None,
) -> AsyncClient:
    def extractor(_: TranscriptStoragePort, job_id: JobId) -> AudioExtractorPort:
        return FakeAudioExtractorPort(
            job_id, probe_error=probe_error, probe_result=probe_result
        )

    app = create_app(
        WebDependencies(
            storage=storage,
            authenticate=fake_authenticate,
            max_upload_bytes=LIMIT,
            extractor_for=(
                extractor if (probe_error or probe_result) else accepting_extractor
            ),
            start_job=launched.append,
        )
    )
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=auth_headers()
    )


@pytest.fixture
async def client(
    storage: FakeTranscriptStoragePort, launched: list[JobId]
) -> AsyncIterator[AsyncClient]:
    async with client_for(storage, launched) as http:
        yield http


async def admitted(client: AsyncClient) -> JobId:
    response = await client.post("/api/jobs", json={"engine": "local"})
    return make_job_id(response.json()["job_id"])


class TestASuccessfulUploadQueues:
    async def test_the_record_reads_queued(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        """CAP-01: admission never refuses for capacity, it queues instead.

        A 429 would be a possibly day-long "come back later" for a job the
        operator has already spent an hour uploading.
        """
        job_id = await admitted(client)

        response = await client.put(f"/api/jobs/{job_id}/media", content=b"media")

        assert response.status_code == 204
        assert storage.load_job(job_id).state is JobState.QUEUED

    async def test_nothing_is_spawned(
        self, client: AsyncClient, launched: list[JobId]
    ) -> None:
        """The single-spawn-decision-point rule, asserted where it is easiest to
        break: a future maintainer adding "just start it if a slot is free" here
        reintroduces the whole race class."""
        job_id = await admitted(client)

        await client.put(f"/api/jobs/{job_id}/media", content=b"media")

        assert launched == []

    async def test_the_media_is_described_before_the_record_says_queued(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        """QUEUED is what makes a supervisor spawn a worker, and that worker's
        first act is to read the media record. Writing QUEUED first would be a
        race against the process still describing the upload."""
        job_id = await admitted(client)
        storage.calls.clear()

        await client.put(f"/api/jobs/{job_id}/media", content=b"media")

        assert storage.calls == ["save_media", "update_job:queued"]

    async def test_the_queue_write_is_the_only_record_write(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        """CAP-11 write side: one gate write, before any spawn, and then the web
        process is done touching this record until a worker owns it."""
        job_id = await admitted(client)
        storage.calls.clear()

        await client.put(f"/api/jobs/{job_id}/media", content=b"media")

        assert [c for c in storage.calls if c.startswith("update_job")] == [
            "update_job:queued"
        ]

    async def test_the_owner_is_carried_through_unchanged(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        """OWN-02: no transition reassigns ownership, including this one."""
        job_id = await admitted(client)
        owner_before = storage.load_job(job_id).owner

        await client.put(f"/api/jobs/{job_id}/media", content=b"media")

        assert storage.load_job(job_id).owner == owner_before

    async def test_a_queued_job_has_no_worker_pid(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        """What makes the web process's write legitimate under the single-writer
        rule: QUEUED means no worker exists, by construction."""
        job_id = await admitted(client)

        await client.put(f"/api/jobs/{job_id}/media", content=b"media")

        assert storage.load_job(job_id).worker_pid is None


class TestWhatMustNotQueue:
    """A queued job is one a supervisor will pick up, so a refused upload that
    left the record QUEUED would spawn a worker for a file that is not there."""

    async def test_admission_alone_does_not(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        """CAP-02: there is no media yet. Queueing here would spawn a worker
        with nothing to read."""
        job_id = await admitted(client)

        assert storage.load_job(job_id).state is JobState.PENDING

    async def test_an_oversized_upload_does_not(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        job_id = await admitted(client)

        await client.put(f"/api/jobs/{job_id}/media", content=b"x" * (LIMIT + 1))

        assert storage.load_job(job_id).state is JobState.PENDING

    async def test_a_file_that_is_not_media_does_not(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        async with client_for(
            storage, launched, probe_error=UnsupportedContainer("not media")
        ) as client:
            job_id = await admitted(client)
            await client.put(f"/api/jobs/{job_id}/media", content=b"plain text")

        assert storage.load_job(job_id).state is JobState.PENDING

    async def test_a_video_with_no_audio_does_not(
        self, storage: FakeTranscriptStoragePort, launched: list[JobId]
    ) -> None:
        """It extracts cleanly to a silent track and transcribes to an empty
        sermon — the failure that looks like success."""
        silent = MediaProbe(duration_s=3600.0, container="mov,mp4,m4a", has_audio=False)
        async with client_for(storage, launched, probe_result=silent) as client:
            job_id = await admitted(client)
            await client.put(f"/api/jobs/{job_id}/media", content=b"video only")

        assert storage.load_job(job_id).state is JobState.PENDING

    async def test_an_upload_to_an_unknown_job_queues_nothing(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        await client.put(
            "/api/jobs/01HQ3M8XKJ7VNPQR2ZYWB4TCFF/media", content=b"media"
        )

        assert storage.list_jobs() == ()
