"""AUTH-17: token plaintext is extracted only at a composition root.

Every `get_secret_value()` call on the operator-token field must reside in
`main.py` or under `runtime/` — the composition roots that build the static
token map and hand it to `parse_operator_tokens`. No module under `shared/`
(presentation, application, domain — or the later `systems/` trees) may peel
the `SecretStr` open; the field stays sealed until the root extracts it once.
"""

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "onevoicecut"


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.py"))


def _is_composition_root(path: Path) -> bool:
    """`main.py` at the package root, or any file under `runtime/`.

    `main.py` does not exist yet (slice 1d lands it); naming it here means
    the rule does not need a second edit when the web factory moves.
    """
    rel = path.relative_to(SRC_ROOT)
    if rel.name == "main.py" and len(rel.parts) == 1:
        return True
    return bool(rel.parts) and rel.parts[0] == "runtime"


def _calls_get_secret_value(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "get_secret_value":
                return True
    return False


def test_get_secret_value_lives_only_in_composition_roots() -> None:
    """AUTH-17: every `get_secret_value()` call sits in `main.py` or `runtime/`."""
    offenders: list[str] = []
    for path in _iter_python_files(SRC_ROOT):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if _calls_get_secret_value(tree) and not _is_composition_root(path):
            offenders.append(str(path.relative_to(SRC_ROOT)))
    assert not offenders, (
        "get_secret_value() outside a composition root: "
        f"{sorted(offenders)}; extraction is allowed only in main.py or runtime/"
    )


def test_presentation_application_domain_never_extract_plaintext() -> None:
    """Belt-and-suspenders: no presentation/application/domain directory
    extracts the plaintext, even if the composition-root rule is ever
    loosened or a future call is written under a different name."""
    offenders: list[str] = []
    for path in _iter_python_files(SRC_ROOT):
        rel = path.relative_to(SRC_ROOT)
        parts = rel.parts
        # Directory components only — a file named `domain.py` is not a layer.
        if any(part in ("presentation", "application", "domain") for part in parts[:-1]):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if _calls_get_secret_value(tree):
                offenders.append(str(rel))
    assert not offenders, (
        f"presentation/application/domain extracts plaintext: {sorted(offenders)}"
    )
