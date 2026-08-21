"""Overlap reconciliation — deterministic, no ASR involvement.

Chunks are cut with overlap so no word is lost at a boundary, which means every
internal boundary produces the same words twice. Stitching decides which copy
survives. Getting it wrong is invisible in the artifact: a duplicated phrase reads
like the speaker repeated themselves, and a lost one reads like they never said it.
"""

from transcribe.domain.chunking import ChunkPlan, ChunkResult, ChunkState, PlannedChunk
from transcribe.domain.ids import make_job_id
from transcribe.domain.transcript import SegmentKind, TranscriptSegment
from transcribe.usecases.stitch_transcript import MIN_MATCH_TOKENS, stitch_transcript

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")

# Two chunks: [0, 605] and [600, 1205]. Contested window is [600, 605].
PLAN = ChunkPlan(
    job_id=JOB_ID,
    stride_s=600.0,
    overlap_s=5.0,
    chunks=(
        PlannedChunk(index=0, start_s=0.0, end_s=605.0),
        PlannedChunk(index=1, start_s=600.0, end_s=1205.0),
    ),
)


def _seg(
    start_s: float,
    end_s: float,
    text: str,
    kind: SegmentKind = SegmentKind.SPEECH,
    speaker: str | None = None,
) -> TranscriptSegment:
    """Chunk-LOCAL times, per the TranscriptionPort invariant."""
    return TranscriptSegment(
        start_s=start_s,
        end_s=end_s,
        text=text,
        speaker=speaker,
        confidence=0.9,
        kind=kind,
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


def _text(segments: tuple[TranscriptSegment, ...]) -> str:
    return " ".join(s.text for s in segments)


def test_single_chunk_passes_through_with_global_times() -> None:
    plan = ChunkPlan(
        job_id=JOB_ID,
        stride_s=600.0,
        overlap_s=5.0,
        chunks=(PlannedChunk(index=0, start_s=0.0, end_s=120.0),),
    )
    stitched = stitch_transcript(plan, (_result(0, _seg(0.0, 10.0, "hola")),))
    assert [(s.start_s, s.end_s, s.text) for s in stitched] == [(0.0, 10.0, "hola")]


def test_chunk_local_times_become_global() -> None:
    """The one place chunk-local becomes track-relative. Nothing downstream re-offsets."""
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, _seg(10.0, 20.0, "primera parte")),
            _result(1, _seg(100.0, 110.0, "segunda parte")),
        ),
    )
    assert (stitched[0].start_s, stitched[0].end_s) == (10.0, 20.0)
    # Chunk 1 starts at 600.0, so its local 100-110 lands at 700-710.
    assert (stitched[-1].start_s, stitched[-1].end_s) == (700.0, 710.0)


def test_matched_overlap_is_spoken_once() -> None:
    """Both chunks decode the same six words in the window; the output keeps one."""
    stitched = stitch_transcript(
        PLAN,
        (
            _result(
                0,
                _seg(590.0, 597.0, "esto es lo que quiero decir"),
                _seg(597.0, 605.0, "y ahora viene la parte importante"),
            ),
            _result(
                1,
                _seg(0.0, 5.0, "y ahora viene la parte importante"),
                _seg(5.0, 40.0, "porque esto cambia todo"),
            ),
        ),
    )
    assert _text(stitched).count("y ahora viene la parte importante") == 1


def test_matched_overlap_loses_no_words() -> None:
    stitched = stitch_transcript(
        PLAN,
        (
            _result(
                0,
                _seg(590.0, 597.0, "esto es lo que quiero decir"),
                _seg(597.0, 605.0, "y ahora viene la parte importante"),
            ),
            _result(
                1,
                _seg(0.0, 5.0, "y ahora viene la parte importante"),
                _seg(5.0, 40.0, "porque esto cambia todo"),
            ),
        ),
    )
    joined = _text(stitched)
    for phrase in (
        "esto es lo que quiero decir",
        "y ahora viene la parte importante",
        "porque esto cambia todo",
    ):
        assert phrase in joined


def test_results_are_sorted_by_chunk_index_not_arrival_order() -> None:
    """Slice 4 retries chunks, so results can arrive out of order."""
    out_of_order = (
        _result(1, _seg(100.0, 110.0, "segunda")),
        _result(0, _seg(10.0, 20.0, "primera")),
    )
    stitched = stitch_transcript(PLAN, out_of_order)
    assert [s.text for s in stitched] == ["primera", "segunda"]


def test_accents_make_words_distinct() -> None:
    """Spanish `si` and `sí` are different words; a tokenizer that folds accents
    would match them and cut in the wrong place."""
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, _seg(598.0, 605.0, "si si si si")),
            _result(1, _seg(0.0, 5.0, "sí sí sí sí"), _seg(5.0, 20.0, "continua aqui")),
        ),
    )
    # No token match, so the fallback runs rather than treating these as the same phrase.
    joined = _text(stitched)
    assert "si si si si" in joined or "sí sí sí sí" in joined
    assert "continua aqui" in joined


def test_match_shorter_than_the_minimum_does_not_cut() -> None:
    """Three shared tokens is coincidence, not the same utterance."""
    assert MIN_MATCH_TOKENS == 4
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, _seg(598.0, 605.0, "uno dos tres")),
            _result(1, _seg(0.0, 5.0, "uno dos tres"), _seg(5.0, 20.0, "resto")),
        ),
    )
    assert "resto" in _text(stitched)


# --- Fallback: routine on this material, not exceptional -----------------------


def test_fallback_snaps_to_the_nearest_segment_boundary() -> None:
    """Window [600, 605], midpoint 602.5, only boundary inside is 604."""
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, _seg(598.0, 604.0, "algo completamente distinto")),
            _result(1, _seg(0.0, 5.0, "otra cosa"), _seg(5.0, 20.0, "y sigue")),
        ),
    )
    assert any(s.start_s == 604.0 for s in stitched)


def test_fallback_uses_the_raw_midpoint_when_no_boundary_is_inside() -> None:
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, _seg(590.0, 620.0, "un segmento largo sin cortes")),
            _result(1, _seg(0.0, 5.0, "otra cosa"), _seg(5.0, 20.0, "y sigue")),
        ),
    )
    assert any(s.end_s == 602.5 or s.start_s == 602.5 for s in stitched)


# --- Straddling segments, kind preservation, and plan integrity ---------------


def test_segment_straddling_the_cut_is_truncated_not_duplicated() -> None:
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, _seg(598.0, 604.0, "nuestra version")),
            _result(1, _seg(0.0, 5.0, "su version"), _seg(5.0, 20.0, "y sigue")),
        ),
    )
    # Cut snaps to 604; chunk 1's [600, 605] straddles it and starts at the cut.
    straddler = next(s for s in stitched if s.text == "su version")
    assert straddler.start_s == 604.0
    assert straddler.end_s == 605.0
    assert [s.text for s in stitched].count("su version") == 1


def test_punctuation_and_case_do_not_prevent_a_match() -> None:
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, _seg(597.0, 605.0, "Y ahora, viene la parte importante.")),
            _result(
                1,
                _seg(0.0, 5.0, "y ahora viene la parte importante"),
                _seg(5.0, 40.0, "porque esto cambia todo"),
            ),
        ),
    )
    joined = _text(stitched).lower()
    assert joined.count("viene la parte importante") == 1
