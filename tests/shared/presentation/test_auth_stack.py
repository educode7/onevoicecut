"""AUTH-11: inspection of the shipped auth stack — no JWT component
participates, and only the static operator-token map can authenticate.

The first thing this file does is import `shared/application/principal.py`
and `shared/presentation/security.py`; before slice 1c's implementation that
import fails, and the failure is the RED. What follows is inspection, not
argument: an AST walk of every shipped module, the requirements files the
project actually installs from, and the resolver's own behaviour against
headers a forged session would carry.
"""

import ast
import re
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from fastapi import HTTPException

from onevoicecut.shared.application.principal import build_authenticator
from onevoicecut.shared.domain.ids import OperatorId, make_operator_id
from onevoicecut.shared.presentation.security import make_current_principal
from tests.shared.presentation.conftest import request_with_headers

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_ROOT = REPO_ROOT / "src" / "onevoicecut"

# Import roots (and requirement names) whose presence would mean JWT issuing
# or verification participates. `python-jose` is listed beside `jose` because
# requirements name packages while imports name modules.
JWT_COMPONENTS = frozenset(
    {"jwt", "pyjwt", "jose", "python-jose", "jwcrypto", "authlib", "pyjwkest"}
)


def _import_roots() -> set[str]:
    """Every top-level module name any shipped file imports or re-exports."""
    roots: set[str] = set()
    for path in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                roots.add(node.module.partition(".")[0])
    return roots


def _installed_requirement_names() -> set[str]:
    """Normalized package names from every requirements file at the repo root."""
    names: set[str] = set()
    for requirements in REPO_ROOT.glob("requirements*.txt"):
        for line in requirements.read_text(encoding="utf-8").splitlines():
            content = line.split("#", 1)[0].strip()
            if not content or content.startswith("-"):
                continue
            names.add(re.split(r"[<>=!~\[; ]", content)[0].strip().lower())
    return names


def test_no_jwt_component_participates() -> None:
    """Every shipped module parsed with `ast`, not one importing a JWT
    library — and no requirements file installing one, because a component
    that is never installed cannot participate either."""
    imported = _import_roots()
    assert not imported & JWT_COMPONENTS, sorted(imported & JWT_COMPONENTS)

    listed = _installed_requirement_names()
    assert not listed & JWT_COMPONENTS, sorted(listed & JWT_COMPONENTS)


def test_only_the_static_token_map_can_authenticate() -> None:
    """The resolver consults exactly one credential: the Authorization header,
    verified against the injected static map. A request whose every other
    channel is populated — forged cookie, pretend API key — authenticates
    nobody, and a wrong token fails with the one 401 shape regardless."""
    resolve = make_current_principal(
        build_authenticator({make_operator_id("a"): "t-a"})
    )

    principal = resolve(request_with_headers((b"authorization", b"Bearer t-a")))
    assert principal.identity == make_operator_id("a")

    forged_channels = (
        (b"cookie", b"session=forged"),
        (b"x-api-key", b"pretend-admin"),
    )
    for headers in (
        ((b"authorization", b"Bearer wrong-token"), *forged_channels),
        forged_channels,
    ):
        with pytest.raises(HTTPException) as refused:
            resolve(request_with_headers(*headers))
        assert refused.value.status_code == 401
        assert refused.value.headers == {"WWW-Authenticate": "Bearer"}


def test_the_dependency_is_unconstructable_without_an_authenticator() -> None:
    """Deny-by-default, layer two: past `WebDependencies`' required field, the
    factory itself refuses to build without an authenticator — a wiring slip
    is a TypeError at construction, never a 500 on the first request."""
    with pytest.raises(TypeError):
        make_current_principal()  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        make_current_principal(
            cast("Callable[[str | None], OperatorId]", None)
        )
