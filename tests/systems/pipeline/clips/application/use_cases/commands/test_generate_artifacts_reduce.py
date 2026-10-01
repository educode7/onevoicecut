"""What comes back from the model, and how the pieces become one summary.

Two jobs, both about not trusting the output.

**Ids are checked against the window that produced them.** The model never emits
a timestamp — it cites segment ids, which the use case resolves against the real
`Transcript`. An id the window did not contain is not a near miss to be tolerated:
it is the model inventing a reference, and a fabricated moment points an operator
at the wrong minute of a three-hour video while looking entirely correct. The
whole id scheme exists to make that detectable, so detecting it and continuing
anyway would waste it.

**The fold is sequential and bounded.** Eighty-seven partial summaries do not fit
in a context window any more than the transcript did, so they are folded two at a
time: running summary plus the next partial. The running summary stays bounded
because the model is asked for at most `max_output_tokens` each time, which is
what keeps a fold over a three-hour sermon from re-creating the problem windowing
was invented to solve.
"""

import json

import pytest

from onevoicecut.shared.domain.errors import GenerationFailed
from onevoicecut.systems.pipeline.transcripts.domain.transcript import SegmentKind, TranscriptSegment
from onevoicecut.systems.pipeline.clips.application.use_cases.commands.generate_artifacts import (
    MapPartial,
    MapWindow,
    estimate_tokens,
    parse_map_response,
    reduce_summaries,
    run_map,
)
from tests.fakes.text_generation import FakeTextGenerationPort

OUTPUT_TOKENS = 200


def _window(*ids: int) -> MapWindow:
    return MapWindow(
        segment_ids=ids,
        text="\n".join(f"[s{i:04d}] palabra{i:03d}" for i in ids),
    )


def _response(summary: str, *cited: int) -> str:
    """Slice 10b-i gave the cited ids their structure: they are carried by a
    *moment*, which also names the hook, the quote and a score. The id rules
    these tests assert are unchanged — they just live one level in now."""
    moments = (
        [
            {
                "segment_ids": list(cited),
                "hook": "gancho",
                "quote": "cita",
                "rationale": "porque si",
                "score": 0.5,
            }
        ]
        if cited
        else []
    )
    return json.dumps({"summary": summary, "moments": moments})


def _segments(count: int) -> tuple[TranscriptSegment, ...]:
    return tuple(
        TranscriptSegment(
            start_s=float(i * 10),
            end_s=float(i * 10 + 10),
            text=f"palabra{i:03d}",
            speaker=None,
            confidence=0.9,
            kind=SegmentKind.SPEECH,
        )
        for i in range(count)
    )


class TestIdsAreCheckedAgainstTheirWindow:
    def test_a_cited_id_the_window_held_is_accepted(self) -> None:
        partial = parse_map_response(_response("predico sobre la fe", 1, 2), _window(1, 2, 3))

        assert partial.cited_ids == (1, 2)

    def test_an_id_the_window_never_held_is_refused(self) -> None:
        """The model inventing a reference. Not a near miss to tolerate — the id
        scheme exists to make exactly this detectable."""
        with pytest.raises(GenerationFailed):
            parse_map_response(_response("algo", 99), _window(1, 2, 3))

    def test_the_refusal_names_the_invented_id(self) -> None:
        """An operator debugging a refused job needs to know the model made up
        `s0099`, not that "generation failed"."""
        with pytest.raises(GenerationFailed) as refusal:
            parse_map_response(_response("algo", 99), _window(1, 2, 3))

        assert "99" in str(refusal.value)

    def test_one_bad_id_refuses_the_whole_response(self) -> None:
        """Rather than dropping it and keeping the rest.

        A model that fabricated one reference may well have fabricated the
        sentence around it, and the summary text is not checkable the way an id
        is. Silently discarding the evidence while keeping the prose is the
        worse of the two failures.
        """
        with pytest.raises(GenerationFailed):
            parse_map_response(_response("algo", 1, 99, 2), _window(1, 2, 3))

    def test_citing_nothing_is_allowed(self) -> None:
        """A window of transition or throat-clearing has no moment worth citing,
        and a model forced to name one would invent it."""
        assert parse_map_response(_response("sin nada notable"), _window(1, 2)).cited_ids == ()

    def test_an_unparseable_response_is_refused(self) -> None:
        """A model that answered in prose instead of JSON. A raw
        `JSONDecodeError` would escape every caller's except clause."""
        with pytest.raises(GenerationFailed):
            parse_map_response("lo siento, no puedo", _window(1, 2))

    def test_a_response_without_a_summary_is_refused(self) -> None:
        with pytest.raises(GenerationFailed):
            parse_map_response(json.dumps({"moments": []}), _window(1, 2))

    def test_a_non_integer_id_is_refused(self) -> None:
        """`"s0001"` is what a model returns when it echoes the rendered form
        instead of the number, and `int()` on it raises somewhere useless."""
        with pytest.raises(GenerationFailed):
            parse_map_response(
                json.dumps(
                    {
                        "summary": "x",
                        "moments": [{"segment_ids": ["s0001"], "hook": "g",
                                     "quote": "c", "rationale": "r", "score": 0.5}],
                    }
                ),
                _window(1, 2),
            )


