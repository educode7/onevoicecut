"""One script per candidate per target, and a duration the model never chose.

`ScriptVariant` has carried four fields since slice 1: `target`, `format`, `body`
and `duration_target_s`. Only one of them comes from the model.

**The body does. The duration does not**, and the reason is the one this project
keeps returning to: an LLM asked for a number produces a plausible one. A
fabricated `duration_target_s` would be a fifty-second script labelled thirty,
and the label is what an editor cuts against. It is a property of the target —
of what a "reel" is — not an observation about the text, so the target carries
it and the model is never offered the field.

`format` is the same argument in a different coat: it says how the script is
shaped, which is decided before the model is asked, not reported afterwards.

Count is the other half. Two targets means two calls and two variants on the same
candidate, with no schema change — the field was a tuple from the first day
precisely so N could grow without one.
"""

import pytest

from onevoicecut.domain.errors import GenerationFailed
from onevoicecut.domain.generation import ClipCandidate
from onevoicecut.usecases.generate_artifacts import (
    DEFAULT_SCRIPT_TARGETS,
    ScriptTarget,
    resolve_script_targets,
    write_script_variants,
)
from tests.fakes.text_generation import FakeTextGenerationPort

OUTPUT_TOKENS = 300


def _candidate(start_s: float = 10.0, hook: str = "gancho") -> ClipCandidate:
    return ClipCandidate(
        start_s=start_s,
        end_s=start_s + 30.0,
        hook=hook,
        quote="la cita",
        rationale="porque si",
        score=0.8,
        variants=(),
    )


def _targets(names: str = DEFAULT_SCRIPT_TARGETS) -> tuple[ScriptTarget, ...]:
    return resolve_script_targets(names)


class TestOneCallPerCandidateTargetPair:
    def test_one_candidate_one_target_is_one_call(self) -> None:
        port = FakeTextGenerationPort(replies=("guion",))

        write_script_variants(
            (_candidate(),),
            generate=port,
            targets=_targets(),
            max_output_tokens=OUTPUT_TOKENS,
        )

        assert len(port.calls) == 1

    def test_the_grid_is_candidates_times_targets(self) -> None:
        """Three candidates over two targets is six scripts, and six billed
        calls. Naming that here means a change to either count is visible as a
        change in cost rather than discovered on an invoice."""
        port = FakeTextGenerationPort(replies=("guion",))

        write_script_variants(
            (_candidate(10.0), _candidate(50.0), _candidate(90.0)),
            generate=port,
            targets=_targets("generic,generic"),
            max_output_tokens=OUTPUT_TOKENS,
        )

        assert len(port.calls) == 6

    def test_no_candidates_means_no_calls(self) -> None:
        port = FakeTextGenerationPort()

        assert (
            write_script_variants(
                (), generate=port, targets=_targets(), max_output_tokens=OUTPUT_TOKENS
            )
            == ()
        )
        assert port.calls == []


