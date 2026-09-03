"""Where "is this the same person as in the last chunk?" will be answered.

Diarization is per chunk, and it has to be — a three-hour sermon is not held in
memory at once. So the labels it produces are namespaced, `c00/S01`, and `S01` in
chunk 0 has no relationship to `S01` in chunk 1 beyond both being the second voice
their own chunk happened to notice. Across 87 chunks the same preacher collects 87
distinct identities, and a transcript labelled that way looks precise while saying
nothing.

Nobody knows yet what should decide it. Voice embeddings are the obvious answer
and they are not free, and this project has not measured whether the accuracy is
worth the cost on this material. What *is* knowable now is **where** the answer
goes, and that the stitcher is the only place with every chunk's labels in front
of it at once.

So this unit builds the seam and nothing else. The default resolver renames
nothing, which makes it exactly today's behaviour — the seam is provably free
until someone fills it.

The constraint is the interesting part. A resolver returns a **mapping between
labels**, not segments: it can rename a speaker and it cannot move a boundary,
drop a phrase or reorder anything. Overlap reconciliation took a slice of its own
to get right, and a seam that let a future speaker-identity experiment reach into
it would put that at risk to answer an unrelated question.
"""

from collections.abc import Mapping

import pytest

from onevoicecut.domain.chunking import ChunkPlan, ChunkResult, ChunkState, PlannedChunk
from onevoicecut.domain.ids import make_job_id
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment
from onevoicecut.usecases.stitch_transcript import SpeakerResolver, stitch_transcript

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")

# Two chunks that do not overlap, so every assertion here is about the resolver
# rather than about which copy of a contested phrase survived.
STRIDE_S = 100.0


def _segment(
    start_s: float, end_s: float, text: str, speaker: str | None
) -> TranscriptSegment:
    return TranscriptSegment(
        start_s=start_s,
        end_s=end_s,
        text=text,
        speaker=speaker,
        confidence=0.9,
        kind=SegmentKind.SPEECH,
    )


def _plan() -> ChunkPlan:
    return ChunkPlan(
        job_id=JOB_ID,
        stride_s=STRIDE_S,
        overlap_s=0.0,
        chunks=(
            PlannedChunk(index=0, start_s=0.0, end_s=STRIDE_S),
            PlannedChunk(index=1, start_s=STRIDE_S, end_s=STRIDE_S * 2),
        ),
    )


def _results() -> tuple[ChunkResult, ...]:
    """The same two people in both chunks, labelled independently by each.

    `c00/S00` and `c01/S00` are the preacher in both. Nothing in the data says
    so, which is the whole problem.
    """
    return (
        _result(0, (_segment(0.0, 10.0, "hermanos", "c00/S00"),
                    _segment(10.0, 20.0, "amen", "c00/S01"))),
        _result(1, (_segment(0.0, 10.0, "y entonces", "c01/S00"),
                    _segment(10.0, 20.0, "asi sea", "c01/S01"))),
    )


def _result(
    index: int, segments: tuple[TranscriptSegment, ...]
) -> ChunkResult:
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


class TestTheDefaultChangesNothing:
    def test_namespaced_labels_pass_through_unchanged(self) -> None:
        """Today's behaviour, now expressed as a default rather than as an
        absence — which is what makes the seam free to add."""
        stitched = stitch_transcript(_plan(), _results())

        assert [s.speaker for s in stitched] == [
            "c00/S00",
            "c00/S01",
            "c01/S00",
            "c01/S01",
        ]

    def test_unlabelled_segments_stay_unlabelled(self) -> None:
        """`speaker=None` is what every single-speaker job produces, which is
        most of them. A default resolver inventing a label for those would put
        speaker attribution on transcripts nobody asked to diarize."""
        results = (
            _result(0, (_segment(0.0, 10.0, "hermanos", None),)),
            _result(1, (_segment(0.0, 10.0, "y entonces", None),)),
        )

        stitched = stitch_transcript(_plan(), results)

        assert all(s.speaker is None for s in stitched)


