"""Turns the architecture boundary into a failing test.

Legacy hexagonal rules — `domain`, `usecases`, and `ports` must not import
`onevoicecut.adapters` or `onevoicecut.runtime` — stay in force until those
packages no longer exist. Rules are data (`RuleGroup`) held in `RULE_GROUPS`,
the AB-11 registration seam: a migration slice appends its module's group in
the same commit that moves the module, so coverage grows with the tree and no
present code is ever structurally unenforced (AB-12).

Static AST parsing rather than `importlib`: an import statement is a violation
the moment it is written in source text, whether or not the imported package is
importable.
"""

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parent.parent / "src" / "onevoicecut"


@dataclass(frozen=True, slots=True)
class RuleGroup:
    """One named group: guarded subtrees (relative to the package root) and
    the import prefixes those subtrees must never name."""

    name: str
    guarded_subtrees: tuple[str, ...]
    forbidden_prefixes: tuple[str, ...]


LEGACY_HEXAGONAL = RuleGroup(
    name="legacy-hexagonal",
    guarded_subtrees=("domain", "usecases", "ports"),
    forbidden_prefixes=("onevoicecut.adapters", "onevoicecut.runtime"),
)

# AB-08: the shared kernel is domain-agnostic — no file under `shared/` may
# import any `onevoicecut.systems.*` module package.
SHARED_KERNEL = RuleGroup(
    name="shared-kernel",
    guarded_subtrees=("shared",),
    forbidden_prefixes=("onevoicecut.systems",),
)

# AB-03: `shared/domain` is the innermost layer — it may not import the
# shared kernel's own outer layers (infrastructure, application, presentation).
SHARED_DOMAIN_LAYER = RuleGroup(
    name="shared-domain-layer",
    guarded_subtrees=("shared/domain",),
    forbidden_prefixes=(
        "onevoicecut.shared.infrastructure",
        "onevoicecut.shared.application",
        "onevoicecut.shared.presentation",
    ),
)

# AB-11 registration seam: migration slices append their module's RuleGroup
# here, in the slice that migrates the module.
RULE_GROUPS: list[RuleGroup] = [LEGACY_HEXAGONAL, SHARED_KERNEL, SHARED_DOMAIN_LAYER]


def _iter_guarded_python_files(root: Path, subtrees: Sequence[str]) -> list[Path]:
    files: list[Path] = []
    for subtree in subtrees:
        directory = root.joinpath(*subtree.split("/"))
        if directory.exists():
            files.extend(directory.rglob("*.py"))
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


def _forbidden_imports(path: Path, prefixes: Sequence[str]) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        name
        for name in _imported_module_names(tree)
        if any(
            name == prefix or name.startswith(prefix + ".")
            for prefix in prefixes
        )
    }


def _collect_violations(
    root: Path, groups: Sequence[RuleGroup]
) -> dict[str, set[str]]:
    violations: dict[str, set[str]] = {}
    for group in groups:
        for path in _iter_guarded_python_files(root, group.guarded_subtrees):
            if forbidden := _forbidden_imports(path, group.forbidden_prefixes):
                violations[str(path)] = forbidden
    return violations


def register_rule_group(group: RuleGroup) -> None:
    """Add a group to the live registry (contract tests; migration slices
    append their group to `RULE_GROUPS` in source instead)."""
    RULE_GROUPS.append(group)


def check_tree(root: Path) -> dict[str, set[str]]:
    """Evaluate every registered rule group against the tree at `root`."""
    return _collect_violations(root, RULE_GROUPS)


def test_domain_usecases_ports_never_import_adapters_or_runtime() -> None:
    violations = _collect_violations(SRC_ROOT, RULE_GROUPS)
    assert not violations, f"Hexagonal boundary violated: {violations}"


