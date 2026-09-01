"""Fail-closed boot: a server never comes up with authentication disabled.

The composition root parses the operator token map before anything can serve a
request — an absent, empty, or malformed map refuses with an error naming the
failure, and the refusal message never carries a token value.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.web.auth import InvalidTokenMap
from onevoicecut.domain.ids import make_operator_id
from onevoicecut.runtime.app import build_dependencies
from onevoicecut.runtime.settings import Settings


def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.setenv("ONEVOICECUT_DATA_DIR", str(tmp_path))
    return Settings()  # type: ignore[call-arg]


def test_an_absent_token_map_refuses_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AUTH-07: zero operators means nobody can authenticate, and a server like
    that must never come up — the refusal happens at dependency construction,
    before any app exists to serve a route."""
    monkeypatch.delenv("ONEVOICECUT_OPERATOR_TOKENS", raising=False)

    with pytest.raises(InvalidTokenMap):
        build_dependencies(_settings(monkeypatch, tmp_path))


def test_an_empty_token_map_refuses_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ONEVOICECUT_OPERATOR_TOKENS", "")

    with pytest.raises(InvalidTokenMap):
        build_dependencies(_settings(monkeypatch, tmp_path))


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("maria", id="pair-with-no-colon"),
        pytest.param(":tok", id="empty-name"),
        pytest.param("Maria:tok", id="invalid-name"),
        pytest.param("maria:", id="empty-token"),
        pytest.param("maria:t1;maria:t2", id="duplicate-name"),
        pytest.param("maria:same;jose:same", id="duplicate-token"),
    ],
)
def test_every_malformed_map_refuses_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str
) -> None:
    """AUTH-08 boot level: the same malformed shapes the parser refuses all stop
    the composition root, before any route can serve requests."""
    monkeypatch.setenv("ONEVOICECUT_OPERATOR_TOKENS", raw)

    with pytest.raises(InvalidTokenMap):
        build_dependencies(_settings(monkeypatch, tmp_path))


def test_boot_refusal_messages_contain_no_token_value(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AUTH-09: the secret discipline starts at boot — the error may name an
    operator or a position, never the token."""
    monkeypatch.setenv("ONEVOICECUT_OPERATOR_TOKENS", "maria:sekrit-value;jose")

    with pytest.raises(InvalidTokenMap) as excinfo:
        build_dependencies(_settings(monkeypatch, tmp_path))

    assert "sekrit-value" not in str(excinfo.value)


def test_a_valid_map_boots_and_authenticates_its_operators(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The happy path: a well-formed map builds dependencies whose authenticator
    resolves each configured operator from their token."""
    monkeypatch.setenv("ONEVOICECUT_OPERATOR_TOKENS", "maria:tok-m;jose:tok-j")

    deps = build_dependencies(_settings(monkeypatch, tmp_path))

    assert deps.authenticate("Bearer tok-m") == make_operator_id("maria")
    assert deps.authenticate("Bearer tok-j") == make_operator_id("jose")