class TestWhatEachCandidateComesBackWith:
    def test_a_variant_per_target(self) -> None:
        """The tuple grows; nothing else changes. That is what `variants` being
        a tuple from slice 1 bought."""
        port = FakeTextGenerationPort(replies=("guion",))

        candidates = write_script_variants(
            (_candidate(),),
            generate=port,
            targets=_targets("generic,generic"),
            max_output_tokens=OUTPUT_TOKENS,
        )

        assert len(candidates[0].variants) == 2

    def test_the_body_is_what_the_model_wrote(self) -> None:
        port = FakeTextGenerationPort(replies=("hola hermanos, escuchen esto",))

        candidates = write_script_variants(
            (_candidate(),),
            generate=port,
            targets=_targets(),
            max_output_tokens=OUTPUT_TOKENS,
        )

        assert candidates[0].variants[0].body == "hola hermanos, escuchen esto"

    def test_the_duration_comes_from_the_target(self) -> None:
        """Never from the model. A fabricated duration is a fifty-second script
        labelled thirty, and the label is what an editor cuts against."""
        port = FakeTextGenerationPort(replies=("guion de cualquier largo",))

        candidates = write_script_variants(
            (_candidate(),),
            generate=port,
            targets=_targets(),
            max_output_tokens=OUTPUT_TOKENS,
        )

        assert candidates[0].variants[0].duration_target_s == _targets()[0].duration_target_s

    def test_the_format_comes_from_the_target(self) -> None:
        """How the script is shaped is decided before the model is asked, not
        reported afterwards."""
        port = FakeTextGenerationPort(replies=("guion",))

        candidates = write_script_variants(
            (_candidate(),),
            generate=port,
            targets=_targets(),
            max_output_tokens=OUTPUT_TOKENS,
        )

        assert candidates[0].variants[0].format == _targets()[0].format

    def test_the_variant_names_its_target(self) -> None:
        port = FakeTextGenerationPort(replies=("guion",))

        candidates = write_script_variants(
            (_candidate(),),
            generate=port,
            targets=_targets(),
            max_output_tokens=OUTPUT_TOKENS,
        )

        assert candidates[0].variants[0].target == "generic"

    def test_everything_else_about_the_candidate_survives(self) -> None:
        """Times, score and the model's own hook are not re-derived here. This
        step adds scripts; it must not become a second place a timestamp can
        change."""
        port = FakeTextGenerationPort(replies=("guion",))
        original = _candidate(42.0, hook="el gancho")

        candidates = write_script_variants(
            (original,),
            generate=port,
            targets=_targets(),
            max_output_tokens=OUTPUT_TOKENS,
        )

        assert (candidates[0].start_s, candidates[0].end_s) == (42.0, 72.0)
        assert candidates[0].hook == "el gancho"
        assert candidates[0].score == original.score


class TestThePromptItSends:
    def test_it_carries_the_candidates_hook_and_quote(self) -> None:
        """The model writes from the moment, not from the whole sermon — which
        is what keeps a script call small enough to never need windowing."""
        port = FakeTextGenerationPort(replies=("guion",))

        write_script_variants(
            (_candidate(hook="el gancho"),),
            generate=port,
            targets=_targets(),
            max_output_tokens=OUTPUT_TOKENS,
        )

        assert "el gancho" in port.prompts[0]
        assert "la cita" in port.prompts[0]

    def test_it_carries_the_target_and_its_duration(self) -> None:
        port = FakeTextGenerationPort(replies=("guion",))

        write_script_variants(
            (_candidate(),),
            generate=port,
            targets=_targets(),
            max_output_tokens=OUTPUT_TOKENS,
        )

        assert "generic" in port.prompts[0]
        assert str(int(_targets()[0].duration_target_s)) in port.prompts[0]

    def test_it_carries_no_timestamp(self) -> None:
        """There is nothing for the model to do with one, and offering it a
        number invites it to give one back."""
        port = FakeTextGenerationPort(replies=("guion",))

        write_script_variants(
            (_candidate(1234.0),),
            generate=port,
            targets=_targets(),
            max_output_tokens=OUTPUT_TOKENS,
        )

        assert "1234" not in port.prompts[0]


class TestResolvingTargets:
    def test_the_default_is_one_generic_target(self) -> None:
        """Q3 — what the real targets are — is still open, so shipping a guess
        at Instagram's preferred length would be a product decision made by
        nobody."""
        assert [t.name for t in resolve_script_targets(DEFAULT_SCRIPT_TARGETS)] == [
            "generic"
        ]

    def test_names_are_comma_separated_like_the_operator_token_map(self) -> None:
        assert len(resolve_script_targets("generic,generic")) == 2

    def test_surrounding_whitespace_is_ignored(self) -> None:
        assert len(resolve_script_targets(" generic , generic ")) == 2

    def test_an_unknown_target_is_refused_naming_what_exists(self) -> None:
        """Rather than silently falling back to generic. An operator who asked
        for a reel script and got a generic one would have no way to tell."""
        with pytest.raises(GenerationFailed) as refusal:
            resolve_script_targets("tiktok")

        assert "generic" in str(refusal.value)

    def test_no_targets_at_all_is_refused(self) -> None:
        """The script artifact is this system's stopping point. A build
        configured to write none of them produces nothing, and should say so at
        the call rather than deliver an empty result."""
        with pytest.raises(GenerationFailed):
            resolve_script_targets("   ")
