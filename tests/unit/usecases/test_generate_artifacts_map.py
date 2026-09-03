"""Cutting a three-hour transcript into windows a model will accept.

The MAP half of map-reduce, and the part where a bug is invisible. Every failure
here produces a summary that reads perfectly: a dropped window is a passage of the
sermon the model never saw, a duplicated one is a point made twice, and neither
leaves a mark in the artifact an operator would notice.

So the assertions are about **coverage and progress**, not about text. Every
segment reaches at least one window; consecutive windows overlap so a thought
split across a boundary survives in one piece; and every window admits at least
one segment the previous one did not, which is what stops a transcript longer
than the budget from windowing forever.

Ids are the other half of the design and they are load-bearing. The model never
emits a timestamp — it emits segment ids, which the use case resolves against the
real `Transcript`. An LLM asked for a number will produce a plausible one, and a
fabricated timestamp points the operator at the wrong minute of a three-hour video
while looking entirely correct. So a window carries the ids it contains, and
slice 10a-iii rejects any the model invents.

Token counting is `chars/4` and deliberately has no tokenizer behind it. The
estimate is conservative, the adapter raises `ContextLengthExceeded` when it is
wrong, and 10a-iv halves and retries — which keeps a tokenizer dependency out of
the core and makes the estimate provider-neutral.
"""

import pytest

from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment
from onevoicecut.usecases.generate_artifacts import (
    CHARS_PER_TOKEN,
    DEFAULT_MAP_OVERLAP_TOKENS,
    DEFAULT_MAP_WINDOW_TOKENS,
    MapWindow,
    estimate_tokens,
    map_windows,
)

# Small enough to force many windows without building a fixture nobody can read.
WINDOW = 100
OVERLAP = 20


def _segment(index: int, *, words: int = 10) -> TranscriptSegment:
    """Text long enough to cost real tokens, distinct enough to trace."""
    return TranscriptSegment(
        start_s=float(index * 10),
        end_s=float(index * 10 + 10),
        text=" ".join(f"palabra{index:03d}" for _ in range(words)),
        speaker=None,
        confidence=0.9,
        kind=SegmentKind.SPEECH,
    )


def _transcript(count: int, *, words: int = 10) -> tuple[TranscriptSegment, ...]:
    return tuple(_segment(i, words=words) for i in range(count))


def _windows(
    segments: tuple[TranscriptSegment, ...],
    *,
    window_tokens: int = WINDOW,
    overlap_tokens: int = OVERLAP,
) -> tuple[MapWindow, ...]:
    return map_windows(
        segments, window_tokens=window_tokens, overlap_tokens=overlap_tokens
    )


class TestTheTokenEstimate:
    def test_it_is_chars_over_four(self) -> None:
        assert estimate_tokens("a" * 400) == 100

    def test_it_rounds_up_rather_than_down(self) -> None:
        """A budget that under-counts is a request the provider refuses. Costing
        a fraction of a token as a whole one is the safe direction."""
        assert estimate_tokens("abc") == 1

    def test_empty_text_costs_nothing(self) -> None:
        """Non-speech ranges carry timestamps and no text. They must not each
        consume a token of a budget they contribute nothing to."""
        assert estimate_tokens("") == 0

    def test_the_divisor_is_stated_once(self) -> None:
        assert CHARS_PER_TOKEN == 4


class TestWindowCoverage:
    def test_an_empty_transcript_produces_no_windows(self) -> None:
        """Not one empty window. A model asked to summarise nothing returns
        something, and that something would become the summary."""
        assert _windows(()) == ()

    def test_a_transcript_inside_the_budget_is_one_window(self) -> None:
        assert len(_windows(_transcript(2))) == 1

    def test_a_transcript_over_the_budget_is_split(self) -> None:
        assert len(_windows(_transcript(40))) > 1

    def test_every_segment_reaches_a_window(self) -> None:
        """The assertion this module exists for. A dropped segment is a passage
        of the sermon the model never saw, and the summary that comes back reads
        exactly as well without it."""
        segments = _transcript(40)

        covered = {
            index for window in _windows(segments) for index in window.segment_ids
        }

        assert covered == set(range(len(segments)))

    def test_ids_stay_in_order_within_a_window(self) -> None:
        for window in _windows(_transcript(40)):
            assert list(window.segment_ids) == sorted(window.segment_ids)

    def test_windows_stay_in_order(self) -> None:
        """A model folding partial summaries sequentially (10a-iii) reads them in
        the order they arrive, so a sermon summarised out of order would argue
        backwards."""
        windows = _windows(_transcript(40))

        assert [w.segment_ids[0] for w in windows] == sorted(
            w.segment_ids[0] for w in windows
        )


