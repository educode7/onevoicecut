"""Who spoke when, decided without any of the machinery that answers it.

The diarizing call splits the way `declarations.py` did: every decision that can
be made from data alone — maximal-overlap assignment, stable per-chunk speaker
indices, the `c{chunk}/S{speaker}` namespace, and the build-once pipeline
lifecycle — is a pure function or an injectable seam tested here, on a checkout
carrying no torch, no pyannote, no gated weights. What is left for the
`localmodel` suite is the one thing only the real pipeline can prove: that the
call runs and the labels come back.

The namespace is the load-bearing convention. Pyannote's cluster identities
restart on every run, so chunk 4's `SPEAKER_00` and chunk 5's `SPEAKER_00` may
be different people; reconciling them is the `SpeakerResolver` seam (slice 9b).
Until then the per-chunk prefix is what makes "the same label means the same
voice" true — within the one chunk it promises, and nowhere else.
"""

from typing import Any

import pytest

from onevoicecut.adapters.asr.local.declarations import HF_TOKEN_ENV
from onevoicecut.adapters.asr.local.diarization import (
    LocalDiarizer,
    SpeakerRegion,
    assign_speakers,
    regions_from_annotation,
    speaker_indices,
    speaker_label,
)
from onevoicecut.domain.errors import EngineUnavailable
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment

TOKEN = "hf_not-a-real-token"


def _segment(
    start_s: float,
    end_s: float,
    *,
    text: str = "",
    kind: SegmentKind = SegmentKind.SPEECH,
    confidence: float | None = 0.9,
) -> TranscriptSegment:
    return TranscriptSegment(
        start_s=start_s,
        end_s=end_s,
        text=text,
        speaker=None,
        confidence=confidence,
        kind=kind,
    )


class TestTheNamespace:
    def test_the_label_names_the_chunk_before_the_speaker(self) -> None:
        assert speaker_label(3, 1) == "c03/S01"

    def test_both_halves_pad_to_two_digits(self) -> None:
        assert speaker_label(12, 0) == "c12/S00"

    def test_the_namespace_is_what_keeps_chunks_apart(self) -> None:
        """The same raw cluster id in two chunks is two different labels — the
        property slice 9b's resolver will later relax on purpose, and which must
        hold absolutely until it does."""
        assert speaker_label(4, 0) != speaker_label(5, 0)


class TestStableSpeakerIndices:
    def test_indices_follow_sorted_label_order(self) -> None:
        """Stable means derived from the annotation's own labels in sorted
        order, so the same annotation always yields the same numbering — and a
        run whose first voice happens to cluster as `SPEAKER_01` still gets a
        dense numbering starting at zero."""
        assert speaker_indices(["SPEAKER_01", "SPEAKER_00"]) == {
            "SPEAKER_00": 0,
            "SPEAKER_01": 1,
        }

    def test_repeated_labels_collapse(self) -> None:
        assert speaker_indices(["B", "A", "B"]) == {"A": 0, "B": 1}

    def test_unsorted_vendor_labels_still_number_densely(self) -> None:
        indices = speaker_indices(["SPEAKER_07", "SPEAKER_02"])

        assert sorted(indices.values()) == [0, 1]


class TestAnnotationExtraction:
    """Against a duck-typed stand-in: pyannote's `Annotation` is not installed
    on every checkout that runs this suite, and the extraction is a pure shape
    question — start, end, label — so a fake with the same iteration protocol
    proves exactly what the real one would."""

    def test_regions_carry_start_end_and_label(self) -> None:
        annotation = _FakeAnnotation(
            [(0.5, 2.25, "SPEAKER_00"), (3.0, 4.0, "SPEAKER_01")]
        )

        assert regions_from_annotation(annotation) == (
            (0.5, 2.25, "SPEAKER_00"),
            (3.0, 4.0, "SPEAKER_01"),
        )

    def test_an_empty_annotation_yields_no_regions(self) -> None:
        assert regions_from_annotation(_FakeAnnotation([])) == ()


