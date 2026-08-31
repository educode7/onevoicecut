"""Overlap reconciliation — deterministic, no ASR involvement.

Chunks are cut with overlap so no word is lost at a boundary, which means every
internal boundary produces the same words twice. Stitching decides which copy
survives. Getting it wrong is invisible in the artifact: a duplicated phrase reads
like the speaker repeated themselves, and a lost one reads like they never said it.
"""

import pytest

from onevoicecut.domain.chunking import ChunkPlan, ChunkResult, ChunkState, PlannedChunk
from onevoicecut.domain.ids import make_job_id
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment
from onevoicecut.usecases.stitch_transcript import MIN_MATCH_TOKENS, stitch_transcript

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


def test_output_is_ordered_by_start_time() -> None:
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
    starts = [s.start_s for s in stitched]
    assert starts == sorted(starts)


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


def test_fallback_keeps_content_outside_the_contested_window() -> None:
    """The cut is bounded by the window, so nothing beyond it can be discarded."""
    stitched = stitch_transcript(
        PLAN,
        (
            _result(
                0,
                _seg(100.0, 200.0, "muy anterior"),
                _seg(598.0, 604.0, "desacuerdo"),
            ),
            _result(
                1,
                _seg(0.0, 5.0, "otra version"),
                _seg(300.0, 320.0, "muy posterior"),
            ),
        ),
    )
    joined = _text(stitched)
    assert "muy anterior" in joined
    assert "muy posterior" in joined


def test_fallback_is_deterministic() -> None:
    results = (
        _result(0, _seg(598.0, 604.0, "algo distinto")),
        _result(1, _seg(0.0, 5.0, "otra cosa"), _seg(5.0, 20.0, "sigue")),
    )
    assert stitch_transcript(PLAN, results) == stitch_transcript(PLAN, results)


def test_music_in_the_overlap_window_falls_back_cleanly() -> None:
    """Two decodes of the same song rarely agree; this is the routine path."""
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, _seg(598.0, 604.0, "y volare sin ti", SegmentKind.MUSIC)),
            _result(
                1,
                _seg(0.0, 5.0, "volare junto a ti", SegmentKind.MUSIC),
                _seg(5.0, 30.0, "retomamos entonces", SegmentKind.SPEECH),
            ),
        ),
    )
    assert "retomamos entonces" in _text(stitched)
    assert all(s.end_s > s.start_s for s in stitched)


def test_chunk_with_nothing_in_the_window_does_not_discard_our_copy() -> None:
    """A music-only chunk can transcribe to nothing; that is not a reason to cut."""
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, _seg(598.0, 605.0, "lo que dijo al final")),
            _result(1, _seg(20.0, 40.0, "mucho despues")),
        ),
    )
    joined = _text(stitched)
    assert "lo que dijo al final" in joined
    assert "mucho despues" in joined


def test_chunk_result_with_no_segments_at_all_is_survivable() -> None:
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, _seg(590.0, 605.0, "unico contenido")),
            _result(1),
        ),
    )
    assert "unico contenido" in _text(stitched)


def test_no_empty_segment_is_ever_emitted() -> None:
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, _seg(598.0, 604.0, "algo distinto")),
            _result(1, _seg(0.0, 5.0, "otra cosa"), _seg(5.0, 20.0, "sigue")),
        ),
    )
    assert all(s.end_s > s.start_s for s in stitched)


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


def test_segment_entirely_before_the_cut_survives_untouched() -> None:
    original = _seg(100.0, 200.0, "intacto")
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, original, _seg(598.0, 604.0, "desacuerdo")),
            _result(1, _seg(0.0, 5.0, "otra"), _seg(5.0, 20.0, "sigue")),
        ),
    )
    assert stitched[0] == original


def test_kind_survives_the_cut() -> None:
    """A relabelled segment would let lyrics into the message export."""
    stitched = stitch_transcript(
        PLAN,
        (
            _result(
                0,
                _seg(500.0, 560.0, "la cancion entera", SegmentKind.MUSIC),
                _seg(598.0, 604.0, "hablado", SegmentKind.SPEECH),
            ),
            _result(
                1,
                _seg(0.0, 5.0, "otra version", SegmentKind.UNCERTAIN),
                _seg(5.0, 20.0, "y sigue", SegmentKind.SPEECH),
            ),
        ),
    )
    by_text = {s.text: s.kind for s in stitched}
    assert by_text["la cancion entera"] is SegmentKind.MUSIC
    assert by_text["hablado"] is SegmentKind.SPEECH
    assert by_text["otra version"] is SegmentKind.UNCERTAIN
    assert by_text["y sigue"] is SegmentKind.SPEECH


def test_speaker_and_confidence_survive_the_cut() -> None:
    stitched = stitch_transcript(
        PLAN,
        (
            _result(0, _seg(598.0, 604.0, "hablado", speaker="c00/S01")),
            _result(1, _seg(0.0, 5.0, "otra", speaker="c01/S00"), _seg(5.0, 20.0, "x")),
        ),
    )
    truncated = next(s for s in stitched if s.text == "otra")
    assert truncated.speaker == "c01/S00"
    assert truncated.confidence == 0.9


def test_a_missing_chunk_result_is_refused_not_silently_stitched() -> None:
    """A hole in the results would produce a transcript that reads as complete.

    Chunk 84 of 87 failing is a designed-for case; stitching around it and
    delivering the result as the transcript is not.
    """
    three = ChunkPlan(
        job_id=JOB_ID,
        stride_s=600.0,
        overlap_s=5.0,
        chunks=(
            PlannedChunk(index=0, start_s=0.0, end_s=605.0),
            PlannedChunk(index=1, start_s=600.0, end_s=1205.0),
            PlannedChunk(index=2, start_s=1200.0, end_s=1800.0),
        ),
    )
    with pytest.raises(ValueError, match="1"):
        stitch_transcript(
            three,
            (_result(0, _seg(0.0, 10.0, "a")), _result(2, _seg(0.0, 10.0, "c"))),
        )


def test_a_duplicated_chunk_result_is_refused() -> None:
    with pytest.raises(ValueError):
        stitch_transcript(
            PLAN,
            (
                _result(0, _seg(0.0, 10.0, "a")),
                _result(0, _seg(0.0, 10.0, "a again")),
                _result(1, _seg(0.0, 10.0, "b")),
            ),
        )


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
