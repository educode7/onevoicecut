"""The HTTP surface is served under `/api/v1` and nowhere else.

`api-versioning` AV-02, AV-03, AV-04 and AV-08. The version prefix is not a
convention this file asserts by reading source: it is derived from the live
route table, the same table the generated auth gates read, so a route added
later joins every check here without being listed.

AV-08's plant proves the derivation by construction — a route registered
without authentication handling has to be *found* by the walk before the 401
gate can fail on it, and a check built from a literal list would silently
never see it.
"""

import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
import yaml
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from onevoicecut.main import WebDependencies, create_app
from onevoicecut.shared.domain.ids import JobId
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    accepting_extractor,
    fake_authenticate,
    route_request_body,
)

CONFIG_PATH = Path(__file__).resolve().parent.parent / "fca_config.yaml"
PROBE_JOB_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFD"
VERSION_SEGMENT = re.compile(r"^/api/(v[^/]+)/")


def _walk_api_routes(app: FastAPI) -> list[APIRoute]:
    """Every APIRoute the app serves, descending through included routers.

    FastAPI wraps a router registered with `include_router` in a lazy object
    whose `original_router` carries the nested route table, so the walk
    descends through those rather than seeing one opaque entry. Non-APIRoute
    entries (`/openapi.json`) are skipped: they are framework surface, not
    operations, and no operation of ours lives there.
    """
    routes: list[APIRoute] = []
    pending: list[object] = list(app.routes)
    for route in pending:
        nested_router = getattr(route, "original_router", None)
        if nested_router is not None:
            pending.extend(getattr(nested_router, "routes", []))
            continue
        if isinstance(route, APIRoute):
            routes.append(route)
    return routes


def _build_app(tmp_path: Path) -> FastAPI:
    return create_app(
        WebDependencies(
            storage=FakeTranscriptStoragePort(tmp_path),
            authenticate=fake_authenticate,
            extractor_for=accepting_extractor,
        )
    )


def _served_paths(tmp_path: Path) -> list[str]:
    paths = [route.path for route in _walk_api_routes(_build_app(tmp_path))]
    assert paths, "the route table must not be empty"
    return paths


def _active_versions() -> set[str]:
    """The `active` entries in the API-version registry, the source of truth
    AV-04 compares the shipped routes against."""
    raw: Any = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    config = cast(dict[str, Any], raw)
    return {
        str(version["version"])
        for system in config["systems"]
        for module in system["modules"]
        for version in module["api_versions"]
        if version["status"] == "active"
    }


def test_av02_every_served_operation_is_versioned(tmp_path: Path) -> None:
    """AV-02: every path in the route table begins `/api/v1/`, and none is
    served under a bare `/api` — an unversioned alias is the thing AV-08
    exists to keep out of the surface."""
    paths = _served_paths(tmp_path)
    unversioned = [path for path in paths if not path.startswith("/api/v1/")]
    assert unversioned == [], (
        "operations served outside the v1 prefix: " + ", ".join(sorted(unversioned))
    )


@pytest.mark.parametrize(
    ("method", "suffix"),
    [
        pytest.param("POST", "/api/jobs", id="admit"),
        pytest.param("GET", "/api/jobs", id="listing"),
        pytest.param("GET", f"/api/jobs/{PROBE_JOB_ID}", id="status"),
        pytest.param("PUT", f"/api/jobs/{PROBE_JOB_ID}/media", id="upload"),
        pytest.param("POST", f"/api/jobs/{PROBE_JOB_ID}/cancel", id="cancel"),
        pytest.param("POST", f"/api/jobs/{PROBE_JOB_ID}/clips", id="clips"),
    ],
)
async def test_av03_former_unversioned_paths_answer_404_without_side_effects(
    tmp_path: Path, method: str, suffix: str
) -> None:
    """AV-03: a request to a former unversioned path is not merely refused, it
    does not exist — 404 with nothing admitted, written or spawned. A 401 here
    would mean the old surface is still routed and merely gated."""
    assert not suffix.startswith("/api/v1/"), (
        "these literals are the *retired* paths on purpose; if a bulk "
        "migration rewrites them to the live ones this test becomes a "
        "tautology against the surface it is supposed to prove gone"
    )
    storage = FakeTranscriptStoragePort(tmp_path)
    app = create_app(
        WebDependencies(
            storage=storage,
            authenticate=fake_authenticate,
            extractor_for=accepting_extractor,
        )
    )
    content, json_body = route_request_body(method, suffix)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.request(method, suffix, content=content, json=json_body)

    assert response.status_code == 404
    assert storage.list_jobs() == ()
    assert storage.calls == []
    assert list(tmp_path.rglob("*")) == []


def test_av04_served_versions_equal_the_active_registry(tmp_path: Path) -> None:
    """AV-04: the version segment carried by the route table is exactly the set
    of versions `fca_config.yaml` marks active. Serves as the cross-check
    between the registry AV-05 pins and the routes the server actually
    registers — neither alone proves the other."""
    served = {
        match.group(1)
        for path in _served_paths(tmp_path)
        if (match := VERSION_SEGMENT.match(path))
    }
    active = _active_versions()
    assert active, "the registry must mark at least one version active"
    assert served == active, (
        f"served versions {sorted(served)} != active versions {sorted(active)}"
    )


async def test_av08_an_unauthenticated_route_is_caught_by_the_derived_gate(
    tmp_path: Path,
) -> None:
    """AV-08 / AUTH-06: the 401 check derives from `app.routes`, so a route
    registered without authentication handling joins it by construction and
    fails the gate naming it.

    The plant below carries no `principal` dependency. Two things are asserted:
    the walk finds it (which is what makes it a *derived* check rather than a
    literal list), and the unauthenticated request it answers is not a 401 —
    which is precisely the failure the generated check reports. A gate built
    from a hand-maintained path list would see neither.
    """
    app = _build_app(tmp_path)

    @app.get("/api/v1/planted-without-auth", tags=["plant"])
    def planted() -> dict[str, str]:
        return {"planted": "yes"}

    paths = [route.path for route in _walk_api_routes(app)]
    assert "/api/v1/planted-without-auth" in paths, (
        "the route-table walk must find a route added after the app was built; "
        "otherwise the generated gate is a literal list wearing a loop"
    )

    storage = FakeTranscriptStoragePort(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/planted-without-auth")

    assert response.status_code != 401, (
        "the planted route serves without authentication, so the generated 401 "
        "gate must fail on it — a 401 here means the gate is not observing "
        "this route at all"
    )
