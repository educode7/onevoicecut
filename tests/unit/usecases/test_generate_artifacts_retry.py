"""When the estimate was wrong, halve the window and ask again.

`chars/4` is deliberately crude — it keeps a provider-specific tokenizer out of
the core, and the price is that it is sometimes wrong in the expensive direction.
The provider says so by raising `ContextLengthExceeded`, and the recovery is the
same shape slice 8b-i built for oversized audio chunks: halve, retry each half,
bound the recursion, lose nothing.

Two properties carry over from that unit because they are the same two properties:

**Coverage.** Every segment id in the original window must still reach the model
in one of the halves. A dropped half is a passage of the sermon nobody summarised,
and the summary that comes back reads exactly as well without it.

**Termination.** A window of one segment that still will not fit cannot be halved
into anything, so it fails loudly rather than recursing forever inside a job
already measured in hours.

The one thing that differs from 8b-i: a split window yields **two partials rather
than one**, and that is fine. REDUCE folds however many arrive, and a fold is the
mechanism that existed for this all along.
"""

import json

import pytest

from onevoicecut.domain.errors import ContextLengthExceeded
from onevoicecut.usecases.generate_artifacts import (
    SEGMENT_SEPARATOR,
    MapWindow,
    _map_prompt,  # private on purpose: these tests are about prompt size exactly
    estimate_tokens,
    run_map,
)

OUTPUT_TOKENS = 200


def _window(*ids: int) -> MapWindow:
    return MapWindow(
        segment_ids=ids,
        text="\n".join(f"[s{i:04d}] " + "palabra " * 20 for i in ids),
    )


class SizeBoundModel:
    """Refuses any prompt over `limit`, the way a real provider does.

    Call-count-based failure would prove the retry happened; only size-based
    failure proves the retry *helped* — that halving actually produced something
    the model would accept.
    """

    def __init__(self, limit_tokens: int) -> None:
        self._limit = limit_tokens
        self.prompts: list[str] = []

    def model_id(self) -> str:
        return "size-bound-fake"

    def complete(
        self, prompt: str, *, max_output_tokens: int, temperature: float = 0.2
    ) -> str:
        self.prompts.append(prompt)
        if estimate_tokens(prompt) > self._limit:
            raise ContextLengthExceeded(
                f"{estimate_tokens(prompt)} tokens over a {self._limit} limit"
            )
        return json.dumps({"summary": f"resumen de {len(prompt)}", "segment_ids": []})


def _fits(window: MapWindow, *, halves: int) -> int:
    """A limit that accepts a window cut into `halves` and refuses anything
    larger. Measured against the whole prompt, not just its transcript: the
    instruction rides along on every call and is a real part of the budget."""
    kept = len(window.segment_ids) // halves
    piece = MapWindow(
        segment_ids=window.segment_ids[:kept],
        text=SEGMENT_SEPARATOR.join(window.text.splitlines()[:kept]),
    )
    return estimate_tokens(_map_prompt(piece))


def _cited_windows(model: SizeBoundModel) -> list[set[int]]:
    """Which ids each accepted prompt actually carried."""
    return [
        {int(part.split("]")[0]) for part in prompt.split("[s")[1:]}
        for prompt in model.prompts
    ]


class TestAWindowThatFits:
    def test_is_never_split(self) -> None:
        """The recovery path must cost nothing on the normal case, which is
        every window of every correctly estimated job."""
        model = SizeBoundModel(limit_tokens=100_000)

        run_map((_window(0, 1, 2),), generate=model, max_output_tokens=OUTPUT_TOKENS)

        assert len(model.prompts) == 1

    def test_produces_one_partial(self) -> None:
        model = SizeBoundModel(limit_tokens=100_000)

        partials = run_map(
            (_window(0, 1),), generate=model, max_output_tokens=OUTPUT_TOKENS
        )

        assert len(partials) == 1


