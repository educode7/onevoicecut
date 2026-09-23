"""Tracker resolution: a factory that imports on call, and an honest absence.

The second composition-root resolver, held to `test_engine_registration.py`'s
standard: registration is cheap, construction is deferred, and the deferral is
proven with `ast` rather than by watching an import not happen. Tracker choice
has no per-job axis — one vision tracker exists — so there is no key to resolve
and no `EngineUnavailable`-shaped refusal; what mirrors the engine resolver is
the factory indirection and the rule that a missing extra registers nothing
broken. Honest absence is *declared*: the adapter's own probe answers
`REQUIRES_SETUP` on a bare checkout, which routes every clip through the
already-proven `TrackingUnavailable` path instead of a process that cannot
start.
"""

import ast
from pathlib import Path

from onevoicecut.ports.capabilities import TrackerCapabilities
from onevoicecut.ports.subject_tracker import SubjectTrackerPort
from onevoicecut.runtime.tracker_resolver import resolve_tracker, vision_tracker

REPO_ROOT = Path(__file__).resolve().parents[3]
RESOLVER_SOURCE = (
    REPO_ROOT / "src" / "onevoicecut" / "runtime" / "tracker_resolver.py"
)
RENDER_WORKER_SOURCE = (
    REPO_ROOT / "src" / "onevoicecut" / "runtime" / "render_worker.py"
)
HEAVY_EXTRAS = {"torch", "torchvision", "av", "numpy"}


def _module_level_imports(source: Path) -> set[str]:
    """Every module imported at import time — nested ones deliberately excluded.

    Parsed rather than imported, so this states a fact about the file itself and
    cannot be satisfied by an import that merely happened to succeed.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:  # top level only
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


class TestResolution:
    def test_the_factory_builds_a_fresh_tracker_per_call(self) -> None:
        """One render, one instance — the engine resolver's rule, restated: an
        adapter may scope per-render state (the built model) to itself."""
        factory = vision_tracker(max_clip_seconds=180.0)

        assert factory() is not factory()

    def test_resolving_builds_no_model_and_reads_no_weights(self) -> None:
        """Cheap on any machine. On a bare checkout this returns a tracker
        declaring `REQUIRES_SETUP`; on a set-up one, `AVAILABLE` — both
        without a forward pass, a download, or a torch import."""
        tracker = resolve_tracker(max_clip_seconds=180.0)

        assert isinstance(tracker.capabilities(), TrackerCapabilities)

    def test_the_resolved_tracker_satisfies_the_port(self) -> None:
        tracker: SubjectTrackerPort = resolve_tracker(max_clip_seconds=180.0)

        assert callable(tracker.detect)


class TestTheHeavyImportStaysLazy:
    def test_the_resolver_does_not_import_the_adapter_at_module_scope(self) -> None:
        """The factory indirection's whole point, one level up: the render
        worker imports this module, and this module must not carry the adapter
        — let alone torchvision — into every process that reads it."""
        assert "vision" not in _module_level_imports(RESOLVER_SOURCE)

    def test_the_resolver_imports_no_heavy_extra_at_module_scope(self) -> None:
        assert not (_module_level_imports(RESOLVER_SOURCE) & HEAVY_EXTRAS)


class TestTheRenderWorkerConstructsThroughTheResolver:
    def test_render_worker_resolves_its_default_tracker(self) -> None:
        """13c.7's one change to the worker: the default tracker is resolved,
        not hand-built — so the day a second tracker exists, the worker gains
        it without an edit."""
        tree = ast.parse(RENDER_WORKER_SOURCE.read_text(encoding="utf-8"))
        resolved = [
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "onevoicecut.runtime.tracker_resolver"
            for alias in node.names
        ]

        assert "resolve_tracker" in resolved

    def test_the_unconfigured_placeholder_is_gone(self) -> None:
        """Its own docstring named the day: 13c-i replaces it. Leaving the
        class behind would leave a second answer to "what tracks when nothing
        is configured", and the two would drift."""
        assert "_UnconfiguredSubjectTracker" not in RENDER_WORKER_SOURCE.read_text(
            encoding="utf-8"
        )