class TestTheFenceIsPresentationNotTheContract:
    """A model may wrap its JSON in a markdown fence. The fence is how the
    answer is dressed, never what protects the artifact — the schema check is,
    and it runs on whatever the fence contained."""

    def test_a_json_fenced_answer_parses(self) -> None:
        """qwen2.5:7b-instruct answers exactly like this against the real
        server, and refusing it stranded a finished three-hour transcription
        with no artifacts."""
        raw = "```json\n" + _response("predico sobre la fe", 1) + "\n```"

        partial = parse_map_response(raw, _window(1, 2))

        assert partial.cited_ids == (1,)

    def test_a_bare_fenced_answer_parses(self) -> None:
        """The same wrapper without the language tag — presentation varies
        between providers; the contract does not."""
        raw = "```\n" + _response("predico sobre la fe", 1) + "\n```"

        partial = parse_map_response(raw, _window(1, 2))

        assert partial.cited_ids == (1,)

    def test_a_prose_preamble_outside_a_fence_is_refused(self) -> None:
        """Tolerance is for a fence that *surrounds* the whole answer. Prose in
        front of one is the model not answering the shape it was asked for, and
        admitting it would carry the preamble into the summary."""
        raw = "Aqui va el resumen:\n```json\n" + _response("x") + "\n```"

        with pytest.raises(GenerationFailed):
            parse_map_response(raw, _window(1, 2))

    def test_a_fence_holding_invalid_json_is_refused(self) -> None:
        """The fence is stripped, never trusted: whatever it wraps still faces
        the same load and the same schema check."""
        with pytest.raises(GenerationFailed):
            parse_map_response("```json\n{oops}\n```", _window(1, 2))


class TestTheMapPass:
    def test_one_call_per_window(self) -> None:
        # Cites nothing: the fake repeats its last reply, and a reply citing a
        # concrete id would be correctly refused by every window but the first.
        port = FakeTextGenerationPort(replies=(_response("parcial"),))
        windows = (_window(0), _window(1), _window(2))

        run_map(windows, generate=port, max_output_tokens=OUTPUT_TOKENS)

        assert len(port.calls) == 3

    def test_each_prompt_carries_its_own_window(self) -> None:
        port = FakeTextGenerationPort(replies=(_response("parcial"),))

        run_map(
            (_window(0), _window(7)), generate=port, max_output_tokens=OUTPUT_TOKENS
        )

        assert "[s0000]" in port.prompts[0]
        assert "[s0007]" in port.prompts[1]

    def test_a_window_citing_another_windows_id_is_refused(self) -> None:
        """Each response is checked against the window that produced it, not
        against the transcript as a whole. Otherwise a model could cite a moment
        it was never shown and the citation would validate."""
        port = FakeTextGenerationPort(replies=(_response("parcial", 7),))

        with pytest.raises(GenerationFailed):
            run_map((_window(0),), generate=port, max_output_tokens=OUTPUT_TOKENS)

    def test_no_windows_means_no_calls(self) -> None:
        port = FakeTextGenerationPort()

        assert run_map((), generate=port, max_output_tokens=OUTPUT_TOKENS) == ()
        assert port.calls == []

    def test_the_map_call_asks_for_json(self) -> None:
        """The transport half of the fix: MAP's answer is a schema the parser
        enforces, so the server constrains the grammar instead of trusting the
        prompt to keep the model on it."""
        port = FakeTextGenerationPort(replies=(_response("parcial"),))

        run_map((_window(0),), generate=port, max_output_tokens=OUTPUT_TOKENS)

        assert port.calls[0].json_mode is True


