"""Polling a job that will run for hours.

"running" is not a status for a three-hour job — it is the same answer at minute
two and minute one hundred and eighty. So the route reports chunk-level progress
derived from what is on disk, and an ETA only once there is something to
extrapolate from.

Everything here is read-only by construction. The worker owns the job record; this
route reads it, reads the plan, counts the results, and computes. There is nothing
to race against because nothing is written.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from transcribe.adapters.web.app import WebDependencies, create_app
from transcribe.domain.chunking import ChunkPlan, ChunkResult, ChunkState, PlannedChunk
from transcribe.domain.ids import JobId, make_job_id, make_media_id
from transcribe.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from transcribe.domain.transcript import SegmentKind, TranscriptSegment
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import accepting_extractor

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
CREATED_AT = 1000.0
NOW = CREATED_AT + 600.0


def a_job(state: JobState = JobState.TRANSCRIBING) -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=state,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=CREATED_AT,
        updated_at=CREATED_AT,
        worker_pid=4812,
        error=None,
    )


def a_plan(count: int) -> ChunkPlan:
    return ChunkPlan(
        job_id=JOB_ID,
        stride_s=600.0,
        overlap_s=5.0,
        chunks=tuple(
            PlannedChunk(index=i, start_s=i * 600.0, end_s=(i + 1) * 600.0)
            for i in range(count)
        ),
    )


def a_result(index: int, state: ChunkState = ChunkState.DONE) -> ChunkResult:
    return ChunkResult(
        job_id=JOB_ID,
        index=index,
        state=state,
        segments=(
            TranscriptSegment(
                start_s=0.0,
                end_s=1.0,
                text="hola",
                speaker=None,
                confidence=0.9,
                kind=SegmentKind.SPEECH,
            ),
        ),
        engine_id="fake-asr",
        attempts=1,
        error=None,
        finished_at=NOW,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    store = FakeTranscriptStoragePort(tmp_path)
    store.create_job(a_job())
    return store


@pytest.fixture
async def client(storage: FakeTranscriptStoragePort) -> AsyncIterator[AsyncClient]:
    app = create_app(
        WebDependencies(
            storage=storage, now=lambda: NOW, extractor_for=accepting_extractor
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def status_of(client: AsyncClient, job_id: JobId = JOB_ID) -> dict[str, Any]:
    response = await client.get(f"/api/jobs/{job_id}")
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    return payload


async def test_the_status_reports_the_job_state(client: AsyncClient) -> None:
    assert (await status_of(client))["state"] == "transcribing"


async def test_the_status_reports_the_choices_made_at_admission(
    client: AsyncClient,
) -> None:
    """The operator picked these hours ago and the worker is acting on them. Not
    surfacing them means no way to tell a cloud job from a local one while it
    runs."""
    status = await status_of(client)

    assert status["engine"] == "local"
    assert status["speaker_mode"] == "single"


async def test_an_unplanned_job_has_no_progress_to_report(
    client: AsyncClient,
) -> None:
    """`null`, not zero of zero — which would render as a finished job."""
    assert (await status_of(client))["progress"] is None


async def test_progress_is_counted_from_what_is_on_disk(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    storage.save_chunk_plan(JOB_ID, a_plan(87))
    for index in range(10):
        storage.save_chunk_result(a_result(index))

    progress = (await status_of(client))["progress"]

    assert progress["chunks_total"] == 87
    assert progress["chunks_done"] == 10
    assert progress["chunks_remaining"] == 77


async def test_there_is_no_eta_before_the_first_chunk_finishes(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """A number from zero samples is a fabrication, and the operator plans their
    evening around it."""
    storage.save_chunk_plan(JOB_ID, a_plan(87))

    assert (await status_of(client))["progress"]["eta_s"] is None


async def test_the_eta_appears_once_there_is_something_to_extrapolate(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    storage.save_chunk_plan(JOB_ID, a_plan(87))
    for index in range(10):
        storage.save_chunk_result(a_result(index))

    assert (await status_of(client))["progress"]["eta_s"] == pytest.approx(77 * 60.0)


async def test_failed_chunks_are_reported_separately(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """Folding them into "done" would report a job as complete while a quarter of
    the sermon is missing."""
    storage.save_chunk_plan(JOB_ID, a_plan(3))
    storage.save_chunk_result(a_result(0))
    storage.save_chunk_result(a_result(1, ChunkState.FAILED))

    progress = (await status_of(client))["progress"]

    assert (progress["chunks_done"], progress["chunks_failed"]) == (1, 1)


async def test_a_failed_job_surfaces_its_reason(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """Otherwise the operator sees "failed" against three hours of audio and has
    nowhere to start."""
    from dataclasses import replace

    storage.update_job(
        replace(a_job(JobState.FAILED), error="2 of 87 chunks failed: 41, 62")
    )

    status = await status_of(client)

    assert status["state"] == "failed"
    assert "41" in status["error"]


async def test_polling_writes_nothing(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """The worker is the sole writer of the job record. A status route that
    touched it would be the other half of the race the design exists to avoid."""
    storage.calls.clear()

    await status_of(client)

    assert storage.calls == []


async def test_an_unknown_job_is_a_404(client: AsyncClient) -> None:
    response = await client.get("/api/jobs/01HQ3M8XKJ7VNPQR2ZYWB4TCFF")

    assert response.status_code == 404


async def test_a_malformed_id_is_a_404_too(client: AsyncClient) -> None:
    """Same answer as unknown, so the store never reveals which ids exist."""
    response = await client.get("/api/jobs/not-a-ulid")

    assert response.status_code == 404
