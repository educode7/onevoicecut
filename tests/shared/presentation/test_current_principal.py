"""AUTH-10: a valid bearer token resolves `CurrentPrincipal`, and only the
identity crosses into the application.

Both halves of the scenario are asserted where they can actually break: the
route's own dependency — not a fresh factory call — turns `Bearer t-a` into
operator "a", and the `AdmitJobCommand` handed to the handler carries that
identity with no token anywhere in the crossing. `dataclasses.fields` keeps the
principal honest: two fields, neither of which can hold a credential.

Importing `shared/application/principal.py` and `shared/presentation/security.py`
is part of the test — before slice 1c's implementation this module fails at
collection, which is its RED.
"""

from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies
from onevoicecut.main import create_app
from onevoicecut.shared.application.principal import Principal, build_authenticator
from onevoicecut.shared.domain.ids import make_operator_id
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.admit_job import (
    AdmitJobCommand,
    AdmitJobHandler,
)
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.shared.presentation.conftest import request_with_headers
from tests.unit.adapters.web.conftest import accepting_extractor

OPERATOR_A = make_operator_id("a")
TOKEN_A = "t-a"


def _a_server(tmp_path: Path) -> FastAPI:
    """A server configured exactly as the scenario names it: operator "a",
    token "t-a", nothing else."""
    return create_app(
        WebDependencies(
            storage=FakeTranscriptStoragePort(tmp_path),
            authenticate=build_authenticator({OPERATOR_A: TOKEN_A}),
            extractor_for=accepting_extractor,
        )
    )


def _route_principal_dependency(app: FastAPI) -> Any:
    """The dependency the POST /api/jobs route itself declares for its
    `principal` parameter.

    Read off the registered route rather than built from a factory call,
    because a fresh `make_current_principal(...)` would prove the factory and
    say nothing about whether the route is wired to it. The walk descends the
    lazy `_IncludedRouter` wrapper exactly as the generated auth-gate does.
    """
    pending: list[object] = list(app.routes)
    route: APIRoute | None = None
    while pending:
        candidate = pending.pop(0)
        nested = getattr(candidate, "original_router", None)
        if nested is not None:
            pending.extend(getattr(nested, "routes", []))
            continue
        if (
            isinstance(candidate, APIRoute)
            and candidate.path == "/api/jobs"
            and "POST" in (candidate.methods or set())
        ):
            route = candidate
            break
    assert route is not None, "POST /api/jobs is not in the route table"
    dependency = next(
        dep for dep in route.dependant.dependencies if dep.name == "principal"
    )
    assert dependency.call is not None
    return dependency.call


def test_valid_token_resolves_the_route_principal_to_the_configured_identity(
    tmp_path: Path,
) -> None:
    """AUTH-10 first half: `Bearer t-a` against the configured map resolves to
    identity "a" — through the route's own dependency, with the token nowhere
    in the result."""
    resolve = _route_principal_dependency(_a_server(tmp_path))

    principal = resolve(request_with_headers((b"authorization", b"Bearer t-a")))

    assert principal == Principal(identity=OPERATOR_A, roles=frozenset())
    assert TOKEN_A not in repr(principal)


def test_the_principal_carries_only_identity_and_roles(tmp_path: Path) -> None:
    """The structural half of "no token crosses into the application": the
    principal has exactly two fields, so a credential has nowhere to ride
    along even if some future code path wanted it to."""
    resolve = _route_principal_dependency(_a_server(tmp_path))

    principal = resolve(request_with_headers((b"authorization", b"Bearer t-a")))

    assert [field.name for field in fields(principal)] == ["identity", "roles"]


def test_a_missing_or_wrong_token_is_refused_by_the_route_dependency(
    tmp_path: Path,
) -> None:
    """The dependency refuses before the handler exists to do work — one 401
    shape for a missing header and for a token that matches nobody, which is
    what makes it a drop-in replacement for the handler-first helper."""
    resolve = _route_principal_dependency(_a_server(tmp_path))

    for header in (None, b"Bearer wrong"):
        request = (
            request_with_headers()
            if header is None
            else request_with_headers((b"authorization", header))
        )
        with pytest.raises(HTTPException) as refused:
            resolve(request)
        assert refused.value.status_code == 401
        assert refused.value.detail == "not authenticated"
        assert refused.value.headers == {"WWW-Authenticate": "Bearer"}


async def test_the_application_receives_the_identity_and_never_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AUTH-10 second half: what crosses presentation → application is the
    identity, on the command's own `principal` — the token that produced it
    stops at the dependency."""
    captured: dict[str, Any] = {}
    real_handle = AdmitJobHandler.handle

    def spy(self: AdmitJobHandler, command: AdmitJobCommand) -> Any:
        captured["principal"] = command.principal
        captured["engine"] = command.engine
        captured["speaker_mode"] = command.speaker_mode
        return real_handle(self, command)

    monkeypatch.setattr(AdmitJobHandler, "handle", spy)

    app = _a_server(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/jobs",
            json={"engine": "local"},
            headers={"authorization": f"Bearer {TOKEN_A}"},
        )

    assert response.status_code == 201
    assert captured["principal"].identity == OPERATOR_A
    assert TOKEN_A not in repr(captured)
