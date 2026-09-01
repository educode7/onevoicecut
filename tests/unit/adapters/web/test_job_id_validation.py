"""The id in the URL, checked at the door.

It already worked before this: the filesystem adapter validates the id before
building a path, so a hostile one produced a 404 rather than a traversal. But it
worked *because of where storage happens to check*, which makes the guarantee a
property of one adapter rather than of the route. A different storage backend —
or a refactor inside this one — would silently remove it.

So the route validates too, against the same ULID pattern the domain owns. Two
checks for one rule is not duplication here; it is the difference between a
boundary that holds and one that happens to.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.domain.ids import JobId, make_media_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.ports.media_source import MediaSourcePort
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    accepting_extractor,
    auth_headers,
    fake_authenticate,
)

HOSTILE_IDS = [
    "..",
    "../..",
    "../../etc/passwd",
    "..%2F..%2Fetc",
    "%2e%2e%2f",
    "..%5C..%5Cwindows",  # backslash: the separator that matters on this machine
    "%00",
    "01HQ3M8XKJ7VNPQR2ZYWB4TCF",  # 25 chars — one short of a ULID
    "01HQ3M8XKJ7VNPQR2ZYWB4TCFDD",  # 27 — one too many
    "01hq3m8xkj7vnpqr2zywb4tcfd",  # lower case
    "01HQ3M8XKJ7VNPQR2ZYWB4TCFI",  # I is not in the Crockford alphabet
    "01HQ3M8XKJ7VNPQR2ZYWB4TCFU",  # nor is U
    "",
    " ",
    ".",
]


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
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=auth_headers()
    ) as http:
        yield http


@pytest.mark.parametrize("job_id", HOSTILE_IDS)
async def test_an_id_that_is_not_a_ulid_never_reaches_a_job(
    client: AsyncClient, job_id: str
) -> None:
    response = await client.put(f"/api/jobs/{job_id}/media", content=b"x")

    assert response.status_code in (404, 405, 307), (
        f"{job_id!r} produced {response.status_code}"
    )


@pytest.mark.parametrize("job_id", HOSTILE_IDS)
async def test_a_hostile_id_writes_nothing_at_all(
    client: AsyncClient, job_id: str, tmp_path: Path
) -> None:
    """Rejected before the filesystem is touched, not after a path is resolved and
    found to be outside. Resolution-then-check is the ordering that has already
    created a directory somewhere by the time it answers."""
    await client.put(f"/api/jobs/{job_id}/media", content=b"x")

    assert list(tmp_path.rglob("*")) == []


async def test_a_well_formed_but_unknown_id_is_also_a_404(
    client: AsyncClient,
) -> None:
    """Same answer as a malformed one, on purpose: a different status would tell a
    caller which ids exist."""
    response = await client.put(
        "/api/jobs/01HQ3M8XKJ7VNPQR2ZYWB4TCFD/media", content=b"x"
    )

    assert response.status_code == 404


async def test_a_hostile_id_never_reaches_the_writer(tmp_path: Path) -> None:
    """The check that is actually load-bearing, isolated so it can fail.

    Today a hostile id dies at `load_job`, which finds no such job — so every
    other test here would pass with no route-level validation at all. This one
    removes that accident: storage trusts any id and returns a job for it, so the
    only thing left between a traversal and the writer is the route.

    That matters because the current safety is an artifact of statement order. A
    handler that built the writer before loading the job would hand it a path
    outside the data directory, and nothing in the other tests would notice.
    """
    reached: list[str] = []

    class TrustingStorage(FakeTranscriptStoragePort):
        def load_job(self, job_id: JobId) -> JobRecord:
            return JobRecord(
                job_id=job_id,
                media_id=make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE"),
                state=JobState.PENDING,
                speaker_mode=SpeakerMode.SINGLE,
                engine=EngineChoice.LOCAL,
                created_at=1.0,
                updated_at=1.0,
                worker_pid=None,
                error=None,
                owner=None,
            )

    def spy(_: TranscriptStoragePort, job_id: JobId) -> MediaSourcePort:
        reached.append(job_id)
        raise AssertionError(f"the writer was handed {job_id!r}")

    app = create_app(
        WebDependencies(
            storage=TrustingStorage(tmp_path),
            authenticate=fake_authenticate,
            media_source_for=spy,
            extractor_for=accepting_extractor,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers=auth_headers(),
    ) as client:
        # `%2e%2e` survives routing and decodes to `..` in the path parameter —
        # the form that reaches a handler, unlike `../..`, which the router and
        # the client normalise away before anyone sees it.
        response = await client.put("/api/jobs/%2e%2e/media", content=b"x")

    assert reached == []
    assert response.status_code == 404
    assert list(tmp_path.rglob("*")) == []


async def test_a_non_ulid_that_survives_routing_never_reaches_the_writer(
    tmp_path: Path,
) -> None:
    """`not-a-ulid` is not a traversal, but it is the same hole: an id the route
    accepted without looking, handed to something that builds a path from it."""
    reached: list[str] = []

    class TrustingStorage(FakeTranscriptStoragePort):
        def load_job(self, job_id: JobId) -> JobRecord:
            return JobRecord(
                job_id=job_id,
                media_id=make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE"),
                state=JobState.PENDING,
                speaker_mode=SpeakerMode.SINGLE,
                engine=EngineChoice.LOCAL,
                created_at=1.0,
                updated_at=1.0,
                worker_pid=None,
                error=None,
                owner=None,
            )

    def spy(_: TranscriptStoragePort, job_id: JobId) -> MediaSourcePort:
        reached.append(job_id)
        raise AssertionError(f"the writer was handed {job_id!r}")

    app = create_app(
        WebDependencies(
            storage=TrustingStorage(tmp_path),
            authenticate=fake_authenticate,
            media_source_for=spy,
            extractor_for=accepting_extractor,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
        headers=auth_headers(),
    ) as client:
        response = await client.put("/api/jobs/not-a-ulid/media", content=b"x")

    assert reached == []
    assert response.status_code == 404
