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

import pytest

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

# Slice 2a — the jobs module's rules, registered in the slice that migrates it
# (AB-11: no window in which `systems/pipeline/jobs` exists unenforced).
#
# AB-04/AB-05 + the Domain Import Prohibitions: a module's domain keeps the
# load-bearing hexagonal rule — zero third-party imports, no adapters, no
# runtime — and reaches nothing above itself in the layer matrix.
JOBS_DOMAIN = RuleGroup(
    name="jobs-domain",
    guarded_subtrees=("systems/pipeline/jobs/domain",),
    forbidden_prefixes=(
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "onevoicecut.adapters",
        "onevoicecut.runtime",
        "onevoicecut.shared.infrastructure",
        "onevoicecut.shared.application",
        "onevoicecut.shared.presentation",
        "onevoicecut.systems.pipeline.jobs.presentation",
        "onevoicecut.systems.pipeline.jobs.application",
        "onevoicecut.systems.pipeline.jobs.infrastructure",
    ),
)

# AB-02: application imports its own module's domain and nothing outward.
# Presentation and infrastructure are bound at composition roots (AB-09/AB-10
# cover the two concrete ways out), and another module's domain or
# infrastructure is cross-module contact that must go through an interface
# declared here (AB-06's plant is the transcripts half of that rule).
JOBS_APPLICATION = RuleGroup(
    name="jobs-application",
    guarded_subtrees=("systems/pipeline/jobs/application",),
    forbidden_prefixes=(
        "onevoicecut.systems.pipeline.jobs.presentation",
        "onevoicecut.systems.pipeline.jobs.infrastructure",
        "onevoicecut.systems.pipeline.transcripts.domain",
        "onevoicecut.systems.pipeline.transcripts.infrastructure",
        "onevoicecut.systems.pipeline.clips.domain",
        "onevoicecut.systems.pipeline.clips.infrastructure",
        "onevoicecut.adapters",
        "onevoicecut.runtime",
    ),
)

# AB-01: presentation imports its own module's application plus FastAPI and
# Pydantic — never an infrastructure package and never a concrete adapter, both
# of which are constructed and bound at a composition root (AB-09).
JOBS_PRESENTATION = RuleGroup(
    name="jobs-presentation",
    guarded_subtrees=("systems/pipeline/jobs/presentation",),
    forbidden_prefixes=(
        "onevoicecut.systems.pipeline.jobs.infrastructure",
        "onevoicecut.systems.pipeline.transcripts.infrastructure",
        "onevoicecut.systems.pipeline.clips.infrastructure",
        "onevoicecut.adapters",
        "onevoicecut.runtime",
        # The web composition root (4e). Same edge as the two above: a
        # controller may not name the object a module API hands its route
        # factory, so it cannot reach the root that constructs adapters either.
        "onevoicecut.main",
    ),
)

# AB-07: `jobs.domain` is guarded from the *other side* — a foreign module's
# domain layer may not reach into it. The rule is anchored on the module that
# owns the types, so it lands with slice 2a rather than waiting for the module
# that has not migrated yet; the guarded subtrees simply do not exist until
# those slices put files there.
JOBS_DOMAIN_ISOLATION = RuleGroup(
    name="jobs-domain-isolation",
    guarded_subtrees=(
        "systems/pipeline/transcripts/domain",
        "systems/pipeline/clips/domain",
    ),
    forbidden_prefixes=("onevoicecut.systems.pipeline.jobs.domain",),
)

# Slice 3a — the transcripts module's rules, registered in the slice that
# migrates it (AB-11: no window in which `systems/pipeline/transcripts` exists
# unenforced).
#
# AB-04/AB-05 + the Domain Import Prohibitions: a module's domain keeps the
# load-bearing hexagonal rule — zero third-party imports, no adapters, no
# runtime — and reaches nothing above itself in the layer matrix. The
# cross-module half of AB-07 is stated here too: `clips.domain` is refused
# outright, and `jobs.domain` is restated although `jobs-domain-isolation`
# (registered in 2a, anchored on the module that owns the types) already walks
# this subtree — a second line of defense for the one edge slice 3a's port
# relocation has to satisfy.
TRANSCRIPTS_DOMAIN = RuleGroup(
    name="transcripts-domain",
    guarded_subtrees=("systems/pipeline/transcripts/domain",),
    forbidden_prefixes=(
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "onevoicecut.adapters",
        "onevoicecut.runtime",
        "onevoicecut.shared.infrastructure",
        "onevoicecut.shared.application",
        "onevoicecut.shared.presentation",
        "onevoicecut.systems.pipeline.transcripts.presentation",
        "onevoicecut.systems.pipeline.transcripts.application",
        "onevoicecut.systems.pipeline.transcripts.infrastructure",
        "onevoicecut.systems.pipeline.jobs.domain",
        "onevoicecut.systems.pipeline.clips.domain",
    ),
)