class TestOverlapAssignment:
    def test_a_segment_inside_one_region_takes_its_speaker(self) -> None:
        regions: tuple[SpeakerRegion, ...] = ((0.0, 5.0, "SPEAKER_00"),)

        (labelled,) = assign_speakers([_segment(1.0, 2.0)], regions, chunk_index=0)

        assert labelled.speaker == "c00/S00"

    def test_the_larger_overlap_wins(self) -> None:
        """A Whisper segment straddling a speaker change belongs to whoever
        holds most of it: one label per segment is the contract, and the
        majority is the only choice that is not arbitrary."""
        regions: tuple[SpeakerRegion, ...] = (
            (0.0, 2.5, "SPEAKER_00"),
            (2.5, 6.0, "SPEAKER_01"),
        )

        (labelled,) = assign_speakers([_segment(2.0, 4.0)], regions, chunk_index=1)

        assert labelled.speaker == "c01/S01"

    def test_an_exact_tie_goes_to_the_earlier_region(self) -> None:
        """Deterministic, and documented because "first in annotation order" is
        a decision, not a default: annotations iterate in time order, so the
        tie goes to whoever spoke first."""
        regions: tuple[SpeakerRegion, ...] = (
            (0.0, 3.0, "SPEAKER_00"),
            (3.0, 6.0, "SPEAKER_01"),
        )

        (labelled,) = assign_speakers([_segment(2.0, 4.0)], regions, chunk_index=0)

        assert labelled.speaker == "c00/S00"

    def test_a_segment_no_region_touches_keeps_no_speaker(self) -> None:
        """The decision the spec left silent, made against the project's own
        invariant: `_tile` restores filtered non-speech ranges as segments, and
        a musical range has no speaker. Handing one the preacher's label would
        fabricate attribution — the same class of quiet lie as returning
        unlabelled output for a speaker-mode job, pointed the other way."""
        regions: tuple[SpeakerRegion, ...] = ((0.0, 2.0, "SPEAKER_00"),)
        music = _segment(10.0, 20.0, kind=SegmentKind.MUSIC)

        (labelled,) = assign_speakers([music], regions, chunk_index=0)

        assert labelled.speaker is None

    def test_labelling_touches_nothing_but_the_speaker(self) -> None:
        segment = _segment(1.0, 2.0, text="hola", confidence=0.42)
        regions: tuple[SpeakerRegion, ...] = ((0.0, 5.0, "SPEAKER_00"),)

        (labelled,) = assign_speakers([segment], regions, chunk_index=2)

        assert (labelled.start_s, labelled.end_s, labelled.text) == (1.0, 2.0, "hola")
        assert labelled.confidence == 0.42
        assert labelled.kind is SegmentKind.SPEECH

    def test_an_empty_annotation_leaves_every_segment_unlabelled(self) -> None:
        segments = [_segment(0.0, 1.0), _segment(1.0, 2.0)]

        labelled = assign_speakers(segments, (), chunk_index=0)

        assert [s.speaker for s in labelled] == [None, None]

    def test_labels_carry_the_chunk_index_they_came_from(self) -> None:
        regions: tuple[SpeakerRegion, ...] = ((0.0, 5.0, "SPEAKER_00"),)

        (labelled,) = assign_speakers([_segment(1.0, 2.0)], regions, chunk_index=7)

        assert labelled.speaker == "c07/S00"


class TestThePipelineIsPaidForOnce:
    """The 9a-i design note, held to: the proof belongs with the diarizing call,
    where a job that actually asked for speakers pays for it once. These tests
    inject the loader, so the lifecycle is proven with no pipeline, no torch and
    no gated weights anywhere near the default suite."""

    def test_nothing_is_built_until_a_job_asks(self) -> None:
        calls: list[str] = []

        def loader(token: str) -> Any:
            calls.append(token)
            return object()

        diarizer = LocalDiarizer(TOKEN, loader=loader)

        assert calls == []

    def test_the_pipeline_is_built_once_and_reused(self) -> None:
        calls: list[str] = []

        def loader(token: str) -> Any:
            calls.append(token)
            return object()

        diarizer = LocalDiarizer(TOKEN, loader=loader)

        assert diarizer.pipeline() is diarizer.pipeline()
        assert calls == [TOKEN]

    def test_a_build_failure_surfaces_as_a_domain_error(self) -> None:
        """Gated weights, a revoked licence and an offline machine all fail
        here, and none of them may reach the worker loop as a raw provider
        exception — the adapter boundary is where library errors become domain
        ones, and the message keeps the original complaint for the log."""

        def gated(token: str) -> Any:
            raise OSError("401 Client Error: model is gated")

        with pytest.raises(EngineUnavailable, match="gated") as failure:
            LocalDiarizer(TOKEN, loader=gated).pipeline()

        assert isinstance(failure.value.__cause__, OSError)

    def test_a_loader_that_returns_none_is_a_refusal(self) -> None:
        """`Pipeline.from_pretrained` is typed `Optional` in pyannote 4.x: None
        is a real answer, and caching it would rebuild on every chunk of a job
        that can never diarize."""

        def absent(token: str) -> Any:
            return None

        with pytest.raises(EngineUnavailable):
            LocalDiarizer(TOKEN, loader=absent).pipeline()

    @pytest.mark.parametrize("token", [None, "", "   "])
    def test_a_missing_token_is_refused_by_name(self, token: str | None) -> None:
        """Unreachable through `transcribe` — the capability check refuses
        first — but the diarizer must still refuse on its own terms rather than
        hand a blank credential to the hub and fail with somebody else's
        message."""
        with pytest.raises(EngineUnavailable, match=HF_TOKEN_ENV):
            LocalDiarizer(token)


class _FakeTurn:
    def __init__(self, start: float, end: float) -> None:
        self.start = start
        self.end = end


class _FakeAnnotation:
    def __init__(self, turns: list[tuple[float, float, str]]) -> None:
        self._turns = turns

    def itertracks(self, yield_label: bool = False) -> Any:
        return iter(
            (_FakeTurn(start, end), track, label)
            for track, (start, end, label) in enumerate(self._turns)
        )
