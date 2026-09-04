"""Words move with the segment that carries them, or they point at nothing.

The stitcher does three things to a segment: shifts it from chunk-local to
track-relative, truncates it when a cut lands inside it, and drops it when
nothing survives. Every one of those silently breaks word timing if the words are
carried along unchanged.

A shifted segment whose words did not shift has captions offset by the chunk's
start — on chunk 15 of a three-hour sermon, by two and a half hours. A truncated
segment keeps words past its own end, so a caption renders after the clip it
belongs to has finished. Neither leaves a mark: the transcript still reads
correctly, and the failure appears only when someone watches the video.

**The cut moves to word granularity when words exist.** A word is atomic — half
of *"hermanos"* is not a caption — so the boundary is chosen by which words
survive rather than by the raw time, and the segment's new `start_s`, `end_s` and
`text` are derived from them. That is a change of behaviour, and it applies only
where words are present: a transcript with `words=()` throughout must stitch
byte-identically to what shipped before this retrofit, because every adapter in
production today produces exactly that.
"""

from onevoicecut.domain.chunking import ChunkPlan, ChunkResult, ChunkState, PlannedChunk
from onevoicecut.domain.ids import make_job_id
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment, WordTiming
from onevoicecut.usecases.stitch_transcript import stitch_transcript

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
STRIDE_S = 100.0
OVERLAP_S = 20.0


def _words(*spans: tuple[float, float, str]) -> tuple[WordTiming, ...]:
    return tuple(WordTiming(start_s=a, end_s=b, text=t) for a, b, t in spans)


def _segment(
    start_s: float,
    end_s: float,
    text: str,
    words: tuple[WordTiming, ...] = (),
) -> TranscriptSegment:
    return TranscriptSegment(
        start_s=start_s,
        end_s=end_s,
        text=text,
        speaker=None,
        confidence=0.9,
        kind=SegmentKind.SPEECH,
        words=words,
    )


def _plan(overlap_s: float = OVERLAP_S) -> ChunkPlan:
    return ChunkPlan(
        job_id=JOB_ID,
        stride_s=STRIDE_S,
        overlap_s=overlap_s,
        chunks=(
            PlannedChunk(index=0, start_s=0.0, end_s=STRIDE_S + overlap_s),
            PlannedChunk(index=1, start_s=STRIDE_S, end_s=STRIDE_S * 2),
        ),
    )


def _result(index: int, *segments: TranscriptSegment) -> ChunkResult:
    return ChunkResult(
        job_id=JOB_ID,
        index=index,
        state=ChunkState.DONE,
        segments=segments,
        engine_id="fake-asr",
        attempts=1,
        error=None,
        finished_at=1.0,
    )


class TestWordsShiftWithTheirSegment:
    def test_a_second_chunks_words_become_track_relative(self) -> None:
        """Chunk 1 starts at 100 s, so a word at 5 s inside it is at 105 s in the
        transcript. Words left chunk-local would offset every caption in the
        chunk by the chunk's own start — two and a half hours by chunk 15."""
        stitched = stitch_transcript(
            _plan(overlap_s=0.0),
            (
                _result(0, _segment(0.0, 10.0, "hola ", _words((0.0, 10.0, "hola ")))),
                _result(1, _segment(5.0, 15.0, "adios", _words((5.0, 15.0, "adios")))),
            ),
        )

        assert stitched[-1].words[0].start_s == 105.0

    def test_words_stay_inside_the_segment_that_carries_them(self) -> None:
        stitched = stitch_transcript(
            _plan(overlap_s=0.0),
            (
                _result(0, _segment(0.0, 10.0, "hola ", _words((0.0, 10.0, "hola ")))),
                _result(1, _segment(5.0, 15.0, "adios", _words((5.0, 15.0, "adios")))),
            ),
        )

        for segment in stitched:
            for word in segment.words:
                assert segment.start_s <= word.start_s <= word.end_s <= segment.end_s


