"""OWN-05: every mutating path refuses a non-owner, and none of them is exempt.

The individual routes each prove their own 403 — upload in
`test_upload_ownership.py`, cancellation in `test_cancel_route.py`. What those
cannot prove is the *closure*: that no mutating route exists which nobody
remembered to gate.

So the cases here are generated from the registered route table rather than
listed, exactly as the 401 gate is. A route added later that changes a job and
forgets `_owned` does not slip through review — it fails the default run the day
it is written.
"""

from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.domain.errors import JobNotOwned
from onevoicecut.domain.ids import JobId, make_job_id, make_operator_id
from onevoicecut.domain.jobs import JobState
from onevoicecut.usecases.ownership import require_owner
from onevoicecut.usecases.purge_job_artifacts import PurgeJobArtifacts
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    TOKEN_A,
    TOKEN_B,
    accepting_extractor,
    auth_headers,
    fake_authenticate,
)
from tests.unit.usecases.test_ownership import an_owned_job

OWNERSHIP_BODY = b'{"detail":"not the owner of this job"}'
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _mutating_job_routes() -> list[tuple[str, str]]:
    """Every registered route that names a job and changes something.

    "Names a job" is the `{job_id}` parameter and "changes something" is the
    method — both read off the table, so the set grows by itself.
    """
    app = create_app(
        WebDependencies(
            storage=FakeTranscriptStoragePort(Path("unused-route-probe")),
            authenticate=fake_authenticate,
        )
    )
    cases: list[tuple[str, str]] = []
    pending: list[object] = list(app.routes)
    for route in pending:
        nested_router = getattr(route, "original_router", None)
        if nested_router is not None:
            pending.extend(getattr(nested_router, "routes", []))
            continue
        if not isinstance(route, APIRoute) or "{job_id}" not in route.path:
            continue
        for method in sorted((route.methods or set()) & MUTATING_METHODS):
            cases.append((method, route.path))
    return cases


MUTATING_ROUTES = _mutating_job_routes()


def test_the_matrix_covers_the_mutations_that_exist() -> None:
    """A guard against the generator quietly returning nothing.

    Zero cases would make every route below "pass". Upload and cancellation are
    the two mutating routes today; the assertion is on the floor, not the exact
    number, so adding the next one does not fail this.
    """
    assert len(MUTATING_ROUTES) >= 2


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
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def owned_by_a(client: AsyncClient) -> JobId:
    response = await client.post(
        "/api/jobs", json={"engine": "local"}, headers=auth_headers(TOKEN_A)
    )
    return make_job_id(response.json()["job_id"])


@pytest.mark.parametrize(
    ("method", "path"),
    MUTATING_ROUTES,
    ids=[f"{method} {path}" for method, path in MUTATING_ROUTES],
)
async def test_every_mutating_route_refuses_a_non_owner(
    client: AsyncClient,
    storage: FakeTranscriptStoragePort,
    method: str,
    path: str,
) -> None:
    """One refusal shape across the whole mutation class, nothing touched.

    Operator B is authenticated and the job is real — B can see it on the shared
    board — so this is the ownership check refusing, not identifier secrecy.
    """
    job_id = await owned_by_a(client)
    record_before = storage.load_job(job_id)
    storage.calls.clear()

    response = await client.request(
        method, path.replace("{job_id}", job_id), content=b"x", headers=auth_headers(TOKEN_B)
    )

    assert response.status_code == 403
    assert response.content == OWNERSHIP_BODY
    assert storage.load_job(job_id) == record_before
    assert storage.calls == []


@pytest.mark.parametrize(
    ("method", "path"),
    MUTATING_ROUTES,
    ids=[f"{method} {path}" for method, path in MUTATING_ROUTES],
)
async def test_every_mutating_route_refuses_an_ownerless_legacy_job(
    client: AsyncClient,
    storage: FakeTranscriptStoragePort,
    method: str,
    path: str,
) -> None:
    """D1's uniform rule across the class: `owner=None` matches no operator, so
    a pre-change job is visible to all and mutable by none — with no legacy
    branch anywhere in the authorization code to get wrong."""
    job_id = await owned_by_a(client)
    storage.update_job(replace(storage.load_job(job_id), owner=None))

    response = await client.request(
        method, path.replace("{job_id}", job_id), content=b"x", headers=auth_headers(TOKEN_A)
    )

    assert response.status_code == 403


def test_the_purge_request_belongs_to_the_same_mutation_class() -> None:
    """Purge has no route yet, so its arm of the matrix is stated where it lives.

    The request carries a required `operator` for exactly this reason: when the
    retention policy grows a caller, the gate it faces is already the shared
    one, and no new authorization decision gets invented at that point.
    """
    job = an_owned_job()
    request = PurgeJobArtifacts(job_id=job.job_id, operator=make_operator_id("diego"))

    with pytest.raises(JobNotOwned):
        require_owner(job, request.operator)


async def test_the_matrix_is_not_a_wall_the_owner_still_mutates(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """The refusals above prove denial; this proves the gate is not simply shut.

    A `_owned` that raised for everybody would satisfy every assertion in this
    file and break the product.
    """
    job_id = await owned_by_a(client)

    response = await client.post(
        f"/api/jobs/{job_id}/cancel", headers=auth_headers(TOKEN_A)
    )

    assert response.status_code == 200
    assert storage.load_job(job_id).state is JobState.CANCELLED
