"""Which segments become captions, and what the clip then declares about them.

A vertical clip is watched muted, so the burned-in caption is its only channel in
that state. That raises the stakes on two declarations a clip carries, and both
are computed from **one basis: the eligible segments overlapping the span** —
never from the cues, and never from `capabilities().word_timing`.

The capability answers "could this engine ever produce word timings". The
segments answer "did *this clip* get them", and only the second is true about the
artifact — a word-timing-capable adapter can still return `()` for a segment.

Eligibility is the same message-facing rule every other consumer uses.
`without_music` drops sung audio; `UNCERTAIN` stays, because an adapter that
cannot classify marks *everything* `UNCERTAIN` and excluding it would leave a
muted clip with a silently blank caption channel. Its uncertainty is declared as
metadata rather than burned into the frame — `render_message_text` marks it for a
human reading a transcript, but a caption is the message itself and a `[?]` on
screen is not what the preacher said.

Two traps have their own tests because both are quiet. `all()` over an empty set
is `True`, so a span with no eligible segment would declare a vacuous
`WORD_LEVEL`. And cue construction has to be **total** over the eligible set:
only then are "zero cues" and "no eligible segment" the same condition, which is
what lets `NONE` mean something an operator can act on.
"""

from onevoicecut.domain.framing import TimeSpan
from onevoicecut.domain.rendering import CaptionCoverage, SubtitleTimingSource
from onevoicecut.domain.transcript import (
    UNCERTAIN_MARKER,
    SegmentKind,
    TranscriptSegment,
    WordTiming,
)
from onevoicecut.usecases.build_subtitle_cues import (
    DEFAULT_MAX_CUE_CHARS,
    build_subtitle_cues,
)

SPAN = TimeSpan(100.0, 130.0)


def _words(text: str, start_s: float, end_s: float) -> tuple[WordTiming, ...]:
    """One entry per word, spans dividing the segment. Trailing whitespace is
    carried so a join is lossless, matching the shared contract's invariant."""
    parts = [f"{word} " for word in text.split()]
    parts[-1] = parts[-1].rstrip()
    step = (end_s - start_s) / len(parts)
    return tuple(
        WordTiming(start_s=start_s + i * step, end_s=start_s + (i + 1) * step, text=part)
        for i, part in enumerate(parts)
    )


def _segment(
    start_s: float,
    end_s: float,
    text: str,
    kind: SegmentKind = SegmentKind.SPEECH,
    *,
    timed: bool = False,
) -> TranscriptSegment:
    return TranscriptSegment(
        start_s=start_s,
        end_s=end_s,
        text=text,
        speaker=None,
        confidence=0.9,
        kind=kind,
        words=_words(text, start_s, end_s) if timed and text.strip() else (),
    )