class TestAResolverSubstitutes:
    def test_its_mapping_is_applied_to_every_segment(self) -> None:
        """The point of the seam: one preacher across two chunks, one label."""
        stitched = stitch_transcript(
            _plan(),
            _results(),
            resolve_speakers=_mapping_to(
                {"c00/S00": "S00", "c01/S00": "S00", "c00/S01": "S01", "c01/S01": "S01"}
            ),
        )

        assert [s.speaker for s in stitched] == ["S00", "S01", "S00", "S01"]

    def test_a_label_it_omits_passes_through(self) -> None:
        """A partial answer is a legitimate one. A resolver confident about the
        preacher and unsure about a guest must be able to say so, rather than
        being forced to guess to stay well-formed."""
        stitched = stitch_transcript(
            _plan(),
            _results(),
            resolve_speakers=_mapping_to({"c01/S00": "c00/S00"}),
        )

        assert [s.speaker for s in stitched] == [
            "c00/S00",
            "c00/S01",
            "c00/S00",
            "c01/S01",
        ]

    def test_it_sees_every_chunks_labels_at_once(self) -> None:
        """Why the stitcher and not the adapter. Cross-chunk identity cannot be
        decided inside a chunk, by definition — the stitcher is the first point
        that holds them all."""
        seen: list[tuple[str, ...]] = []

        stitch_transcript(
            _plan(), _results(), resolve_speakers=_recording(seen)
        )

        assert seen == [("c00/S00", "c00/S01", "c01/S00", "c01/S01")]

    def test_it_is_asked_once_for_the_whole_transcript(self) -> None:
        """Not per chunk and not per segment. A resolver that pays for voice
        embeddings should pay once, and one that reasons about who speaks most
        cannot work from a fragment."""
        seen: list[tuple[str, ...]] = []

        stitch_transcript(
            _plan(), _results(), resolve_speakers=_recording(seen)
        )

        assert len(seen) == 1

    def test_it_is_not_asked_at_all_when_nothing_is_labelled(self) -> None:
        """A single-speaker job must not pay for a resolver it has no use for —
        and on the implementation everyone expects, that cost is a model."""
        seen: list[tuple[str, ...]] = []
        results = (
            _result(0, (_segment(0.0, 10.0, "hermanos", None),)),
            _result(1, (_segment(0.0, 10.0, "y entonces", None),)),
        )

        stitch_transcript(_plan(), results, resolve_speakers=_recording(seen))

        assert seen == []


class TestItCannotReachIntoTheStitching:
    def test_times_and_text_are_identical_with_and_without_a_resolver(self) -> None:
        """The constraint the seam's shape enforces. Overlap reconciliation took
        a slice of its own to get right, and a speaker-identity experiment must
        not be able to put it at risk."""
        plain = stitch_transcript(_plan(), _results())
        resolved = stitch_transcript(
            _plan(),
            _results(),
            resolve_speakers=_mapping_to({"c00/S00": "S00", "c01/S00": "S00"}),
        )

        assert [(s.start_s, s.end_s, s.text) for s in plain] == [
            (s.start_s, s.end_s, s.text) for s in resolved
        ]

    def test_an_overlapping_plan_reconciles_the_same_either_way(self) -> None:
        """The same assertion where it actually costs something: a contested
        window, resolved before the resolver is ever consulted."""
        plan = ChunkPlan(
            job_id=JOB_ID,
            stride_s=STRIDE_S,
            overlap_s=10.0,
            chunks=(
                PlannedChunk(index=0, start_s=0.0, end_s=STRIDE_S + 10.0),
                PlannedChunk(index=1, start_s=STRIDE_S, end_s=STRIDE_S * 2),
            ),
        )
        results = (
            _result(0, (_segment(0.0, 100.0, "hermanos queridos de la iglesia", "c00/S00"),
                        _segment(100.0, 110.0, "y entonces dijo el senor", "c00/S00"))),
            _result(1, (_segment(0.0, 10.0, "y entonces dijo el senor", "c01/S00"),
                        _segment(10.0, 20.0, "asi sea", "c01/S00"))),
        )

        plain = stitch_transcript(plan, results)
        resolved = stitch_transcript(
            plan, results, resolve_speakers=_mapping_to({"c01/S00": "c00/S00"})
        )

        assert [(s.start_s, s.end_s, s.text) for s in plain] == [
            (s.start_s, s.end_s, s.text) for s in resolved
        ]

    def test_a_resolver_returning_nothing_is_the_default(self) -> None:
        """An empty mapping and no resolver must be the same thing, so a
        resolver that declines to decide degrades to today's behaviour rather
        than to a blank transcript."""
        assert stitch_transcript(
            _plan(), _results(), resolve_speakers=lambda labels: {}
        ) == stitch_transcript(_plan(), _results())


def _mapping_to(mapping: Mapping[str, str]) -> SpeakerResolver:
    def resolve(labels: tuple[str, ...]) -> Mapping[str, str]:
        return mapping

    return resolve


def _recording(seen: list[tuple[str, ...]]) -> SpeakerResolver:
    def resolve(labels: tuple[str, ...]) -> Mapping[str, str]:
        seen.append(labels)
        return {}

    return resolve


@pytest.mark.parametrize("label", ["c00/S00", "c99/S42"])
def test_the_namespaced_shape_survives_the_stitcher(label: str) -> None:
    """The label format is the adapter's (`c{index:02d}/S{speaker:02d}`) and the
    stitcher is not entitled to reinterpret it — it hands whatever it was given
    to the resolver verbatim, so changing the namespace scheme later is an
    adapter change rather than a two-module one."""
    seen: list[tuple[str, ...]] = []
    results = (
        _result(0, (_segment(0.0, 10.0, "hermanos", label),)),
        _result(1, (_segment(0.0, 10.0, "y entonces", None),)),
    )

    stitch_transcript(_plan(), results, resolve_speakers=_recording(seen))

    assert seen == [(label,)]