class TestTheReduceFold:
    def test_a_single_partial_needs_no_fold(self) -> None:
        """Nothing to reconcile, so nothing to pay a model for."""
        port = FakeTextGenerationPort(replies=("no deberia llamarse",))
        partials = run_map(
            (_window(0),),
            generate=FakeTextGenerationPort(replies=(_response("el unico", 0),)),
            max_output_tokens=OUTPUT_TOKENS,
        )

        assert reduce_summaries(
            partials, generate=port, max_output_tokens=OUTPUT_TOKENS
        ) == "el unico"
        assert port.calls == []

    def test_partials_fold_into_one_summary(self) -> None:
        port = FakeTextGenerationPort(replies=("resumen final",))
        partials = _partials("uno", "dos", "tres")

        assert (
            reduce_summaries(partials, generate=port, max_output_tokens=OUTPUT_TOKENS)
            == "resumen final"
        )

    def test_the_fold_never_asks_for_json(self) -> None:
        """A fold's answer is prose — the running summary in words. A JSON
        grammar would refuse exactly that, which is why the mode is per call
        and never a property of the adapter."""
        port = FakeTextGenerationPort(replies=("resumen final",))

        reduce_summaries(
            _partials("uno", "dos"), generate=port, max_output_tokens=OUTPUT_TOKENS
        )

        assert [call.json_mode for call in port.calls] == [False]

    def test_it_folds_sequentially_rather_than_all_at_once(self) -> None:
        """Eighty-seven partials do not fit in a context window any more than the
        transcript did. One call per fold, not one call for everything."""
        port = FakeTextGenerationPort(replies=("acumulado",))
        partials = _partials(*[f"parcial {i}" for i in range(5)])

        reduce_summaries(partials, generate=port, max_output_tokens=OUTPUT_TOKENS)

        assert len(port.calls) == 4  # n - 1 folds

    def test_each_fold_sees_the_running_summary_and_one_partial(self) -> None:
        port = FakeTextGenerationPort(replies=("acumulado",))
        partials = _partials("primero", "segundo", "tercero")

        reduce_summaries(partials, generate=port, max_output_tokens=OUTPUT_TOKENS)

        assert "primero" in port.prompts[0] and "segundo" in port.prompts[0]
        assert "acumulado" in port.prompts[1] and "tercero" in port.prompts[1]

    def test_no_fold_call_exceeds_the_practical_budget(self) -> None:
        """The property that keeps REDUCE from re-creating the problem windowing
        was invented to solve. The running summary is bounded because the model
        is asked for at most `max_output_tokens` each time."""
        port = FakeTextGenerationPort(replies=("x" * 400,))
        partials = _partials(*[f"parcial {i}" * 20 for i in range(10)])

        reduce_summaries(
            partials,
            generate=port,
            max_output_tokens=OUTPUT_TOKENS,
            budget_tokens=2000,
        )

        for prompt in port.prompts:
            assert estimate_tokens(prompt) <= 2000

    def test_an_oversized_fold_is_refused_rather_than_sent(self) -> None:
        """Sending it would spend a paid call to be told what the estimate
        already knew. 10a-iv turns this into a halving retry."""
        port = FakeTextGenerationPort(replies=("acumulado",))
        partials = _partials("x" * 4000, "y" * 4000)

        with pytest.raises(GenerationFailed):
            reduce_summaries(
                partials,
                generate=port,
                max_output_tokens=OUTPUT_TOKENS,
                budget_tokens=100,
            )

    def test_no_partials_is_an_empty_summary_and_no_calls(self) -> None:
        """Reached only if every window was filtered away, which admission now
        refuses up front. The floor underneath that guard."""
        port = FakeTextGenerationPort()

        assert reduce_summaries((), generate=port, max_output_tokens=OUTPUT_TOKENS) == ""
        assert port.calls == []


def _partials(*summaries: str) -> tuple[MapPartial, ...]:
    return run_map(
        tuple(_window(i) for i in range(len(summaries))),
        generate=FakeTextGenerationPort(
            replies=tuple(_response(s, i) for i, s in enumerate(summaries))
        ),
        max_output_tokens=OUTPUT_TOKENS,
    )


def test_cited_ids_resolve_against_the_real_transcript() -> None:
    """The point of the whole scheme: an accepted id is an index into the
    transcript, so the moment it names can be looked up rather than searched
    for — and 10b turns it into a real timestamp from there."""
    segments = _segments(10)
    partial = parse_map_response(_response("sobre la fe", 3), _window(1, 2, 3))

    assert [segments[i].text for i in partial.cited_ids] == ["palabra003"]