# AB-02/AB-06: application imports its own module's domain and nothing
# outward. Both siblings' infrastructure is composition-bound, and
# `jobs.infrastructure` is the contact AB-06's scenario names. `jobs.domain`
# is deliberately *not* refused: the behavior-frozen transcription body owns a
# job's lifecycle and names `JobRecord`/`JobState` while it runs, so forbidding
# it would fail the tree the moment slice 3c lands that handler — severing
# that coupling is a behavior change, not a relocation, and no scenario in
# this slice tests it. `clips` is refused in full because nothing in the
# transcripts tree names it: clips consumes transcripts' artifacts, never the
# reverse.
TRANSCRIPTS_APPLICATION = RuleGroup(
    name="transcripts-application",
    guarded_subtrees=("systems/pipeline/transcripts/application",),
    forbidden_prefixes=(
        "onevoicecut.systems.pipeline.transcripts.presentation",
        "onevoicecut.systems.pipeline.transcripts.infrastructure",
        "onevoicecut.systems.pipeline.jobs.infrastructure",
        "onevoicecut.systems.pipeline.clips.domain",
        "onevoicecut.systems.pipeline.clips.infrastructure",
        "onevoicecut.adapters",
        "onevoicecut.runtime",
    ),
)

# AB-01/AB-09: presentation imports its own module's application plus FastAPI
# and Pydantic — never an infrastructure package and never a concrete adapter,
# both of which are constructed and bound at a composition root. Transcripts is
# worker-driven and may never grow a router; the group still registers, proved
# against plants rather than against real code that does not exist.
TRANSCRIPTS_PRESENTATION = RuleGroup(
    name="transcripts-presentation",
    guarded_subtrees=("systems/pipeline/transcripts/presentation",),
    forbidden_prefixes=(
        "onevoicecut.systems.pipeline.transcripts.infrastructure",
        "onevoicecut.systems.pipeline.jobs.infrastructure",
        "onevoicecut.systems.pipeline.clips.infrastructure",
        "onevoicecut.adapters",
        "onevoicecut.runtime",
    ),
)

# Slice 4a — the clips module's rules, registered in the slice that migrates
# it (AB-11: no window in which `systems/pipeline/clips` exists unenforced).
#
# AB-04/AB-05 + the Domain Import Prohibitions: a module's domain keeps the
# load-bearing hexagonal rule — zero third-party imports, no adapters, no
# runtime — and reaches nothing above itself in the layer matrix. The
# cross-module half of AB-07 is stated here too: both siblings' domains are
# refused outright, so a clips domain module can never reach back into the
# module that owns a job record or a transcript.
CLIPS_DOMAIN = RuleGroup(
    name="clips-domain",
    guarded_subtrees=("systems/pipeline/clips/domain",),
    forbidden_prefixes=(
        "fastapi",
        "pydantic",
        "sqlalchemy",
        "onevoicecut.adapters",
        "onevoicecut.runtime",
        "onevoicecut.shared.infrastructure",
        "onevoicecut.shared.application",
        "onevoicecut.shared.presentation",
        "onevoicecut.systems.pipeline.clips.presentation",
        "onevoicecut.systems.pipeline.clips.application",
        "onevoicecut.systems.pipeline.clips.infrastructure",
        "onevoicecut.systems.pipeline.jobs.domain",
        "onevoicecut.systems.pipeline.transcripts.domain",
    ),
)

# AB-02/AB-06/AB-10: application imports its own module's domain and nothing
# outward. Both siblings' infrastructure is composition-bound, and
# `jobs.infrastructure` is the contact AB-06's scenario names.
# `transcripts.domain` is deliberately *not* refused: the behavior-frozen
# generation and subtitle bodies read the transcript directly, so forbidding
# it would fail the tree the moment slice 4c lands those handlers — severing
# that coupling is a behavior change, not a relocation, and no scenario in
# this slice tests it (the exact argument TRANSCRIPTS_APPLICATION makes for
# `jobs.domain`).
CLIPS_APPLICATION = RuleGroup(
    name="clips-application",
    guarded_subtrees=("systems/pipeline/clips/application",),
    forbidden_prefixes=(
        "onevoicecut.systems.pipeline.clips.presentation",
        "onevoicecut.systems.pipeline.clips.infrastructure",
        "onevoicecut.systems.pipeline.jobs.infrastructure",
        "onevoicecut.systems.pipeline.transcripts.infrastructure",
        "onevoicecut.adapters",
        "onevoicecut.runtime",
    ),
)

