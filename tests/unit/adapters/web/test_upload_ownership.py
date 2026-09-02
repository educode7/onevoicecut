"""Ownership on the upload path: the 403 that keeps operator B out of
operator A's media.

The check wraps the existing mechanics without restructuring them — the owner
still uploads exactly as before, and a non-owner gets nothing touched: no
partial file, no record write, no worker.
"""

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

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
)

OWNERSHIP_BODY = b'{"detail":"not the owner of this job"}'


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


async def test_owner_upload_succeeds_with_the_mechanics_unchanged(
    client: AsyncClient, storage: FakeTranscriptStoragePort, started: list[JobId]
) -> None:
    """OWN-03 / OWN-10: authorization wraps the upload path — commit-by-rename
    from the sibling partial file, extensionless stored source, content type
    from the probe, and the client filename as metadata only."""
    job_id = await admitted(client)

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"hola mundo",
        headers={**auth_headers(TOKEN_A), "x-filename": quote("predicación.mp4")},
    )

    assert response.status_code == 204
    assert storage.source_path(job_id).read_bytes() == b"hola mundo"
    assert not list(storage.job_dir(job_id).glob("*.part"))
    media = storage.load_media(job_id)
    assert media.original_filename == "predicación.mp4"
    assert media.stored_path == storage.source_path(job_id)
    assert media.container == "mov,mp4,m4a"
    # Queued rather than started: the supervisor owns the spawn decision now.
    assert storage.load_job(job_id).state is JobState.QUEUED
    assert started == []


async def test_non_owner_upload_is_denied_with_nothing_touched(
    client: AsyncClient, storage: FakeTranscriptStoragePort, started: list[JobId]
) -> None:
    """OWN-04: operator B's attempt answers 403 and leaves the job exactly as
    found — prior media intact, record unchanged, no partial file, no worker."""
    job_id = await admitted(client)
    await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"first upload",
        headers=auth_headers(TOKEN_A),
    )
    record_before = storage.load_job(job_id)

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"second upload",
        headers=auth_headers(TOKEN_B),
    )

    assert response.status_code == 403
    assert response.content == OWNERSHIP_BODY
    assert storage.source_path(job_id).read_bytes() == b"first upload"
    assert storage.load_job(job_id) == record_before
    assert not list(storage.job_dir(job_id).glob("*.part"))
    assert started == []


async def test_a_known_foreign_id_is_denied_by_ownership_not_secrecy(
    client: AsyncClient,
) -> None:
    """OWN-08: the id is well-formed and the job really exists — B saw it in the
    shared listing — yet the mutation is refused by the ownership check, with
    403 rather than the 404 reserved for ids that do not exist."""
    job_id = await admitted(client)

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"x",
        headers=auth_headers(TOKEN_B),
    )

    assert response.status_code == 403


async def test_an_ownerless_legacy_job_is_mutable_by_nobody_over_http(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """D1's uniform rule reaches the route: a pre-change record with no owner
    answers 403 to every operator — visible to all, mutable by nobody."""
    job_id = await admitted(client)
    storage.update_job(replace(storage.load_job(job_id), owner=None))

    response = await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"x",
        headers=auth_headers(TOKEN_A),
    )

    assert response.status_code == 403


async def test_a_malformed_id_is_a_404_before_any_filesystem_access(
    client: AsyncClient, tmp_path: Path
) -> None:
    """OWN-09: authenticated, mutating, malformed — refused at the id check
    with the unknown-identifier outcome, before any filesystem access."""
    response = await client.put(
        "/api/jobs/not-a-ulid/media",
        content=b"x",
        headers=auth_headers(TOKEN_A),
    )

    assert response.status_code == 404
    assert list(tmp_path.rglob("*")) == []


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
    """OWN-09's three orderings that matter on the mutating route: an
    unauthenticated request never learns whether the id exists; an authenticated
    one never reaches a foreign job's bytes. The foreign job is owned by A
    (admitted above), so B is the non-owner."""
    foreign_id = await admitted(client)
    job_id = "not-a-ulid" if target == "malformed" else foreign_id
    headers = {} if token is None else auth_headers(token)

    response = await client.put(f"/api/jobs/{job_id}/media", content=b"x", headers=headers)

    assert response.status_code == expected
    assert storage.load_job(foreign_id).state is JobState.PENDING
