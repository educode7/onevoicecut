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
import json
import re
from pathlib import Path

import pytest

from onevoicecut.shared.domain.errors import GenerationFailed
from onevoicecut.systems.pipeline.clips.domain.generation import ClipCandidate, GenerationResult, ScriptVariant
from onevoicecut.systems.pipeline.clips.application.use_cases.commands import generate_artifacts
from onevoicecut.systems.pipeline.clips.application.use_cases.commands.generate_artifacts import (
    MapWindow,
    ScriptTarget,
    _fold_prompt,
    _map_prompt,
    _script_prompt,
    parse_map_response,
)

MODULE = Path(inspect.getsourcefile(generate_artifacts) or "")

# Anything that could put a frame on disk, or reach the thing that does.
FORBIDDEN_IMPORTS = (
    "subprocess",
    "onevoicecut.adapters",
    "onevoicecut.runtime",
    "onevoicecut.systems.pipeline.transcripts.domain.interfaces.audio_extractor",
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
        artifact on disk that no `ClipStore` knows about."""
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
        `usecases` may legitimately import `ports`.

        The filter names both spellings of that seam: the legacy `onevoicecut.ports`
        package and the `domain/interfaces` package every module's narrow Protocols
        moved behind in slice 4a. Matching only the old one would have left this
        asserting against an empty set — passing for the wrong reason, which is
        worse than failing.
        """
        ports = {
            name
            for name in _imported_names()
            if name.startswith("onevoicecut.ports") or ".domain.interfaces." in name
        }

        assert ports == {
            "onevoicecut.systems.pipeline.clips.domain.interfaces.text_generation"
        }


class TestEveryPromptIsBuiltTheSameWay:
    def _prompts(self) -> list[str]:
        window = MapWindow(segment_ids=(0,), text="[s0000] hola")
        target = ScriptTarget(
            name="tiktok", format="plain", duration_target_s=45.0, profile="vertical"
        )
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


class TestTheScoreContractReachesTheModel:
    """`Moment.score` is bounded so ranking compares like with like, but a bound
    the model is never told is a bound it cannot respect: the e2e run answered
    `8.5` to an instruction that only said `float`, and the refusal was right
    while the prompt was wrong.

    The bounds are extracted from the two messages themselves rather than
    imported from a constant and compared against it — a test holding the same
    constant on both sides of `==` would pass on a prompt that never mentions
    it. Prompt, refusal and enforced behaviour are three places, and this pins
    all three against each other.
    """

    _BOUNDS = re.compile(r"(\d+(?:\.\d+)?)\.\.(\d+(?:\.\d+)?)")

    @staticmethod
    def _window() -> MapWindow:
        return MapWindow(segment_ids=(0,), text="[s0000] hola")

    @staticmethod
    def _answer(score: object) -> str:
        return json.dumps(
            {
                "summary": "resumen",
                "moments": [
                    {
                        "segment_ids": [0],
                        "hook": "gancho",
                        "quote": "cita",
                        "rationale": "motivo",
                        "score": score,
                    }
                ],
            }
        )

    def _refusal_for(self, score: object) -> str:
        with pytest.raises(GenerationFailed) as refusal:
            parse_map_response(self._answer(score), self._window())
        return str(refusal.value)

    def _stated_bounds(self, message: str) -> tuple[float, float]:
        match = self._BOUNDS.search(message)
        assert match is not None, f"no score bounds stated in {message!r}"
        return float(match.group(1)), float(match.group(2))

    def test_the_instruction_states_the_bounds_the_refusal_quotes(self) -> None:
        """The defect, pinned from both ends: the refusal says `0..1`, so the
        prompt that produced the score must say the same `0..1` — otherwise the
        model is refused against a contract it was never shown."""
        refusal = self._refusal_for(8.5)

        assert self._stated_bounds(_map_prompt(self._window())) == self._stated_bounds(
            refusal
        )
        # The refusal reports what the model actually sent, not a rescaled
        # reading: a normalised 8.5 would hide the disagreement it exists to stop.
        assert "8.5" in refusal

    def test_the_bounds_stated_are_the_bounds_enforced(self) -> None:
        """The numbers the prompt states are the numbers `_read_moment` enforces
        — inclusive endpoints, refusal just outside. A prompt describing one
        range while the validator applies another is the same drift as a prompt
        describing none."""
        low, high = self._stated_bounds(_map_prompt(self._window()))

        for endpoint in (low, high):
            parse_map_response(self._answer(endpoint), self._window())

        for outside in (low - 0.1, high + 0.1):
            with pytest.raises(GenerationFailed):
                parse_map_response(self._answer(outside), self._window())

    def test_one_constant_states_the_bounds_for_both_messages(self) -> None:
        """One shared value interpolated by both, so the instruction and the
        refusal cannot be edited into describing different ranges."""
        assert generate_artifacts.SCORE_RANGE_TEXT in _map_prompt(self._window())
        assert generate_artifacts.SCORE_RANGE_TEXT in self._refusal_for(8.5)