# AB-01/AB-09: presentation imports its own module's application plus FastAPI
# and Pydantic — never an infrastructure package and never a concrete adapter,
# both of which are constructed and bound at a composition root (AB-09). Slice
# 4e built the tree this group was registered against — the three clip
# operations — so it now walks real code as well as the plants below, and it
# refuses `onevoicecut.main` on the same reasoning as `jobs-presentation`.
CLIPS_PRESENTATION = RuleGroup(
    name="clips-presentation",
    guarded_subtrees=("systems/pipeline/clips/presentation",),
    forbidden_prefixes=(
        "onevoicecut.systems.pipeline.clips.infrastructure",
        "onevoicecut.systems.pipeline.jobs.infrastructure",
        "onevoicecut.systems.pipeline.transcripts.infrastructure",
        "onevoicecut.adapters",
        "onevoicecut.runtime",
        "onevoicecut.main",
    ),
)

# AB-11 registration seam: migration slices append their module's RuleGroup
# here, in the slice that migrates the module.
RULE_GROUPS: list[RuleGroup] = [
    LEGACY_HEXAGONAL,
    SHARED_KERNEL,
    SHARED_DOMAIN_LAYER,
    JOBS_DOMAIN,
    JOBS_APPLICATION,
    JOBS_PRESENTATION,
    JOBS_DOMAIN_ISOLATION,
    TRANSCRIPTS_DOMAIN,
    TRANSCRIPTS_APPLICATION,
    TRANSCRIPTS_PRESENTATION,
    CLIPS_DOMAIN,
    CLIPS_APPLICATION,
    CLIPS_PRESENTATION,
]


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


# Slice 2a — the jobs module's rule groups (AB-01, AB-02, AB-04, AB-05, AB-06,
# AB-07, AB-09, AB-10). The names below are the registration seam the tests
# assert: the slice that migrates `systems/pipeline/jobs` appends its groups to
# `RULE_GROUPS` in the same commit, so the module never exists unenforced.
JOBS_RULE_GROUP_NAMES = (
    "jobs-domain",
    "jobs-application",
    "jobs-presentation",
    "jobs-domain-isolation",
)

