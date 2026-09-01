"""Identity never travels as a request parameter.

The owner comes from the token and nothing else: a body that names somebody is
ignored, not rejected, and no route even declares a place to put an operator —
the structural half of that claim is re-run by the listing filter's tests.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.adapters.web.schemas import AdmitJobRequest
from onevoicecut.domain.ids import make_job_id
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    OPERATOR_B,
    TOKEN_B,
    auth_headers,
    fake_authenticate,
)


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


@pytest.fixture
async def client(storage: FakeTranscriptStoragePort) -> AsyncIterator[AsyncClient]:
    app = create_app(
        WebDependencies(storage=storage, authenticate=fake_authenticate)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def test_a_client_supplied_operator_identity_has_no_effect(
    client: AsyncClient, storage: FakeTranscriptStoragePort
) -> None:
    """OWN-07: operator B admits with a body claiming operator "a" — the
    recorded owner is still B, the token-resolved caller. Discarded, not
    rejected: a 422 here would fail the scenario the same as honoring it."""
    response = await client.post(
        "/api/jobs",
        json={"engine": "local", "operator": "a"},
        headers=auth_headers(TOKEN_B),
    )

    assert response.status_code == 201
    job = storage.load_job(make_job_id(response.json()["job_id"]))
    assert job.owner == OPERATOR_B


def test_no_route_declares_an_operator_identity_parameter(
    tmp_path: Path,
) -> None:
    """VIS-08 structural half: identity has nowhere to arrive — no route
    declares an operator in path, query, or header, and the admission body has
    no such field. A future route adding one fails here, not in review."""
    app = create_app(
        WebDependencies(
            storage=FakeTranscriptStoragePort(tmp_path),
            authenticate=fake_authenticate,
        )
    )

    pending: list[object] = list(app.routes)
    checked = 0
    for route in pending:
        nested_router = getattr(route, "original_router", None)
        if nested_router is not None:
            pending.extend(getattr(nested_router, "routes", []))
            continue
        if not isinstance(route, APIRoute):
            continue
        checked += 1
        dependant = route.dependant
        declared = {
            param.name
            for param in (
                *dependant.path_params,
                *dependant.query_params,
                *dependant.header_params,
            )
        }
        assert "operator" not in declared, f"{route.path} declares an operator"
    assert checked > 0
    assert "operator" not in AdmitJobRequest.model_fields
