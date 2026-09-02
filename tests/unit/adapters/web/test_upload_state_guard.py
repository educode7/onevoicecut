"""Upload is only legal while the job is still PENDING.

The cancel route created this problem. Until it existed, a job could not leave
PENDING while its owner was still uploading — now it can, and an upload that
finishes into a cancelled job would resurrect it: bytes on disk, a media record,
and a worker spawned for work the operator explicitly stopped.

Two checks, because a multi-hour upload gives the state plenty of time to change
underneath it. The early one refuses before a single byte is accepted. The late
one re-reads immediately before the record is written, and throws the stored
bytes away through the same `discard` seam a rejected container already uses.

The guard also closes something older: re-uploading over a job that is already
extracting used to be accepted, and it silently replaced the file a worker was
reading at that moment.
"""

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.domain.errors import JobNotFound
from onevoicecut.domain.ids import JobId, make_job_id
from onevoicecut.domain.jobs import JobState
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    TOKEN_A,
    accepting_extractor,
    auth_headers,
    fake_authenticate,
)


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


@pytest.fixture
def started() -> list[JobId]:
    return []


@pytest.fixture
async def client(
    storage: FakeTranscriptStoragePort, started: list[JobId]
) -> AsyncIterator[AsyncClient]:
    app = create_app(
        WebDependencies(
            storage=storage,
            authenticate=fake_authenticate,
            extractor_for=accepting_extractor,
            start_job=started.append,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def admitted(client: AsyncClient) -> JobId:
    response = await client.post(
        "/api/jobs", json={"engine": "local"}, headers=auth_headers(TOKEN_A)
    )
    return make_job_id(response.json()["job_id"])


def parked(
    storage: FakeTranscriptStoragePort, job_id: JobId, state: JobState
) -> None:
    storage.update_job(replace(storage.load_job(job_id), state=state))
    storage.calls.clear()


def nothing_on_disk(storage: FakeTranscriptStoragePort, job_id: JobId) -> bool:
    """No stored source and no partial file — the upload left no trace."""
    return not storage.source_path(job_id).exists() and not list(
        storage.job_dir(job_id).glob("*.part")
    )


@pytest.mark.parametrize(
    "state",
    [
        pytest.param(JobState.CANCELLED, id="cancelled"),
        pytest.param(JobState.QUEUED, id="queued"),
        pytest.param(JobState.EXTRACTING, id="extracting"),
        pytest.param(JobState.TRANSCRIBING, id="transcribing"),
        pytest.param(JobState.COMPLETED, id="completed"),
    ],
)
async def test_upload_to_a_job_that_left_pending_is_refused_early(
    client: AsyncClient,
    storage: FakeTranscriptStoragePort,
    started: list[JobId],
    state: JobState,
) -> None:
    """409 before the writer exists, so no byte is ever accepted.

    Refusing after the transfer would be correct and useless: the operator would
    have spent an hour uploading a file that was never going to be kept.
    """
    job_id = await admitted(client)
    parked(storage, job_id, state)

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"bytes that must never land",
        headers=auth_headers(TOKEN_A),
    )

    assert response.status_code == 409
    assert nothing_on_disk(storage, job_id)
    assert storage.calls == []
    assert started == []


async def test_the_refusal_names_the_state_that_caused_it(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """The operator needs to know *why*, because the two causes have different
    answers: a cancelled job needs re-admitting, a transcribing one needs
    leaving alone."""
    job_id = await admitted(client)
    parked(storage, job_id, JobState.CANCELLED)

    response = await client.put(
        f"/api/jobs/{job_id}/media", content=b"x", headers=auth_headers(TOKEN_A)
    )

    assert "cancelled" in response.json()["detail"]


async def test_a_job_cancelled_mid_stream_has_its_bytes_discarded(
    client: AsyncClient, storage: FakeTranscriptStoragePort, started: list[JobId]
) -> None:
    """The late re-read: the state changed while the body was in flight.

    Without it the upload commits, `save_media` records a source for a cancelled
    job, and `start_job` spawns a worker for work that was called off — the one
    outcome cancellation exists to prevent.
    """
    job_id = await admitted(client)

    async def cancelled_halfway() -> AsyncIterator[bytes]:
        yield b"first half of the sermon"
        storage.update_job(
            replace(storage.load_job(job_id), state=JobState.CANCELLED)
        )
        yield b"second half of the sermon"

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content=cancelled_halfway(),
        headers=auth_headers(TOKEN_A),
    )

    assert response.status_code == 409
    assert nothing_on_disk(storage, job_id)
    assert started == []


async def test_a_job_cancelled_mid_stream_records_no_media(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """A media record pointing at bytes that were just deleted is worse than
    none: the worker would read the record, find nothing, and fail hours later
    with a message about a missing file rather than a cancellation."""
    job_id = await admitted(client)

    async def cancelled_halfway() -> AsyncIterator[bytes]:
        yield b"first half"
        storage.update_job(
            replace(storage.load_job(job_id), state=JobState.CANCELLED)
        )
        yield b"second half"

    await client.put(
        f"/api/jobs/{job_id}/media",
        content=cancelled_halfway(),
        headers=auth_headers(TOKEN_A),
    )

    with pytest.raises(JobNotFound):
        storage.load_media(job_id)


async def test_the_ordinary_pending_upload_still_works(
    client: AsyncClient, storage: FakeTranscriptStoragePort, started: list[JobId]
) -> None:
    """The guard must refuse the illegal cases without closing the legal one.

    A check that rejected everything would satisfy every assertion above and
    break the only path the product actually needs.
    """
    job_id = await admitted(client)

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"hola mundo",
        headers=auth_headers(TOKEN_A),
    )

    assert response.status_code == 204
    assert storage.source_path(job_id).read_bytes() == b"hola mundo"
    assert started == [job_id]


async def test_ownership_is_still_decided_before_the_state(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """A stranger uploading to a cancelled job gets 403, not 409.

    The state of a job is information about it. Answering 409 would tell a
    non-owner what happened to somebody else's work, and the ownership refusal
    is meant to come first.
    """
    job_id = await admitted(client)
    parked(storage, job_id, JobState.CANCELLED)

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"x",
        headers={"authorization": "Bearer test-token-for-operator-b"},
    )

    assert response.status_code == 403
