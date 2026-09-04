"""Generation stops at data. It does not render, and it cannot.

Through proposal rev 3 "no video" was a system-wide non-goal. Rev 4 put vertical
clip rendering in scope, which turned this from a delivery boundary into a
**capability** one: rendering is reached through `VideoRenderPort` and specified
in `clip-rendering`, never from inside generation. The separation is load-bearing
— generation decides *which* moments are worth cutting and knows nothing about
how a frame is cropped.

A boundary that only exists because nobody has crossed it yet is not a boundary,
so this is asserted structurally rather than behaviourally. `GenerationResult`
carries no media handle, and `generate_artifacts` imports nothing that could
produce one — checked by parsing the module rather than by running it, because an
absence cannot be proven by calling something.

The prompt half is here for a related reason. Three prompts are built —
MAP, REDUCE and the script variants — and the invariant they share is the one
this whole change keeps defending: **no prompt hands the model a number an
operator will act on.** One builder means one place to assert it, instead of
three places to remember.
"""

import ast
import inspect
from pathlib import Path

import pytest

from onevoicecut.domain.generation import ClipCandidate, GenerationResult, ScriptVariant
from onevoicecut.usecases import generate_artifacts
from onevoicecut.usecases.generate_artifacts import (
    MapWindow,
    ScriptTarget,
    _fold_prompt,
    _map_prompt,
    _script_prompt,
)

MODULE = Path(inspect.getsourcefile(generate_artifacts) or "")

# Anything that could put a frame on disk, or reach the thing that does.
FORBIDDEN_IMPORTS = (
    "subprocess",
    "onevoicecut.adapters",
    "onevoicecut.runtime",
    "onevoicecut.ports.audio_extractor",
)


def _imported_names() -> set[str]:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


class TestTheResultIsData:
    def test_it_carries_no_media_handle(self) -> None:
        """Not a path, not a file, not a rendered anything — a job id, prose and
        candidates. A field here holding a video would make "generation does not
        render" a convention rather than a fact."""
        assert set(GenerationResult.__dataclass_fields__) == {
            "job_id",
            "summary",
            "clip_candidates",
        }

    def test_a_candidate_carries_no_media_handle(self) -> None:
        """A clip candidate is times plus text. The rendering slice reads those
        times through `VideoRenderPort`; it is not handed a file to fill in."""
        assert set(ClipCandidate.__dataclass_fields__) == {
            "start_s",
            "end_s",
            "hook",
            "quote",
            "rationale",
            "score",
            "variants",
        }

    def test_a_variant_is_text_and_its_shape(self) -> None:
        assert set(ScriptVariant.__dataclass_fields__) == {
            "target",
            "format",
            "body",
            "duration_target_s",
        }


class TestTheModuleCannotRender:
    @pytest.mark.parametrize("forbidden", FORBIDDEN_IMPORTS)
    def test_it_imports_nothing_that_could_produce_a_frame(
        self, forbidden: str
    ) -> None:
        """Parsed rather than imported, and structural rather than behavioural.

        A test that ran generation and checked no file appeared would pass on a
        module that renders only under a flag nobody set. This one fails the day
        the import is written.
        """
        assert not any(name.startswith(forbidden) for name in _imported_names())

    def test_it_writes_nothing_to_disk(self) -> None:
        """Generation hands its result back; storing it is the caller's job and
        the caller's port. A module that wrote its own output would put an
        artifact on disk that no `TranscriptStoragePort` knows about."""
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }

        assert "open" not in called

    def test_it_reaches_no_port_but_text_generation(self) -> None:
        """The one port generation is entitled to. Reaching another from here
        would be the layering violation the architecture test cannot see, since
        `usecases` may legitimately import `ports`."""
        ports = {name for name in _imported_names() if name.startswith("onevoicecut.ports")}

        assert ports == {"onevoicecut.ports.text_generation"}


class TestEveryPromptIsBuiltTheSameWay:
    def _prompts(self) -> list[str]:
        window = MapWindow(segment_ids=(0,), text="[s0000] hola")
        target = ScriptTarget(name="generic", format="plain", duration_target_s=45.0)
        candidate = ClipCandidate(
            start_s=1234.0,
            end_s=1264.0,
            hook="gancho",
            quote="cita",
            rationale="motivo",
            score=0.9,
            variants=(),
        )
        return [
            _map_prompt(window),
            _fold_prompt("resumen A", "resumen B"),
            _script_prompt(candidate, target),
        ]

    def test_each_one_opens_with_its_instruction(self) -> None:
        """The model reads the task before the material. A prompt that buried
        its instruction under three hundred lines of transcript would be a
        prompt whose instruction is advisory."""
        for prompt in self._prompts():
            assert prompt.split("\n")[0].strip()
            assert not prompt.startswith("[s")

    def test_none_of_them_hands_the_model_a_timestamp(self) -> None:
        """The invariant the whole change keeps defending, asserted once for all
        three instead of remembered in three places.

        The candidate above starts at 1234 s. If any prompt path ever begins
        interpolating a time — for context, for framing, for anything — this
        fails, and the fabrication risk is caught at the place it enters rather
        than in whatever the model returns.
        """
        for prompt in self._prompts():
            assert "1234" not in prompt

    def test_they_share_one_builder(self) -> None:
        """So the framing is decided once. Three prompts that drifted apart
        would be three different contracts with the same model, and the one that
        misbehaved would be the hardest to find."""
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        builders = {
            node.name: {
                child.func.id
                for child in ast.walk(node)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
            }
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name.endswith("_prompt")
        }

        assert {"_map_prompt", "_fold_prompt", "_script_prompt"} <= set(builders)
        for name in ("_map_prompt", "_fold_prompt", "_script_prompt"):
            assert "_prompt" in builders[name], f"{name} builds its own framing"