class TestTheBudgetIsRespected:
    def test_no_window_exceeds_the_token_budget(self) -> None:
        for window in _windows(_transcript(40)):
            assert estimate_tokens(window.text) <= WINDOW

    def test_a_single_oversized_segment_gets_its_own_window(self) -> None:
        """It cannot be made to fit and it must not be dropped. Its own window,
        over budget, and `ContextLengthExceeded` handles it downstream — which
        is what 10a-iv's halving retry is for."""
        segments = (_segment(0, words=2), _segment(1, words=500), _segment(2, words=2))

        windows = _windows(segments)

        oversized = [w for w in windows if 1 in w.segment_ids]
        assert len(oversized) == 1
        assert oversized[0].segment_ids == (1,)

    def test_an_oversized_segment_does_not_swallow_its_neighbours(self) -> None:
        segments = (_segment(0, words=2), _segment(1, words=500), _segment(2, words=2))

        covered = {i for w in _windows(segments) for i in w.segment_ids}

        assert covered == {0, 1, 2}


class TestTheOverlap:
    def test_consecutive_windows_share_segments(self) -> None:
        """A thought split across a boundary would otherwise be summarised twice
        as two half-thoughts, and the fold has nothing to reconcile them with."""
        windows = _windows(_transcript(40))

        for earlier, later in zip(windows, windows[1:]):
            assert set(earlier.segment_ids) & set(later.segment_ids)

    def test_the_shared_tail_stays_within_the_overlap_budget(self) -> None:
        """Overlap is duplicated work paid for on every window of a three-hour
        transcript. Unbounded, it is a second pass over the sermon."""
        windows = _windows(_transcript(40))

        for earlier, later in zip(windows, windows[1:]):
            shared = sorted(set(earlier.segment_ids) & set(later.segment_ids))
            cost = sum(estimate_tokens(_segment(i).text) for i in shared)
            assert cost <= OVERLAP + estimate_tokens(_segment(shared[-1]).text)

    def test_every_window_admits_something_new(self) -> None:
        """Termination. A window whose ids the previous one already held makes no
        progress, and a transcript longer than the budget would window forever —
        inside a job already measured in hours."""
        windows = _windows(_transcript(40))

        for earlier, later in zip(windows, windows[1:]):
            assert set(later.segment_ids) - set(earlier.segment_ids)

    def test_no_overlap_requested_means_none_given(self) -> None:
        windows = _windows(_transcript(40), overlap_tokens=0)

        for earlier, later in zip(windows, windows[1:]):
            assert not set(earlier.segment_ids) & set(later.segment_ids)


class TestTheRenderedText:
    def test_each_segment_is_prefixed_with_its_id(self) -> None:
        """The model cites these back instead of inventing timestamps."""
        window = _windows(_transcript(2))[0]

        assert "[s0000]" in window.text
        assert "[s0001]" in window.text

    def test_the_ids_in_the_text_are_exactly_the_ids_declared(self) -> None:
        """10a-iii rejects any id the model returns that the window did not
        contain, so a window whose text and manifest disagreed would either
        reject a valid citation or admit an invented one."""
        for window in _windows(_transcript(40)):
            rendered = {
                int(part.split("]")[0])
                for part in window.text.split("[s")[1:]
            }
            assert rendered == set(window.segment_ids)

    def test_the_id_is_the_index_into_the_transcript(self) -> None:
        """Which is what makes resolution against the real `Transcript` a lookup
        rather than a search, and what stops two windows numbering the same
        segment differently."""
        segments = _transcript(40)
        window = _windows(segments)[-1]

        for index in window.segment_ids:
            assert segments[index].text.startswith(f"palabra{index:03d}")

    def test_a_segment_with_no_text_is_still_addressable(self) -> None:
        """Non-speech ranges reach here as empty text with real timestamps. They
        cost nothing and carry nothing, but dropping their ids would renumber
        nothing — the id is the transcript index, so a gap is just a gap."""
        segments = (
            _segment(0),
            TranscriptSegment(
                start_s=10.0, end_s=20.0, text="", speaker=None,
                confidence=None, kind=SegmentKind.MUSIC,
            ),
            _segment(2),
        )

        covered = [i for w in _windows(segments) for i in w.segment_ids]

        assert covered == [0, 1, 2]


def test_the_documented_defaults_are_the_ones_in_the_design() -> None:
    """3000 tokens with a 200-token overlap, from design.md. Pinned because a
    silent change to either is a change in what the model is asked to reason
    about, and nothing downstream would report it."""
    assert DEFAULT_MAP_WINDOW_TOKENS == 3000
    assert DEFAULT_MAP_OVERLAP_TOKENS == 200


def test_an_overlap_at_least_the_window_is_refused() -> None:
    """It would make every window start where the last one did, which is the
    non-termination this module's progress rule exists to prevent — better a
    refusal at the call than a job that never ends."""
    with pytest.raises(ValueError):
        _windows(_transcript(40), window_tokens=100, overlap_tokens=100)