class TestAWindowTheProviderRefuses:
    def test_is_halved_and_retried(self) -> None:
        window = _window(*range(8))
        # Accepts about half a window: the whole thing is refused, its halves fit.
        model = SizeBoundModel(limit_tokens=_fits(window, halves=2))

        run_map((window,), generate=model, max_output_tokens=OUTPUT_TOKENS)

        assert len(model.prompts) == 3  # the refused whole, then two halves

    def test_every_id_still_reaches_the_model(self) -> None:
        """Coverage, the property whose violation is silent. A dropped half is a
        passage nobody summarised, and the summary reads as well without it."""
        window = _window(*range(8))
        model = SizeBoundModel(limit_tokens=_fits(window, halves=2))

        run_map((window,), generate=model, max_output_tokens=OUTPUT_TOKENS)

        accepted = _cited_windows(model)[1:]  # the first prompt was refused
        assert set().union(*accepted) == set(window.segment_ids)

    def test_the_halves_do_not_overlap(self) -> None:
        """Unlike windowing's overlap, which exists to protect a thought split
        across a boundary. Here the boundary already existed — re-sending shared
        segments would pay twice for text the model has seen and produce two
        partials that repeat each other."""
        window = _window(*range(8))
        model = SizeBoundModel(limit_tokens=_fits(window, halves=2))

        run_map((window,), generate=model, max_output_tokens=OUTPUT_TOKENS)

        left, right = _cited_windows(model)[1:]
        assert not left & right

    def test_it_yields_two_partials_rather_than_one(self) -> None:
        """And that is fine: REDUCE folds however many arrive, which is the
        mechanism that existed for this all along."""
        window = _window(*range(8))
        model = SizeBoundModel(limit_tokens=_fits(window, halves=2))

        partials = run_map((window,), generate=model, max_output_tokens=OUTPUT_TOKENS)

        assert len(partials) == 2

    def test_it_halves_again_when_once_is_not_enough(self) -> None:
        """Recursive, not a single split. One retry at half size would fail
        again and surrender a window quarters would have carried."""
        window = _window(*range(8))
        model = SizeBoundModel(limit_tokens=_fits(window, halves=4))

        partials = run_map((window,), generate=model, max_output_tokens=OUTPUT_TOKENS)

        assert len(partials) == 4


class TestItTerminates:
    def test_a_single_segment_that_will_not_fit_fails_loudly(self) -> None:
        """It cannot be halved into anything. Recursing forever inside a job
        already measured in hours is worse than saying so."""
        model = SizeBoundModel(limit_tokens=1)

        with pytest.raises(ContextLengthExceeded):
            run_map((_window(0),), generate=model, max_output_tokens=OUTPUT_TOKENS)

    def test_a_window_no_split_can_rescue_fails_rather_than_looping(self) -> None:
        model = SizeBoundModel(limit_tokens=1)

        with pytest.raises(ContextLengthExceeded):
            run_map(
                (_window(*range(8)),), generate=model, max_output_tokens=OUTPUT_TOKENS
            )

    def test_the_failure_names_the_segment_it_could_not_shrink(self) -> None:
        """An operator's only lever is the transcript. Knowing which segment is
        the immovable one is the difference between acting and guessing."""
        model = SizeBoundModel(limit_tokens=1)

        with pytest.raises(ContextLengthExceeded) as refusal:
            run_map((_window(7),), generate=model, max_output_tokens=OUTPUT_TOKENS)

        assert "7" in str(refusal.value)


def test_one_definition_of_the_token_estimate() -> None:
    """10.11 asked for a token-estimation helper to be extracted. It was already
    extracted in 10a-i — `estimate_tokens` is the only place `CHARS_PER_TOKEN` is
    used, and windowing, the fold budget and this retry path all call it."""
    from onevoicecut.usecases import generate_artifacts

    source = __import__("inspect").getsource(generate_artifacts)

    assert source.count("CHARS_PER_TOKEN") == 2  # the constant, and its one use
