"""Music is kept out of the message. It must not be kept out of the clips.

Two exclusions look alike and are opposites. `speech_windows` drops `MUSIC`
because sung lyrics are not the preacher's argument and a model cannot be trusted
to tell the difference from a marker. But the whole reason `SegmentKind` marks
rather than filters — the reason a musical range keeps its timestamps instead of
vanishing at the ASR boundary — is that **the singer's moment is often the best
footage in the service**.

So candidate resolution is `kind`-agnostic, and this module holds it there. A
clip whose range covers music resolves like any other: the ids are transcript
indices, the span runs from the earliest start to the latest end, and nothing in
between is consulted about what kind it is.

That property is easy to lose by accident later — a well-meaning filter added to
`rank_clip_candidates` would look like consistency with the windowing next to it
and would quietly delete the material this project went out of its way to keep.
"""

import json

from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment
from onevoicecut.usecases.generate_artifacts import (
    MapPartial,
    MapWindow,
    parse_map_response,
    rank_clip_candidates,
    speech_windows,
)

# Speech, then a sung chorus, then speech again — a service, in miniature.
KINDS = (
    SegmentKind.SPEECH,
    SegmentKind.MUSIC,
    SegmentKind.MUSIC,
    SegmentKind.SPEECH,
    SegmentKind.UNCERTAIN,
    SegmentKind.SPEECH,
)


def _segments() -> tuple[TranscriptSegment, ...]:
    return tuple(
        TranscriptSegment(
            start_s=float(i * 10),
            end_s=float(i * 10 + 10),
            text=f"palabra{i:03d}",
            speaker=None,
            confidence=0.9,
            kind=kind,
        )
        for i, kind in enumerate(KINDS)
    )


def _partial_citing(*ids: int) -> MapPartial:
    window = MapWindow(segment_ids=tuple(range(len(KINDS))), text="irrelevante")
    return parse_map_response(
        json.dumps(
            {
                "summary": "resumen",
                "moments": [
                    {
                        "segment_ids": list(ids),
                        "hook": "gancho",
                        "quote": "cita",
                        "rationale": "porque si",
                        "score": 0.7,
                    }
                ],
            }
        ),
        window,
    )


class TestACandidateMaySpanMusic:
    def test_it_is_not_rejected_for_covering_a_sung_passage(self) -> None:
        """The spec scenario. Segments 1 and 2 are the chorus; a moment anchored
        on the speech either side of it is a perfectly good clip."""
        candidates = rank_clip_candidates(
            (_partial_citing(0, 3),), _segments(), max_candidates=5
        )

        assert len(candidates) == 1

    def test_its_range_covers_the_music_between(self) -> None:
        """0 runs 0–10 s and 3 runs 30–40 s, so the clip is 0–40 s and the two
        musical segments are inside it. Trimming to the cited segments would cut
        the chorus out of a clip that exists because of it."""
        candidates = rank_clip_candidates(
            (_partial_citing(0, 3),), _segments(), max_candidates=5
        )

        assert (candidates[0].start_s, candidates[0].end_s) == (0.0, 40.0)

    def test_uncertain_inside_a_range_is_equally_fine(self) -> None:
        """`UNCERTAIN` is kept out of the message for a stronger reason than
        `MUSIC` — the engine did not establish what it was. That says nothing
        about whether the footage is usable."""
        candidates = rank_clip_candidates(
            (_partial_citing(3, 5),), _segments(), max_candidates=5
        )

        assert (candidates[0].start_s, candidates[0].end_s) == (30.0, 60.0)

    def test_resolution_reads_no_kind_at_all(self) -> None:
        """Characterises the property rather than one instance of it: the same
        ids over a transcript whose kinds are all rewritten resolve identically.

        A filter added to `rank_clip_candidates` later would look like
        consistency with the windowing beside it, and would quietly delete the
        material `SegmentKind` marks-rather-than-filters to preserve.
        """
        all_music = tuple(
            TranscriptSegment(
                start_s=s.start_s,
                end_s=s.end_s,
                text=s.text,
                speaker=None,
                confidence=s.confidence,
                kind=SegmentKind.MUSIC,
            )
            for s in _segments()
        )

        assert rank_clip_candidates(
            (_partial_citing(0, 3),), _segments(), max_candidates=5
        ) == rank_clip_candidates(
            (_partial_citing(0, 3),), all_music, max_candidates=5
        )


class TestTheTwoExclusionsStayApart:
    def test_windowing_still_drops_music(self) -> None:
        """The other half of the pair, asserted here so the pair is visible in
        one place: what is excluded from the *message* is still excluded."""
        windows = speech_windows(_segments())

        assert [i for w in windows for i in w.segment_ids] == [0, 3, 5]

    def test_but_a_dropped_segment_is_still_addressable(self) -> None:
        """The dropped ones keep their timestamps in the transcript, which is
        what makes them reachable as clip material at all."""
        segments = _segments()

        assert (segments[1].start_s, segments[1].end_s) == (10.0, 20.0)


def test_a_purely_musical_clip_cannot_be_proposed_today() -> None:
    """A structural limitation, characterised rather than fixed.

    The model only ever sees `SPEECH` segments, so it can only cite those — a
    moment's ids are validated against the window that produced it, and a speech
    window contains no musical id. A clip can therefore *span* a chorus, but the
    chorus alone can never be proposed as the clip.

    The spec permits musical ranges as candidates and says a sung passage can be
    strong short-form material; today the strongest such passage is unreachable
    unless speech brackets it. Recorded here because it is the shape of Q9 —
    whether ranking should favour these — one step further out: they cannot be
    favoured before they can be proposed.
    """
    music_only = MapWindow(segment_ids=(0, 3, 5), text="solo habla")

    partial = parse_map_response(
        json.dumps({"summary": "r", "moments": []}), music_only
    )

    assert partial.moments == ()
    assert 1 not in music_only.segment_ids