# One entry per architecture-boundary scenario: (case, file under the guarded
# tree, planted source text, the import the report must name). Each case is a
# separate parametrization so a failure names its own file rather than a
# bundle.
JOBS_PLANT_CASES: tuple[tuple[str, str, str, str], ...] = (
    (
        "ab-01-presentation-imports-infrastructure",
        "systems/pipeline/jobs/presentation/v1/routes.py",
        "from onevoicecut.systems.pipeline.jobs.infrastructure.storage import"
        " FilesystemJobStore\n",
        "onevoicecut.systems.pipeline.jobs.infrastructure.storage",
    ),
    (
        "ab-02-application-imports-presentation",
        "systems/pipeline/jobs/application/use_cases/commands/admit_job.py",
        "from onevoicecut.systems.pipeline.jobs.presentation.v1.schemas import"
        " AdmitJobRequest\n",
        "onevoicecut.systems.pipeline.jobs.presentation.v1.schemas",
    ),
    (
        "ab-04-domain-imports-web-framework",
        "systems/pipeline/jobs/domain/jobs.py",
        "import fastapi\n",
        "fastapi",
    ),
    (
        "ab-05-domain-imports-adapters",
        "systems/pipeline/jobs/domain/media.py",
        "import onevoicecut.adapters.ffmpeg.extractor\n",
        "onevoicecut.adapters.ffmpeg.extractor",
    ),
    (
        "ab-06-application-imports-transcripts-infrastructure",
        "systems/pipeline/jobs/application/use_cases/commands/transcribe.py",
        "import onevoicecut.systems.pipeline.transcripts.infrastructure\n",
        "onevoicecut.systems.pipeline.transcripts.infrastructure",
    ),
    (
        "ab-07-clips-domain-imports-jobs-domain",
        "systems/pipeline/clips/domain/generation.py",
        "from onevoicecut.systems.pipeline.jobs.domain.jobs import JobRecord\n",
        "onevoicecut.systems.pipeline.jobs.domain.jobs",
    ),
    (
        # Both halves of AB-09 get a live positive control: presentation must
        # reach neither the composition object the module API hands a route
        # factory, nor the concrete storage adapters its own infrastructure
        # holds — the second is the import slice 2e moved into this module.
        "ab-09-presentation-imports-concrete-adapter",
        "systems/pipeline/jobs/presentation/v1/controllers/upload.py",
        "from onevoicecut.adapters.web.app import WebDependencies\n",
        "onevoicecut.adapters.web.app",
    ),
    (
        # 4e moved that composition object into `main.py` and drained the
        # package the plant above names. The plant above stays, per the 4d
        # precedent for a rule that outlives the module it was written against
        # — the matcher still has to bite on it. This one is the live control:
        # a presentation module may not reach the composition root at all,
        # whether or not the object it would import still exists.
        "ab-09-presentation-imports-web-composition-root",
        "systems/pipeline/jobs/presentation/v1/controllers/upload.py",
        "from onevoicecut.main import WebDependencies\n",
        "onevoicecut.main",
    ),
    (
        "ab-09b-presentation-imports-own-infrastructure",
        "systems/pipeline/jobs/presentation/v1/controllers/upload.py",
        "from onevoicecut.systems.pipeline.jobs.infrastructure.media_source import"
        " FilesystemMediaSource\n",
        "onevoicecut.systems.pipeline.jobs.infrastructure.media_source",
    ),
    (
        "ab-10-application-imports-runtime",
        "systems/pipeline/jobs/application/use_cases/commands/cancel_job.py",
        "import onevoicecut.runtime.supervisor\n",
        "onevoicecut.runtime.supervisor",
    ),
)


def test_jobs_rule_groups_are_registered() -> None:
    """AB-11: the slice that migrates `jobs` registers the module's rules in
    the same slice — there is no window in which `systems/pipeline/jobs` exists
    while nothing walks it."""
    registered = {group.name for group in RULE_GROUPS}
    for name in JOBS_RULE_GROUP_NAMES:
        assert name in registered, (
            f"{name} rule group not registered; coverage would be vacuous for "
            f"the jobs tree: {sorted(registered)}"
        )


@pytest.mark.parametrize(
    ("relative_path", "source", "expected"),
    [case[1:] for case in JOBS_PLANT_CASES],
    ids=[case[0] for case in JOBS_PLANT_CASES],
)
def test_jobs_plant_fails_naming_its_file(
    tmp_path: Path, relative_path: str, source: str, expected: str
) -> None:
    """AB-12: every planted jobs violation fails the default run naming the
    file it was planted in, and the report names the forbidden import — the
    rule is proven against the plant before it guards real code."""
    plant = tmp_path / relative_path
    plant.parent.mkdir(parents=True, exist_ok=True)
    plant.write_text(source, encoding="utf-8")

    violations = check_tree(tmp_path)

    assert str(plant) in violations, (
        f"planted violation at {relative_path} not caught: {violations}"
    )
    assert violations[str(plant)] == {expected}


def test_jobs_and_legacy_plants_fail_in_the_same_run(tmp_path: Path) -> None:
    """AB-11 both sides in one run: while `domain/`, `usecases/` and `ports/`
    still exist, a legacy plant is caught alongside a jobs plant — registering
    the new group may not cost coverage of code that is still there."""
    legacy_plant = tmp_path / "usecases" / "admit_job.py"
    legacy_plant.parent.mkdir(parents=True, exist_ok=True)
    legacy_plant.write_text(
        "import onevoicecut.runtime.supervisor\n", encoding="utf-8"
    )
    jobs_plant = tmp_path / "systems" / "pipeline" / "jobs" / "domain" / "jobs.py"
    jobs_plant.parent.mkdir(parents=True, exist_ok=True)
    jobs_plant.write_text("import pydantic\n", encoding="utf-8")

    violations = check_tree(tmp_path)

    assert str(legacy_plant) in violations, (
        f"legacy plant not caught while the jobs rules were registered: "
        f"{violations}"
    )
    assert violations[str(legacy_plant)] == {"onevoicecut.runtime.supervisor"}
    assert str(jobs_plant) in violations, (
        f"jobs plant not caught: {violations}"
    )
    assert violations[str(jobs_plant)] == {"pydantic"}


