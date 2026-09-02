"""The shared board: every job, attributed, hidden from nobody.

V2 visibility is a read model — one ministry team cutting the same church's
sermons genuinely needs "is Sunday's sermon done?". Read access to every job
is collaboration, not leakage; mutation stays owner-gated elsewhere. The
listing rides the unscoped `list_jobs()` reconcile uses, so nothing the store
knows can be hidden by the route.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import create_app
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    OPERATOR_A,
    OPERATOR_B,
    TOKEN_A,
    TOKEN_B,
    auth_headers,
    web_dependencies,
)

LEGACY_JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")


def a_legacy_record(job_id: JobId) -> JobRecord:
    """A record written before owners existed: no owner key, no owner."""
    return JobRecord(
        job_id=job_id,
        media_id=make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE"),
        state=JobState.PENDING,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1000.0,
        updated_at=1000.0,
        worker_pid=None,
        error=None,
        owner=None,
    )


@pytest.fixture
async def client(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncClient, FakeTranscriptStoragePort]]:
    deps, storage = web_dependencies(tmp_path)
    app = create_app(deps)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http, storage


async def admit(client: AsyncClient, token: str) -> str:
    response = await client.post(
        "/api/jobs", json={"engine": "local"}, headers=auth_headers(token)
    )
    assert response.status_code == 201
    job_id: str = response.json()["job_id"]
    return job_id


async def test_the_listing_returns_every_operators_jobs_attributed(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """VIS-03: the board shows both operators' work, and each row says whose
    it is — attribution is what makes a shared server legible."""
    http, _ = client
    a_id = await admit(http, TOKEN_A)
    b_id = await admit(http, TOKEN_B)

    response = await http.get("/api/jobs", headers=auth_headers(TOKEN_A))

    assert response.status_code == 200
    payload = response.json()
    items = {item["job_id"]: item for item in payload["jobs"]}
    assert set(items) == {a_id, b_id}
    assert items[a_id]["owner"] == OPERATOR_A
    assert items[b_id]["owner"] == OPERATOR_B


async def test_legacy_jobs_surface_with_null_owner(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """VIS-04: records persisted before this change list with `owner: null` —
    present, attributed to nobody, hidden from nobody."""
    http, storage = client
    storage.create_job(a_legacy_record(LEGACY_JOB_ID))

    response = await http.get("/api/jobs", headers=auth_headers(TOKEN_A))

    assert response.status_code == 200
    items = {item["job_id"]: item for item in response.json()["jobs"]}
    assert items[LEGACY_JOB_ID]["owner"] is None


async def test_the_listing_hides_nothing(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """VIS-05: N mixed records list exactly N, and no caller-identity scoping
    removes items — operator A and operator B see the same complete board."""
    http, storage = client
    a_id = await admit(http, TOKEN_A)
    b_id = await admit(http, TOKEN_B)
    storage.create_job(a_legacy_record(LEGACY_JOB_ID))

    seen: list[set[str]] = []
    for token in (TOKEN_A, TOKEN_B):
        response = await http.get("/api/jobs", headers=auth_headers(token))
        assert response.status_code == 200
        seen.append({item["job_id"] for item in response.json()["jobs"]})

    for ids in seen:
        assert ids == {a_id, b_id, LEGACY_JOB_ID}


async def test_a_foreign_job_is_readable_with_attribution(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """VIS-01: operator B reads operator A's job — 200 with the current state
    and the owner the job actually has."""
    http, _ = client
    a_id = await admit(http, TOKEN_A)

    response = await http.get(f"/api/jobs/{a_id}", headers=auth_headers(TOKEN_B))

    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "pending"
    assert payload["owner"] == OPERATOR_A


async def test_the_status_response_stays_backward_compatible(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """VIS-06 over HTTP: a client shaped against the pre-change response finds
    every field it knew, unchanged, and one field it did not know."""
    http, _ = client
    a_id = await admit(http, TOKEN_A)

    payload: dict[str, Any] = (
        await http.get(f"/api/jobs/{a_id}", headers=auth_headers(TOKEN_A))
    ).json()

    pre_change = {"job_id", "state", "engine", "speaker_mode", "error", "progress"}
    assert pre_change <= set(payload)
    assert payload["owner"] == OPERATOR_A


async def test_the_mine_filter_returns_only_the_callers_jobs(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """VIS-07: with the filter on, operator A sees exactly operator A's jobs —
    no foreign job, and no legacy job either, because an ownerless record
    belongs to the caller by no reading of the word."""
    http, storage = client
    a_id = await admit(http, TOKEN_A)
    await admit(http, TOKEN_B)
    storage.create_job(a_legacy_record(LEGACY_JOB_ID))

    response = await http.get(
        "/api/jobs", params={"mine": "true"}, headers=auth_headers(TOKEN_A)
    )

    assert response.status_code == 200
    items = {item["job_id"]: item for item in response.json()["jobs"]}
    assert set(items) == {a_id}
    assert items[a_id]["owner"] == OPERATOR_A


async def test_a_client_supplied_operator_identity_is_never_honored(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """VIS-08: operator B, filtering with mine and naming operator A in the
    query, still gets B's jobs. The filter is computed solely from the token
    identity; the supplied parameter is not a channel, and its presence is not
    an error — it simply has nowhere to arrive (the structural half is re-run
    by test_identity_discard's route-table assertion, which now walks the
    listing route too)."""
    http, _ = client
    a_id = await admit(http, TOKEN_A)
    b_id = await admit(http, TOKEN_B)

    response = await http.get(
        "/api/jobs",
        params={"mine": "true", "operator": str(OPERATOR_A)},
        headers=auth_headers(TOKEN_B),
    )

    assert response.status_code == 200
    items = {item["job_id"]: item for item in response.json()["jobs"]}
    assert set(items) == {b_id}
    assert items[b_id]["owner"] == OPERATOR_B
    assert a_id not in items


async def test_reading_writes_nothing(
    client: tuple[AsyncClient, FakeTranscriptStoragePort], tmp_path: Path
) -> None:
    """VIS-02: status reads and board polls are derivation, not mutation — zero
    write methods in the storage call log, zero new files, no state change."""
    http, storage = client
    a_id = await admit(http, TOKEN_A)
    await admit(http, TOKEN_B)

    calls_before = list(storage.calls)
    files_before = sorted(tmp_path.rglob("*"))

    status = await http.get(f"/api/jobs/{a_id}", headers=auth_headers(TOKEN_B))
    listing = await http.get("/api/jobs", headers=auth_headers(TOKEN_B))

    assert status.status_code == 200
    assert listing.status_code == 200
    assert storage.calls == calls_before
    assert sorted(tmp_path.rglob("*")) == files_before
    assert storage.state_history(make_job_id(a_id)) == []