class TestABoundaryWord:
    def test_it_appears_exactly_once(self) -> None:
        """The contested window decodes twice, so a word in it exists on both
        sides. Two copies read as the preacher repeating themselves — the same
        failure the whole stitcher exists to prevent, now one level down."""
        stitched = _stitched_with_overlap()

        texts = [w.text for s in stitched for w in s.words]
        assert texts.count("frontera ") == 1

    def test_it_keeps_its_own_timing(self) -> None:
        """Not the cut's. A word truncated to the boundary would render a
        caption that starts mid-syllable."""
        stitched = _stitched_with_overlap()

        boundary = [w for s in stitched for w in s.words if w.text == "frontera "]
        assert boundary[0].end_s > boundary[0].start_s

    def test_no_word_is_cut_in_half(self) -> None:
        """A word is atomic: half of "hermanos" is not a caption. The boundary
        moves to the nearest word edge rather than splitting one."""
        stitched = _stitched_with_overlap()

        for segment in stitched:
            for word in segment.words:
                assert word.text.strip()


class TestOrphanedEntries:
    def test_a_dropped_word_leaves_no_timing_behind(self) -> None:
        """The failure this partition exists to prevent: text truncated by time
        while its words were carried through whole, leaving entries for words
        the segment no longer contains."""
        stitched = _stitched_with_overlap()

        for segment in stitched:
            assert "".join(w.text for w in segment.words) == segment.text

    def test_the_segment_text_is_rebuilt_from_the_survivors(self) -> None:
        """Derived rather than inherited. A segment keeping its original text
        while losing words would claim words it cannot time."""
        stitched = _stitched_with_overlap()

        assert all(segment.text for segment in stitched)


class TestWhenNothingSurvives:
    def test_a_segment_losing_every_word_is_dropped(self) -> None:
        """Rather than surviving as a zero-length range with no words. The
        existing drop-when-empty branch now fires for a genuinely empty result
        rather than only for a zero-length time span."""
        early = _segment(0.0, 5.0, "temprano ", _words((0.0, 5.0, "temprano ")))
        late = _segment(100.0, 105.0, "tarde", _words((100.0, 105.0, "tarde")))

        stitched = stitch_transcript(
            _plan(),
            (_result(0, early, late), _result(1, _segment(0.0, 5.0, "tarde", _words((0.0, 5.0, "tarde"))))),
        )

        assert all(segment.words for segment in stitched)


class TestAWordlessTranscriptIsUntouched:
    def test_it_stitches_exactly_as_before_the_retrofit(self) -> None:
        """Every adapter in production today produces `words=()`, so this is the
        path that matters most and the one a word-granularity cut must not
        change. Truncation by time, text carried through whole."""
        results = (
            _result(0, _segment(0.0, 110.0, "hola hermanos queridos de la iglesia")),
            _result(1, _segment(0.0, 20.0, "queridos de la iglesia y entonces")),
        )

        stitched = stitch_transcript(_plan(), results)

        assert all(segment.words == () for segment in stitched)
        assert stitched[0].text == "hola hermanos queridos de la iglesia"

    def test_a_time_truncated_segment_keeps_its_whole_text(self) -> None:
        """The pre-retrofit behaviour, pinned. Without words there is nothing to
        derive a boundary from, so the time cut stands and the text is not
        rebuilt."""
        results = (
            _result(0, _segment(0.0, 110.0, "texto que no se corta")),
            _result(1, _segment(0.0, 20.0, "otra cosa distinta aqui")),
        )

        stitched = stitch_transcript(_plan(), results)

        assert any("texto que no se corta" in s.text for s in stitched)


def _stitched_with_overlap() -> tuple[TranscriptSegment, ...]:
    """Two chunks whose overlap window contains the same word, timed on both."""
    first = _segment(
        0.0,
        110.0,
        "hola hermanos frontera ",
        _words((0.0, 40.0, "hola "), (40.0, 90.0, "hermanos "), (90.0, 110.0, "frontera ")),
    )
    second = _segment(
        0.0,
        20.0,
        "frontera y entonces",
        _words((0.0, 10.0, "frontera "), (10.0, 15.0, "y "), (15.0, 20.0, "entonces")),
    )
    return stitch_transcript(_plan(), (_result(0, first), _result(1, second)))
