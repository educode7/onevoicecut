"""Turning cited moments into clips with real timestamps.

The model proposes; the transcript decides where. A moment arrives as segment ids
plus a hook, a quote, a reason and a score — and the **times are never taken from
the model**. They are read off the segments those ids resolve to. That is the
whole reason the id scheme exists: an LLM asked for a number produces a plausible
one, and a clip cut at a fabricated timestamp is fluent, confident and aimed at
the wrong minute of a three-hour video.

Ranking is by the model's score, which is the one thing here it *is* qualified to
judge — but the ordering must be deterministic, because two runs over the same
transcript that disagree about the top five are two runs an operator cannot
reason about. Ties break on position, so the earlier moment wins.
"""

import json

import pytest

from onevoicecut.domain.errors import GenerationFailed
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment
from onevoicecut.usecases.generate_artifacts import (
    DEFAULT_MAX_CLIP_CANDIDATES,
    MapPartial,
    MapWindow,
    parse_map_response,
    rank_clip_candidates,
)

WINDOW = MapWindow(segment_ids=(0, 1, 2, 3, 4), text="irrelevante")


def _segments(count: int = 6) -> tuple[TranscriptSegment, ...]:
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


def _moment(
    *ids: int, score: float = 0.5, hook: str = "gancho", quote: str = "cita"
) -> dict[str, object]:
    return {
        "segment_ids": list(ids),
        "hook": hook,
        "quote": quote,
        "rationale": "porque si",
        "score": score,
    }


def _partial(*moments: dict[str, object], summary: str = "resumen") -> MapPartial:
    return parse_map_response(
        json.dumps({"summary": summary, "moments": list(moments)}), WINDOW
    )


class TestTimesComeFromTheTranscript:
    def test_a_moment_spans_the_segments_it_cites(self) -> None:
        """Segment 1 runs 10–20 s and segment 2 runs 20–30 s, so a moment citing
        both is 10–30 s. Read off the transcript, never off the model."""
        candidates = rank_clip_candidates(
            (_partial(_moment(1, 2)),), _segments(), max_candidates=5
        )

        assert (candidates[0].start_s, candidates[0].end_s) == (10.0, 30.0)

    def test_non_contiguous_ids_span_from_first_to_last(self) -> None:
        """A model citing 1 and 4 means "this stretch". Cutting only the two
        cited segments would produce a clip that jumps."""
        candidates = rank_clip_candidates(
            (_partial(_moment(1, 4)),), _segments(), max_candidates=5
        )

        assert (candidates[0].start_s, candidates[0].end_s) == (10.0, 50.0)

    def test_ids_out_of_order_still_span_correctly(self) -> None:
        candidates = rank_clip_candidates(
            (_partial(_moment(4, 1)),), _segments(), max_candidates=5
        )

        assert (candidates[0].start_s, candidates[0].end_s) == (10.0, 50.0)

    def test_the_model_cannot_supply_a_timestamp(self) -> None:
        """Even offered one, it is ignored. There is no field for it, and adding
        one would be an invitation to the exact hallucination the ids prevent."""
        raw = json.dumps(
            {
                "summary": "r",
                "moments": [{**_moment(1), "start_s": 999.0, "end_s": 1234.0}],
            }
        )
        partial = parse_map_response(raw, WINDOW)

        candidates = rank_clip_candidates((partial,), _segments(), max_candidates=5)

        assert candidates[0].start_s == 10.0


class TestRanking:
    def test_candidates_come_back_best_first(self) -> None:
        candidates = rank_clip_candidates(
            (_partial(_moment(0, score=0.2), _moment(1, score=0.9), _moment(2, score=0.5)),),
            _segments(),
            max_candidates=5,
        )

        assert [c.score for c in candidates] == [0.9, 0.5, 0.2]

    def test_moments_from_every_partial_compete(self) -> None:
        """A three-hour sermon produces dozens of partials, and the best moment
        is not more likely to be in the first one."""
        candidates = rank_clip_candidates(
            (_partial(_moment(0, score=0.1)), _partial(_moment(1, score=0.9))),
            _segments(),
            max_candidates=5,
        )

        assert candidates[0].start_s == 10.0

    def test_only_the_top_n_survive(self) -> None:
        moments = tuple(_moment(i, score=i / 10) for i in range(5))

        candidates = rank_clip_candidates(
            (_partial(*moments),), _segments(), max_candidates=2
        )

        assert len(candidates) == 2

    def test_ties_break_on_position_so_two_runs_agree(self) -> None:
        """Determinism is the point. Two runs over the same transcript that
        disagreed about the top five would be two runs an operator cannot reason
        about, and nothing in the artifact would say which they were reading."""
        candidates = rank_clip_candidates(
            (_partial(_moment(3, score=0.5), _moment(1, score=0.5)),),
            _segments(),
            max_candidates=5,
        )

        assert [c.start_s for c in candidates] == [10.0, 30.0]

    def test_the_cap_has_a_documented_default(self) -> None:
        assert DEFAULT_MAX_CLIP_CANDIDATES > 0

    def test_no_moments_is_no_candidates(self) -> None:
        assert rank_clip_candidates((_partial(),), _segments(), max_candidates=5) == ()


class TestWhatAMomentMustCarry:
    def test_a_moment_citing_nothing_is_refused(self) -> None:
        """A clip without a time is not a clip. Dropping it silently would lose
        a moment the model thought was the best in the sermon."""
        with pytest.raises(GenerationFailed):
            _partial(_moment())

    def test_a_moment_citing_an_invented_id_is_refused(self) -> None:
        """Same rule as the summary's ids, applied where it has teeth: this one
        becomes a timestamp an operator will act on."""
        with pytest.raises(GenerationFailed):
            _partial(_moment(99))

    def test_a_score_outside_the_range_is_refused(self) -> None:
        """Ranking is comparison, so the scale has to mean the same thing for
        every moment. A model returning 87 alongside 0.9 would put a mediocre
        moment at the top of every list."""
        with pytest.raises(GenerationFailed):
            _partial(_moment(1, score=87.0))

    def test_a_missing_hook_is_refused(self) -> None:
        raw = json.dumps({"summary": "r", "moments": [{"segment_ids": [1], "score": 0.5}]})

        with pytest.raises(GenerationFailed):
            parse_map_response(raw, WINDOW)

    def test_moments_are_optional(self) -> None:
        """A window of transition has no moment worth cutting, and a model
        forced to name one would invent it."""
        assert parse_map_response(json.dumps({"summary": "r"}), WINDOW).moments == ()


class TestWhatItCarriesForward:
    def test_the_hook_and_quote_survive(self) -> None:
        candidates = rank_clip_candidates(
            (_partial(_moment(1, hook="el gancho", quote="la cita")),),
            _segments(),
            max_candidates=5,
        )

        assert candidates[0].hook == "el gancho"
        assert candidates[0].quote == "la cita"

    def test_variants_are_empty_until_slice_10b_iii(self) -> None:
        """One `complete()` call per (candidate, target) pair is 10b-iii's work.
        An empty tuple says "none yet" without pretending otherwise."""
        candidates = rank_clip_candidates(
            (_partial(_moment(1)),), _segments(), max_candidates=5
        )

        assert candidates[0].variants == ()