# Slice 3a — the transcripts module's rule groups (AB-01, AB-02, AB-04, AB-05,
# AB-06, AB-07, AB-09, AB-10). The names below are the registration seam the
# tests assert: the slice that migrates `systems/pipeline/transcripts` appends
# its groups to `RULE_GROUPS` in the same commit, so the module never exists
# unenforced.
TRANSCRIPTS_RULE_GROUP_NAMES = (
    "transcripts-domain",
    "transcripts-application",
    "transcripts-presentation",
)

# One entry per architecture-boundary scenario: (case, file under the guarded
# tree, planted source text, the import the report must name). Each case is a
# separate parametrization so a failure names its own file rather than a
# bundle.
TRANSCRIPTS_PLANT_CASES: tuple[tuple[str, str, str, str], ...] = (
    (
        "ab-01-presentation-imports-infrastructure",
        "systems/pipeline/transcripts/presentation/v1/routes.py",
        "from onevoicecut.systems.pipeline.transcripts.infrastructure.storage import"
        " TranscriptStore\n",
        "onevoicecut.systems.pipeline.transcripts.infrastructure.storage",
    ),
    (
        "ab-02-application-imports-presentation",
        "systems/pipeline/transcripts/application/use_cases/commands/transcribe.py",
        "import onevoicecut.systems.pipeline.transcripts.presentation\n",
        "onevoicecut.systems.pipeline.transcripts.presentation",
    ),
    (
        "ab-04-domain-imports-web-framework",
        "systems/pipeline/transcripts/domain/chunking.py",
        "import fastapi\n",
        "fastapi",
    ),
    (
        "ab-05-domain-imports-adapters",
        "systems/pipeline/transcripts/domain/transcript.py",
        "import onevoicecut.adapters.ffmpeg.extractor\n",
        "onevoicecut.adapters.ffmpeg.extractor",
    ),
    (
        # The plant task 3a.1 names: AB-06's scenario from this module's side.
        "ab-06-application-imports-jobs-infrastructure",
        "systems/pipeline/transcripts/application/use_cases/commands/plan_chunks.py",
        "import onevoicecut.systems.pipeline.jobs.infrastructure\n",
        "onevoicecut.systems.pipeline.jobs.infrastructure",
    ),
    (
        # The transcripts-side restatement of AB-07, and the exact edge slice
        # 3a's port relocation must clear: `jobs-domain-isolation` (2a) already
        # walks this subtree from the owner's side, so this case is pinned
        # rather than proven here — the clips case below is the one edge only
        # this group walks.
        "ab-07-transcripts-domain-imports-jobs-domain",
        "systems/pipeline/transcripts/domain/interfaces/audio_extractor.py",
        "from onevoicecut.systems.pipeline.jobs.domain.media import SourceMedia\n",
        "onevoicecut.systems.pipeline.jobs.domain.media",
    ),
    (
        "ab-07-transcripts-domain-imports-clips-domain",
        "systems/pipeline/transcripts/domain/chunking.py",
        "from onevoicecut.systems.pipeline.clips.domain.generation import"
        " ClipCandidate\n",
        "onevoicecut.systems.pipeline.clips.domain.generation",
    ),
    (
        "ab-09-presentation-imports-concrete-adapter",
        "systems/pipeline/transcripts/presentation/v1/controllers/transcripts.py",
        "from onevoicecut.adapters.ffmpeg.extractor import FfmpegAudioExtractor\n",
        "onevoicecut.adapters.ffmpeg.extractor",
    ),
    (
        "ab-10-application-imports-runtime",
        "systems/pipeline/transcripts/application/use_cases/commands/"
        "stitch_transcript.py",
        "import onevoicecut.runtime.supervisor\n",
        "onevoicecut.runtime.supervisor",
    ),
)


def test_transcripts_rule_groups_are_registered() -> None:
    """AB-11: the slice that migrates `transcripts` registers the module's
    rules in the same slice — there is no window in which
    `systems/pipeline/transcripts` exists while nothing walks it."""
    registered = {group.name for group in RULE_GROUPS}
    for name in TRANSCRIPTS_RULE_GROUP_NAMES:
        assert name in registered, (
            f"{name} rule group not registered; coverage would be vacuous for "
            f"the transcripts tree: {sorted(registered)}"
        )


