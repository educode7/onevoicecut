"""The authentication gate: deny-by-default construction, and a 401 check
generated from the route table itself.

A route that forgets its authentication step is not caught by review discipline
but by this file: the parametrized check below is derived from `app.routes`, so
any route registered later joins it automatically and fails the default run the
day it is created without auth wiring.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.domain.ids import JobId
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    accepting_extractor,
    auth_headers,
    fake_authenticate,
)

PROBE_JOB_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFD"
UNAUTHENTICATED_BODY = b'{"detail":"not authenticated"}'


def _registered_route_cases() -> list[tuple[str, str]]:
    """Every APIRoute in the registered route table, never a hand-maintained
    list. FastAPI wraps an included router in a lazy object, so anything that is
    not an APIRoute but carries an `original_router` is descended into; the walk
    starts from `app.routes` itself, so every route the app serves joins here.

    The app built for the walk only ever has its route table inspected — no
    request is served by it — which is why a placeholder storage is enough.
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
        if not isinstance(route, APIRoute):
            continue
        path = route.path.replace("{job_id}", PROBE_JOB_ID)
        for method in sorted(route.methods or set()):
            cases.append((method, path))
    return cases


ROUTE_CASES = _registered_route_cases()


def test_the_route_table_is_not_empty() -> None:
    """Guard against the gate silently degenerating to zero cases, which would
    make every future route 'pass' by never being checked."""
    assert ROUTE_CASES


@pytest.fixture
def started() -> list[JobId]:
    return []


@pytest.fixture
async def gate(
    tmp_path: Path, started: list[JobId]
) -> AsyncIterator[tuple[AsyncClient, FakeTranscriptStoragePort]]:
    storage = FakeTranscriptStoragePort(tmp_path)
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
        yield http, storage


@pytest.mark.parametrize(
    ("method", "path"),
    ROUTE_CASES,
    ids=[f"{method} {path}" for method, path in ROUTE_CASES],
)
async def test_every_route_refuses_unauthenticated_requests_with_one_shape(
    gate: tuple[AsyncClient, FakeTranscriptStoragePort],
    started: list[JobId],
    tmp_path: Path,
    method: str,
    path: str,
) -> None:
    """AUTH-02 / AUTH-06: no route does any work without a valid token — the
    request is refused before a job is admitted, a byte is written, a recording
    starter is called, or a cancellation is recorded."""
    client, storage = gate

    if method == "POST":
        response = await client.request(method, path, json={"engine": "local"})
    elif method == "PUT":
        response = await client.request(method, path, content=b"x")
    else:
        response = await client.request(method, path)

    assert response.status_code == 401
    assert response.content == UNAUTHENTICATED_BODY
    assert response.headers["www-authenticate"] == "Bearer"

    assert storage.list_jobs() == ()
    assert storage.calls == []
    assert started == []
    assert list(tmp_path.rglob("*")) == []


@pytest.mark.parametrize(
    "authorization",
    [
        pytest.param(None, id="missing"),
        pytest.param("", id="empty"),
        pytest.param("Basic dXNlcjpwYXNz", id="wrong-scheme"),
        pytest.param("Bearer", id="bare-scheme"),
        pytest.param("Bearer ", id="scheme-no-token"),
        pytest.param("Bearer tok-nobody", id="unknown-token"),
        pytest.param("not even a scheme", id="unparsable"),
    ],
)
async def test_all_401_causes_are_byte_identical(
    tmp_path: Path, authorization: str | None
) -> None:
    """AUTH-04 / AUTH-05 HTTP half: missing, malformed, and unknown credentials
    produce one indistinguishable response — no malformed-vs-missing oracle, no
    enumeration of which operators exist."""
    storage = FakeTranscriptStoragePort(tmp_path)
    app = create_app(WebDependencies(storage=storage, authenticate=fake_authenticate))
    headers = {} if authorization is None else {"authorization": authorization}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/jobs", json={"engine": "local"}, headers=headers)

    assert response.status_code == 401
    assert response.content == UNAUTHENTICATED_BODY
    assert response.headers["www-authenticate"] == "Bearer"
    assert storage.list_jobs() == ()


async def test_web_dependencies_cannot_be_built_without_an_authenticator(
    tmp_path: Path,
) -> None:
    """Deny-by-default at construction (D8): there is no permissive default to
    fall back to, so a composition site that forgets the authenticator is a
    build error, not a server that runs open."""
    with pytest.raises(TypeError):
        WebDependencies(storage=FakeTranscriptStoragePort(tmp_path))  # type: ignore[call-arg]


async def test_an_authenticated_request_still_passes(
    gate: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """The gate proves refusal, and this proves it is not a wall: a valid token
    still reaches normal handling."""
    client, storage = gate

    response = await client.post(
        "/api/jobs", json={"engine": "local"}, headers=auth_headers()
    )

    assert response.status_code == 201
    assert len(storage.list_jobs()) == 1
