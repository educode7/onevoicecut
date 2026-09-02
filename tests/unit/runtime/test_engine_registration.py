"""Registering the local engine without dragging it into every import.

The resolver is imported by the composition root, which is imported by most of
the suite. The local adapter imports `faster_whisper` at module level, and that
package is an optional extra — roughly 90 MB of CTranslate2 and onnxruntime
wheels before a single model weight is downloaded.

So registration has to be a factory that imports on call, not on import. If the
resolver reached for the adapter at module scope, `pytest -m "not localmodel"`
would need the local ASR extras installed to collect at all — the suite that
exists precisely so it does not.

The structural half of that is asserted with `ast`, the same way the architecture
test proves the domain imports no adapters: an absence cannot be demonstrated by
running something and watching it not happen.
"""

import ast
from pathlib import Path

import pytest

from onevoicecut.domain.errors import EngineUnavailable
from onevoicecut.domain.jobs import EngineChoice
from onevoicecut.runtime.engine_resolver import EngineResolver, production_factories

RESOLVER_SOURCE = (
    Path(__file__).resolve().parents[3]
    / "src"
    / "onevoicecut"
    / "runtime"
    / "engine_resolver.py"
)
MODEL_SIZE = "tiny"


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


class TestRegistration:
    def test_the_local_engine_is_registered(self) -> None:
        factories = production_factories(local_model_size=MODEL_SIZE)

        assert EngineChoice.LOCAL in factories

    def test_the_cloud_engine_is_not_registered_yet(self) -> None:
        """And must stay unregistered until its adapter exists.

        The resolver never substitutes one engine for another, so an
        unregistered engine is an error at resolution — which is the correct,
        loud outcome for a job that asked for something this build cannot do.
        """
        factories = production_factories(local_model_size=MODEL_SIZE)

        with pytest.raises(EngineUnavailable):
            EngineResolver(factories).resolve(EngineChoice.CLOUD)

    def test_the_refusal_names_what_is_configured(self) -> None:
        """An operator reading this needs to know which engines this build has,
        not merely that theirs is missing."""
        with pytest.raises(EngineUnavailable) as refusal:
            EngineResolver(production_factories(local_model_size=MODEL_SIZE)).resolve(
                EngineChoice.CLOUD
            )

        assert "local" in str(refusal.value)


class TestTheHeavyImportStaysLazy:
    def test_the_resolver_does_not_import_the_local_engine_at_module_scope(
        self,
    ) -> None:
        """The whole point of the factory indirection.

        `faster_whisper` at module level here would make the composition root —
        and therefore most of the suite — unimportable without the optional
        extras installed.
        """
        assert "faster_whisper" not in _module_level_imports(RESOLVER_SOURCE)

    def test_it_does_not_import_the_adapter_module_at_module_scope_either(
        self,
    ) -> None:
        """One level of indirection removed: the adapter module is what carries
        the `faster_whisper` import, so importing *it* eagerly is the same
        mistake wearing a different name."""
        tree = ast.parse(RESOLVER_SOURCE.read_text(encoding="utf-8"))
        top_level = [
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        ]

        assert not [m for m in top_level if "asr" in m]

    def test_building_the_factory_map_loads_no_model(self) -> None:
        """Registration is cheap; construction is not.

        `production_factories` must be callable in the default suite — it is, in
        this very test — which is only true while nothing is constructed until
        `resolve()` is called.
        """
        factories = production_factories(local_model_size=MODEL_SIZE)

        assert callable(factories[EngineChoice.LOCAL])
