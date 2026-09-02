"""`POST /api/jobs/{id}/cancel` — the HTTP face of the cancellation use case.

The use-case tests already pin *what* gets written in each state. What is only
observable here is the shape of the answer: one status for every branch, the
running state reported honestly, and the check order that decides what an
unauthenticated or non-owning caller is allowed to learn.
"""

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.domain.ids import JobId, make_job_id
from onevoicecut.domain.jobs import JobState
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    TOKEN_A,
    TOKEN_B,
    accepting_extractor,
    auth_headers,
    fake_authenticate,
    unstarted,
)

OWNERSHIP_BODY = b'{"detail":"not the owner of this job"}'


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


@pytest.fixture
async def client(storage: FakeTranscriptStoragePort) -> AsyncIterator[AsyncClient]:
    app = create_app(
        WebDependencies(
            storage=storage,
            authenticate=fake_authenticate,
            extractor_for=accepting_extractor,
            start_job=unstarted,
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


async def running(
    client: AsyncClient, storage: FakeTranscriptStoragePort, state: JobState
) -> JobId:
    """A job parked in `state`, with the calls that put it there forgotten."""
    job_id = await admitted(client)
    storage.update_job(replace(storage.load_job(job_id), state=state))
    storage.calls.clear()
    return job_id


async def test_owner_cancelling_a_running_job_is_answered_immediately(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """CXL-01: 200 without waiting for any chunk boundary.

    The request is recorded and the answer returns. Blocking until the worker
    actually stopped would hold an HTTP request open for the length of one
    chunk — ten minutes of sermon — for no gain.
    """
    job_id = await running(client, storage, JobState.TRANSCRIBING)

    response = await client.post(
        f"/api/jobs/{job_id}/cancel", headers=auth_headers(TOKEN_A)
    )

    assert response.status_code == 200
    assert response.json() == {"job_id": job_id, "state": "transcribing"}
    assert storage.calls == ["request_cancellation:True"]


async def test_the_reported_state_is_the_one_the_record_still_carries(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """A running job comes back running, not cancelled.

    The worker has not stopped yet, and the shared board would contradict a
    premature CANCELLED on its very next poll.
    """
    job_id = await running(client, storage, JobState.STITCHING)

    response = await client.post(
        f"/api/jobs/{job_id}/cancel", headers=auth_headers(TOKEN_A)
    )

    assert response.json()["state"] == "stitching"
    assert storage.load_job(job_id).state is JobState.STITCHING


async def test_cancelling_a_job_no_worker_owns_reports_it_cancelled(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """A pending job has nobody to race, so the web process settles it here."""
    job_id = await admitted(client)

    response = await client.post(
        f"/api/jobs/{job_id}/cancel", headers=auth_headers(TOKEN_A)
    )

    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    assert storage.load_job(job_id).state is JobState.CANCELLED


async def test_cancelling_a_finished_job_succeeds_and_touches_nothing(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """CXL-06: idempotent, not 409.

    A double-click sends the second one. Answering an error would make every
    client distinguish "already done" from a genuine failure to get the outcome
    it was asking for anyway.
    """
    job_id = await running(client, storage, JobState.COMPLETED)

    response = await client.post(
        f"/api/jobs/{job_id}/cancel", headers=auth_headers(TOKEN_A)
    )

    assert response.status_code == 200
    assert response.json()["state"] == "completed"
    assert storage.calls == []


async def test_non_owner_cancellation_is_denied_with_nothing_touched(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """CXL-02: 403, no control file, record byte-identical.

    The dangerous version of this bug is silent: a stranger stopping a job that
    took three hours, with the record still reading `transcribing`.
    """
    job_id = await running(client, storage, JobState.TRANSCRIBING)
    record_before = storage.load_job(job_id)

    response = await client.post(
        f"/api/jobs/{job_id}/cancel", headers=auth_headers(TOKEN_B)
    )

    assert response.status_code == 403
    assert response.content == OWNERSHIP_BODY
    assert storage.calls == []
    assert storage.cancellation_requested(job_id) is False
    assert storage.load_job(job_id) == record_before


async def test_an_ownerless_legacy_job_is_cancellable_by_nobody(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    job_id = await admitted(client)
    storage.update_job(replace(storage.load_job(job_id), owner=None))

    response = await client.post(
        f"/api/jobs/{job_id}/cancel", headers=auth_headers(TOKEN_A)
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    "job_id",
    [
        pytest.param("not-a-ulid", id="malformed"),
        pytest.param("01ARZ3NDEKTSV4RRFFQ69G5FAV", id="well-formed-but-unknown"),
    ],
)
async def test_malformed_and_unknown_ids_are_indistinguishable(
    client: AsyncClient, job_id: str
) -> None:
    """CXL-08: both answer 404, so the route never reveals which ids exist."""
    response = await client.post(
        f"/api/jobs/{job_id}/cancel", headers=auth_headers(TOKEN_A)
    )

    assert response.status_code == 404


async def test_an_unauthenticated_cancellation_is_refused_before_anything_else(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """AUTH-02 on the newest mutating route — the gate is not opt-in."""
    job_id = await running(client, storage, JobState.TRANSCRIBING)

    response = await client.post(f"/api/jobs/{job_id}/cancel")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert storage.calls == []


@pytest.mark.parametrize(
    ("token", "target", "expected"),
    [
        pytest.param(None, "malformed", 401, id="401-beats-404"),
        pytest.param(TOKEN_A, "malformed", 404, id="404-beats-403"),
        pytest.param(TOKEN_B, "foreign", 403, id="403-after-both"),
    ],
)
async def test_the_load_bearing_precedence_401_beats_404_beats_403(
    client: AsyncClient,
    storage: FakeTranscriptStoragePort,
    token: str | None,
    target: str,
    expected: int,
) -> None:
    """An unauthenticated caller never learns whether an id exists, and an
    authenticated stranger never reaches a foreign job's state."""
    foreign_id = await running(client, storage, JobState.TRANSCRIBING)
    job_id = "not-a-ulid" if target == "malformed" else foreign_id
    headers = {} if token is None else auth_headers(token)

    response = await client.post(f"/api/jobs/{job_id}/cancel", headers=headers)

    assert response.status_code == expected
    assert storage.load_job(foreign_id).state is JobState.TRANSCRIBING