@pytest.mark.parametrize(
    ("relative_path", "source", "expected"),
    [case[1:] for case in TRANSCRIPTS_PLANT_CASES],
    ids=[case[0] for case in TRANSCRIPTS_PLANT_CASES],
)
def test_transcripts_plant_fails_naming_its_file(
    tmp_path: Path, relative_path: str, source: str, expected: str
) -> None:
    """AB-12: every planted transcripts violation fails the default run naming
    the file it was planted in, and the report names the forbidden import — the
    rule is proven against the plant before it guards real code."""
    plant = tmp_path / relative_path
    plant.parent.mkdir(parents=True, exist_ok=True)
    plant.write_text(source, encoding="utf-8")

    violations = check_tree(tmp_path)

    assert str(plant) in violations, (
        f"planted violation at {relative_path} not caught: {violations}"
    )
    assert violations[str(plant)] == {expected}


def test_transcripts_and_legacy_plants_fail_in_the_same_run(tmp_path: Path) -> None:
    """AB-11 both sides in one run: while `domain/`, `usecases/` and `ports/`
    still exist, a legacy plant is caught alongside a transcripts plant —
    registering the new groups may not cost coverage of code that is still
    there."""
    legacy_plant = tmp_path / "ports" / "transcription.py"
    legacy_plant.parent.mkdir(parents=True, exist_ok=True)
    legacy_plant.write_text(
        "import onevoicecut.adapters.ffmpeg.extractor\n", encoding="utf-8"
    )
    transcripts_plant = (
        tmp_path / "systems" / "pipeline" / "transcripts" / "domain" / "chunking.py"
    )
    transcripts_plant.parent.mkdir(parents=True, exist_ok=True)
    transcripts_plant.write_text("import pydantic\n", encoding="utf-8")

    violations = check_tree(tmp_path)

    assert str(legacy_plant) in violations, (
        f"legacy plant not caught while the transcripts rules were registered: "
        f"{violations}"
    )
    assert violations[str(legacy_plant)] == {
        "onevoicecut.adapters.ffmpeg.extractor"
    }
    assert str(transcripts_plant) in violations, (
        f"transcripts plant not caught: {violations}"
    )
    assert violations[str(transcripts_plant)] == {"pydantic"}


# Slice 4a — the clips module's rule groups (AB-01, AB-02, AB-04, AB-05,
# AB-06, AB-07, AB-09, AB-10). The names below are the registration seam the
# tests assert: the slice that migrates `systems/pipeline/clips` appends its
# groups to `RULE_GROUPS` in the same commit, so the module never exists
# unenforced.
CLIPS_RULE_GROUP_NAMES = (
    "clips-domain",
    "clips-application",
    "clips-presentation",
)