class TestEligibility:
    def test_music_never_becomes_a_cue(self) -> None:
        """Sung lyrics are not the message, which is the one rule every
        message-facing consumer shares."""
        segments = (
            _segment(100.0, 105.0, "hermanos queridos"),
            _segment(105.0, 110.0, "aleluya aleluya", SegmentKind.MUSIC),
        )

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert not any("aleluya" in cue.text for cue in cues)

    def test_a_music_segment_keeps_its_timestamps_in_the_transcript(self) -> None:
        """Excluded from captioning, never filtered out of the transcript — it
        stays addressable as clip material."""
        music = _segment(105.0, 110.0, "aleluya", SegmentKind.MUSIC)
        segments = (_segment(100.0, 105.0, "hermanos"), music)

        build_subtitle_cues(segments, SPAN)

        assert (music.start_s, music.end_s) == (105.0, 110.0)

    def test_uncertain_audio_is_captioned_rather_than_dropped(self) -> None:
        """An adapter that cannot classify marks everything `UNCERTAIN`.
        Excluding it would leave a muted clip with a blank caption channel."""
        segments = (_segment(100.0, 105.0, "quizas dijo esto", SegmentKind.UNCERTAIN),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert cues

    def test_no_uncertainty_marker_reaches_the_frame(self) -> None:
        """`render_message_text` marks it for a human reading a transcript. A
        caption *is* the message, and `[?]` on screen is not what was said."""
        segments = (_segment(100.0, 105.0, "quizas dijo esto", SegmentKind.UNCERTAIN),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert all(UNCERTAIN_MARKER.strip() not in cue.text for cue in cues)

    def test_a_whitespace_only_segment_is_not_eligible(self) -> None:
        """A classifying adapter reports every non-speech range it kept out of
        its decode. Those carry timestamps and no text, and a caption of nothing
        is a blank box on screen."""
        segments = (_segment(100.0, 105.0, "   "),)

        cues, _, coverage = build_subtitle_cues(segments, SPAN)

        assert cues == ()
        assert coverage is CaptionCoverage.NONE

    def test_a_segment_outside_the_span_is_not_eligible(self) -> None:
        """The transcript covers three hours; the clip covers thirty seconds."""
        segments = (
            _segment(10.0, 20.0, "muy temprano"),
            _segment(100.0, 105.0, "dentro del clip"),
            _segment(200.0, 210.0, "muy tarde"),
        )

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert all("dentro" in cue.text for cue in cues)


class TestCuesAreClipLocal:
    def test_the_span_start_is_subtracted(self) -> None:
        """The transcript is track-relative and the render pass puts `-ss`
        before `-i`, which resets output timestamps to zero. A source-absolute
        cue would land a hundred seconds away."""
        segments = (_segment(100.0, 105.0, "hermanos"),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert cues[0].start_s == 0.0

    def test_a_later_segment_is_offset_not_reset(self) -> None:
        segments = (_segment(110.0, 115.0, "hermanos"),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert cues[0].start_s == 10.0

    def test_a_segment_straddling_the_start_is_clipped(self) -> None:
        """Half of it is not in the clip, so a caption for that half would
        appear before the words were spoken."""
        segments = (_segment(95.0, 105.0, "hermanos queridos"),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert cues[0].start_s == 0.0

    def test_no_cue_runs_past_the_end_of_the_clip(self) -> None:
        segments = (_segment(125.0, 140.0, "hermanos queridos"),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert all(cue.end_s <= SPAN.duration_s for cue in cues)


class TestSplittingOnWordTiming:
    def test_a_long_segment_splits_into_several_cues(self) -> None:
        """A single cue spanning several seconds is more text than the frame can
        hold at a readable size."""
        text = " ".join(f"palabra{i:02d}" for i in range(20))
        segments = (_segment(100.0, 110.0, text, timed=True),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert len(cues) > 1

    def test_no_cue_exceeds_the_character_budget(self) -> None:
        text = " ".join(f"palabra{i:02d}" for i in range(20))
        segments = (_segment(100.0, 110.0, text, timed=True),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert all(len(cue.text) <= DEFAULT_MAX_CUE_CHARS for cue in cues)

    def test_cue_boundaries_come_from_word_times(self) -> None:
        """Not from an even division. The whole point of having word timing is
        that a cue starts when its first word was actually said."""
        text = " ".join(f"palabra{i:02d}" for i in range(20))
        segments = (_segment(100.0, 110.0, text, timed=True),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert cues[1].start_s > cues[0].start_s
        assert cues[0].end_s <= cues[1].start_s

    def test_no_word_is_split_across_cues(self) -> None:
        text = " ".join(f"palabra{i:02d}" for i in range(20))
        segments = (_segment(100.0, 110.0, text, timed=True),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert all(word in text for cue in cues for word in cue.text.split())

    def test_every_word_survives_the_split(self) -> None:
        """Coverage, the property whose violation is silent — a dropped cue is a
        sentence the viewer never sees, and the clip renders fine without it."""
        text = " ".join(f"palabra{i:02d}" for i in range(20))
        segments = (_segment(100.0, 110.0, text, timed=True),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert " ".join(cue.text for cue in cues).split() == text.split()


class TestTheWordlessFallback:
    def test_a_segment_without_words_yields_one_cue(self) -> None:
        """Never an evenly-distributed guess. Even spacing looks exactly like
        timing and drifts with every syllable the speaker lingers on."""
        segments = (_segment(100.0, 110.0, "hermanos queridos de la iglesia"),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert len(cues) == 1

    def test_that_cue_carries_the_segments_own_times(self) -> None:
        segments = (_segment(100.0, 110.0, "hermanos queridos"),)

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert (cues[0].start_s, cues[0].end_s) == (0.0, 10.0)

    def test_it_is_not_split_even_when_it_exceeds_the_budget(self) -> None:
        """Splitting needs word times to split *on*. Splitting without them
        would be inventing the boundary, which is the fabrication the spec names
        explicitly — and the clip declares `SEGMENT_LEVEL` so it is not passed
        off as ordinary captioning."""
        long_text = " ".join(f"palabra{i:02d}" for i in range(20))
        segments = (_segment(100.0, 110.0, long_text),)

        cues, timing, _ = build_subtitle_cues(segments, SPAN)

        assert len(cues) == 1
        assert timing is SubtitleTimingSource.SEGMENT_LEVEL


class TestTheTimingDeclaration:
    def test_all_timed_eligible_segments_declare_word_level(self) -> None:
        segments = (
            _segment(100.0, 105.0, "hermanos queridos", timed=True),
            _segment(105.0, 110.0, "de la iglesia", timed=True),
        )

        _, timing, _ = build_subtitle_cues(segments, SPAN)

        assert timing is SubtitleTimingSource.WORD_LEVEL

    def test_one_untimed_segment_degrades_the_whole_clip(self) -> None:
        """The declaration is about the artifact, and the artifact is one clip.
        Half word-level captions are not word-level captions."""
        segments = (
            _segment(100.0, 105.0, "hermanos queridos", timed=True),
            _segment(105.0, 110.0, "de la iglesia"),
        )

        _, timing, _ = build_subtitle_cues(segments, SPAN)

        assert timing is SubtitleTimingSource.SEGMENT_LEVEL

    def test_an_untimed_music_segment_does_not_degrade_anything(self) -> None:
        """Eligibility runs first, so the quantifier never sees a segment that
        was never going to be captioned."""
        segments = (
            _segment(100.0, 105.0, "hermanos queridos", timed=True),
            _segment(105.0, 110.0, "aleluya", SegmentKind.MUSIC),
        )

        _, timing, _ = build_subtitle_cues(segments, SPAN)

        assert timing is SubtitleTimingSource.WORD_LEVEL

    def test_no_eligible_segment_is_not_vacuously_word_level(self) -> None:
        """`all()` over an empty set is `True`. Left alone, a span of pure music
        would declare word-level timing for captions it does not have."""
        segments = (_segment(100.0, 110.0, "aleluya", SegmentKind.MUSIC),)

        _, timing, _ = build_subtitle_cues(segments, SPAN)

        assert timing is SubtitleTimingSource.SEGMENT_LEVEL


class TestTheCoverageDeclaration:
    def test_all_speech_is_confirmed(self) -> None:
        segments = (
            _segment(100.0, 105.0, "hermanos"),
            _segment(105.0, 110.0, "queridos"),
        )

        _, _, coverage = build_subtitle_cues(segments, SPAN)

        assert coverage is CaptionCoverage.CONFIRMED_SPEECH

    def test_one_uncertain_segment_makes_the_clip_unverified(self) -> None:
        """The declaration exists so a clip whose captions contain unverified
        audio cannot be delivered as though they were confirmed speech."""
        segments = (
            _segment(100.0, 105.0, "hermanos"),
            _segment(105.0, 110.0, "quizas", SegmentKind.UNCERTAIN),
        )

        _, _, coverage = build_subtitle_cues(segments, SPAN)

        assert coverage is CaptionCoverage.INCLUDES_UNVERIFIED

    def test_a_span_of_pure_music_declares_none(self) -> None:
        segments = (_segment(100.0, 110.0, "aleluya", SegmentKind.MUSIC),)

        cues, _, coverage = build_subtitle_cues(segments, SPAN)

        assert cues == ()
        assert coverage is CaptionCoverage.NONE

    def test_an_empty_transcript_declares_none(self) -> None:
        cues, _, coverage = build_subtitle_cues((), SPAN)

        assert cues == ()
        assert coverage is CaptionCoverage.NONE


class TestTotality:
    def test_every_eligible_segment_yields_at_least_one_cue(self) -> None:
        """What makes the declaration describe the delivered captions."""
        segments = (
            _segment(100.0, 105.0, "hermanos", timed=True),
            _segment(105.0, 110.0, "queridos"),
            _segment(110.0, 115.0, "de la iglesia", SegmentKind.UNCERTAIN),
        )

        cues, _, _ = build_subtitle_cues(segments, SPAN)

        assert len(cues) >= 3

    def test_zero_cues_happens_only_with_no_eligible_segment(self) -> None:
        """So `NONE` and "the builder quietly produced nothing" stop being
        indistinguishable."""
        cases = (
            ((_segment(100.0, 110.0, "aleluya", SegmentKind.MUSIC),), True),
            ((_segment(100.0, 110.0, "   "),), True),
            ((_segment(100.0, 110.0, "hermanos"),), False),
        )

        for segments, expect_empty in cases:
            cues, _, coverage = build_subtitle_cues(segments, SPAN)
            assert (cues == ()) is expect_empty
            assert (coverage is CaptionCoverage.NONE) is expect_empty

    def test_a_captioned_clip_never_reports_zero_coverage(self) -> None:
        segments = (_segment(100.0, 105.0, "hermanos"),)

        cues, _, coverage = build_subtitle_cues(segments, SPAN)

        assert cues
        assert coverage is not CaptionCoverage.NONE