def test_registry_covers_mid_migration_tree_on_both_sides(tmp_path: Path) -> None:
    """AB-11: a registered systems/ scaffold subtree and a legacy subtree are
    both enforced by the same run — the dual-coverage mechanism migration
    slices rely on while domain/usecases/ports still exist."""
    scaffold_dir = tmp_path / "systems" / "pipeline" / "jobs" / "domain"
    scaffold_dir.mkdir(parents=True)
    scaffold_plant = scaffold_dir / "planted.py"
    scaffold_plant.write_text(
        "import onevoicecut.adapters.ffmpeg\n", encoding="utf-8"
    )
    legacy_dir = tmp_path / "domain"
    legacy_dir.mkdir()
    legacy_plant = legacy_dir / "planted.py"
    legacy_plant.write_text(
        "import onevoicecut.runtime.supervisor\n", encoding="utf-8"
    )

    scaffold_group = RuleGroup(
        name="jobs-scaffold",
        guarded_subtrees=("systems/pipeline/jobs/domain",),
        forbidden_prefixes=("onevoicecut.adapters", "onevoicecut.runtime"),
    )
    register_rule_group(scaffold_group)
    try:
        violations = check_tree(tmp_path)
        assert str(scaffold_plant) in violations, (
            f"registered scaffold plant not caught: {violations}"
        )
        assert violations[str(scaffold_plant)] == {"onevoicecut.adapters.ffmpeg"}
        assert str(legacy_plant) in violations, (
            f"legacy plant not caught while a scaffold group was registered: "
            f"{violations}"
        )
        assert violations[str(legacy_plant)] == {"onevoicecut.runtime.supervisor"}
    finally:
        RULE_GROUPS.remove(scaffold_group)


def test_registered_subtree_scan_flags_plants_but_not_compliant_imports(
    tmp_path: Path,
) -> None:
    """Edge: under a registered scaffold subtree the scan must visit files
    (a plant is caught) without flagging imports that name no forbidden
    prefix — a vacuous all-clear would satisfy neither half."""
    scaffold_dir = tmp_path / "systems" / "pipeline" / "jobs" / "domain"
    scaffold_dir.mkdir(parents=True)
    plant = scaffold_dir / "planted.py"
    plant.write_text(
        "from onevoicecut.adapters.storage import core\n", encoding="utf-8"
    )
    compliant = scaffold_dir / "compliant.py"
    compliant.write_text("from pathlib import Path\n", encoding="utf-8")

    scaffold_group = RuleGroup(
        name="jobs-scaffold-scan",
        guarded_subtrees=("systems/pipeline/jobs/domain",),
        forbidden_prefixes=("onevoicecut.adapters", "onevoicecut.runtime"),
    )
    register_rule_group(scaffold_group)
    try:
        violations = check_tree(tmp_path)
        assert str(plant) in violations, f"plant not caught: {violations}"
        assert str(compliant) not in violations, (
            f"compliant import flagged: {violations[str(compliant)]}"
        )
    finally:
        RULE_GROUPS.remove(scaffold_group)


def test_shared_rules_are_registered_and_both_sides_bite(tmp_path: Path) -> None:
    """AB-08/AB-03 (shared side) and AB-11 (both sides): the shared kernel
    rules are live in `RULE_GROUPS`, a `systems` plant under `shared/` fails,
    a shared-outer-layer plant under `shared/domain/` fails, and a legacy
    plant still fails in the same run while `domain/`/`ports/` exist."""
    shared_file = tmp_path / "shared" / "kernel.py"
    shared_file.parent.mkdir(parents=True)
    shared_file.write_text(
        "import onevoicecut.systems.pipeline.jobs.domain\n", encoding="utf-8"
    )
    shared_domain_file = tmp_path / "shared" / "domain" / "errors.py"
    shared_domain_file.parent.mkdir(parents=True, exist_ok=True)
    shared_domain_file.write_text(
        "from onevoicecut.shared.infrastructure.settings import Settings\n",
        encoding="utf-8",
    )
    legacy_file = tmp_path / "domain" / "planted.py"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_file.write_text(
        "import onevoicecut.runtime.supervisor\n", encoding="utf-8"
    )

    registered = {group.name for group in RULE_GROUPS}
    assert "shared-kernel" in registered, registered
    assert "shared-domain-layer" in registered, registered

    violations = check_tree(tmp_path)
    assert violations.get(str(shared_file)) == {
        "onevoicecut.systems.pipeline.jobs.domain"
    }, violations
    assert violations.get(str(shared_domain_file)) == {
        "onevoicecut.shared.infrastructure.settings"
    }, violations
    assert violations.get(str(legacy_file)) == {
        "onevoicecut.runtime.supervisor"
    }, violations
