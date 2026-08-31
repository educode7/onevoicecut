"""Turns the hexagonal boundary into a failing test.

Walks `src/onevoicecut/{domain,usecases,ports}` with `ast` and asserts none of
them imports `onevoicecut.adapters` or `onevoicecut.runtime`. Uses static AST
parsing rather than `importlib`, so it works correctly whether or not the
`adapters`/`runtime` packages exist yet on disk — an import statement is a
violation the moment it is written in source text, regardless of whether the
imported module is importable.
"""

import ast
from pathlib import Path

FORBIDDEN_PREFIXES = ("onevoicecut.adapters", "onevoicecut.runtime")
GUARDED_PACKAGES = ("domain", "usecases", "ports")

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "onevoicecut"


def _iter_guarded_python_files() -> list[Path]:
    files: list[Path] = []
    for package in GUARDED_PACKAGES:
        package_dir = SRC_ROOT / package
        if package_dir.exists():
            files.extend(package_dir.rglob("*.py"))
    return files


def _imported_module_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def _forbidden_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        name
        for name in _imported_module_names(tree)
        if any(
            name == prefix or name.startswith(prefix + ".")
            for prefix in FORBIDDEN_PREFIXES
        )
    }


def test_domain_usecases_ports_never_import_adapters_or_runtime() -> None:
    violations = {
        str(path): forbidden
        for path in _iter_guarded_python_files()
        if (forbidden := _forbidden_imports(path))
    }
    assert not violations, f"Hexagonal boundary violated: {violations}"