# One entry per architecture-boundary scenario: (case, file under the guarded
# tree, planted source text, the import the report must name). Each case is a
# separate parametrization so a failure names its own file rather than a
# bundle.
CLIPS_PLANT_CASES: tuple[tuple[str, str, str, str], ...] = (
    (
        "ab-01-presentation-imports-infrastructure",
        "systems/pipeline/clips/presentation/v1/routes.py",
        "from onevoicecut.systems.pipeline.clips.infrastructure.storage import"
        " FilesystemClipStore\n",
        "onevoicecut.systems.pipeline.clips.infrastructure.storage",
    ),
    (
        "ab-02-application-imports-presentation",
        "systems/pipeline/clips/application/use_cases/commands/render_clip.py",
        "import onevoicecut.systems.pipeline.clips.presentation\n",
        "onevoicecut.systems.pipeline.clips.presentation",
    ),
    (
        "ab-04-domain-imports-web-framework",
        "systems/pipeline/clips/domain/rendering.py",
        "import fastapi\n",
        "fastapi",
    ),
    (
        "ab-05-domain-imports-adapters",
        "systems/pipeline/clips/domain/framing.py",
        "import onevoicecut.adapters.ffmpeg.video_render\n",
        "onevoicecut.adapters.ffmpeg.video_render",
    ),
    (
        # The plant task 4a.1 names: AB-06's scenario from this module's side.
        "ab-06-application-imports-jobs-infrastructure",
        "systems/pipeline/clips/application/use_cases/commands/"
        "request_clip_export.py",
        "import onevoicecut.systems.pipeline.jobs.infrastructure\n",
        "onevoicecut.systems.pipeline.jobs.infrastructure",
    ),
    (
        # AB-07's own scenario. `jobs-domain-isolation` (2a, anchored on the
        # module that owns the types) already walks this subtree, so this case
        # is pinned rather than proven here — the transcripts case below is
        # the one edge only this group walks.
        "ab-07-clips-domain-imports-jobs-domain",
        "systems/pipeline/clips/domain/generation.py",
        "from onevoicecut.systems.pipeline.jobs.domain.jobs import JobRecord\n",
        "onevoicecut.systems.pipeline.jobs.domain.jobs",
    ),
    (
        "ab-07-clips-domain-imports-transcripts-domain",
        "systems/pipeline/clips/domain/generation.py",
        "from onevoicecut.systems.pipeline.transcripts.domain.transcript import"
        " Transcript\n",
        "onevoicecut.systems.pipeline.transcripts.domain.transcript",
    ),
    (
        "ab-09-presentation-imports-concrete-adapter",
        "systems/pipeline/clips/presentation/v1/controllers/clips.py",
        "from onevoicecut.adapters.web.app import WebDependencies\n",
        "onevoicecut.adapters.web.app",
    ),
    (
        # 4e's live control: the composition object moved into `main.py`
        # alongside the clip routes this module now owns, so the import a
        # presentation module would be tempted to write names the root. The
        # plant above stays per the 4d precedent for a rule that outlives the
        # module it was written against; this is the one that bites today.
        "ab-09-presentation-imports-web-composition-root",
        "systems/pipeline/clips/presentation/v1/controllers/clips.py",
        "from onevoicecut.main import WebDependencies\n",
        "onevoicecut.main",
    ),
    (
        "ab-10-application-imports-runtime",
        "systems/pipeline/clips/application/use_cases/commands/render_clip.py",
        "import onevoicecut.runtime.render_worker\n",
        "onevoicecut.runtime.render_worker",
    ),
)


def test_clips_rule_groups_are_registered() -> None:
    """AB-11: the slice that migrates `clips` registers the module's rules in
    the same slice — there is no window in which `systems/pipeline/clips`
    exists while nothing walks it."""
    registered = {group.name for group in RULE_GROUPS}
    for name in CLIPS_RULE_GROUP_NAMES:
        assert name in registered, (
            f"{name} rule group not registered; coverage would be vacuous for "
            f"the clips tree: {sorted(registered)}"
        )


@pytest.mark.parametrize(
    ("relative_path", "source", "expected"),
    [case[1:] for case in CLIPS_PLANT_CASES],
    ids=[case[0] for case in CLIPS_PLANT_CASES],
)
def test_clips_plant_fails_naming_its_file(
    tmp_path: Path, relative_path: str, source: str, expected: str
) -> None:
    """AB-12: every planted clips violation fails the default run naming the
    file it was planted in, and the report names the forbidden import — the
    rule is proven against the plant before it guards real code."""
    plant = tmp_path / relative_path
    plant.parent.mkdir(parents=True, exist_ok=True)
    plant.write_text(source, encoding="utf-8")

    violations = check_tree(tmp_path)

    assert str(plant) in violations, (
        f"planted violation at {relative_path} not caught: {violations}"
    )
    assert violations[str(plant)] == {expected}


def test_clips_and_legacy_plants_fail_in_the_same_run(tmp_path: Path) -> None:
    """AB-11 both sides in one run: while `domain/`, `usecases/` and `ports/`
    still exist, a legacy plant is caught alongside a clips plant —
    registering the new groups may not cost coverage of code that is still
    there."""
    legacy_plant = tmp_path / "domain" / "generation.py"
    legacy_plant.parent.mkdir(parents=True, exist_ok=True)
    legacy_plant.write_text(
        "import onevoicecut.runtime.render_worker\n", encoding="utf-8"
    )
    clips_plant = tmp_path / "systems" / "pipeline" / "clips" / "domain" / "framing.py"
    clips_plant.parent.mkdir(parents=True, exist_ok=True)
    clips_plant.write_text("import pydantic\n", encoding="utf-8")

    violations = check_tree(tmp_path)

    assert str(legacy_plant) in violations, (
        f"legacy plant not caught while the clips rules were registered: "
        f"{violations}"
    )
    assert violations[str(legacy_plant)] == {"onevoicecut.runtime.render_worker"}
    assert str(clips_plant) in violations, (
        f"clips plant not caught: {violations}"
    )
    assert violations[str(clips_plant)] == {"pydantic"}
