"""Stages 5 and 6, and the trajectory they finish: clamp, fill, confidence.

Stages 2 to 4 decide where the window *wants* to be at every moment a subject was
found. This is the rest: putting that inside the frame, and answering for the
moments nobody was found at all.

**Every sampled point becomes exactly one keyframe.** That is what makes the
provenance requirement checkable — a trajectory with fewer keyframes than samples
has silently decided some moments do not need answering, and the one thing this
subsystem must never do is leave the origin of a position unstated.

Three fills, three origins, and the distinction between the last two is the whole
no-silent-degradation axis. A gap bounded by real detections on both sides and
short enough to bridge is `INTERPOLATED` — the subject was found either side, so
the position between them is an inference from evidence. A gap with no bounding
detection, or one too long to bridge, is `FALLBACK_CENTER` — nobody was found, and
saying so is what stops a clip framed on an empty pulpit from reading as a
successful render.

**Nothing is re-clamped after stage 5, and that is a proof rather than a
convention.** The frame is convex: both interpolation endpoints are inside it, so
every point on the segment between them is inside it, and a centred rect is inside
by construction. Re-clamping would be the same class of error as re-evening a
clamped value — an operation that looks defensive and can only move a correct
value.
"""

import ast
import inspect
from pathlib import Path

import pytest

from onevoicecut.domain.framing import (
    KeyframeOrigin,
    TimeSpan,
    TrackingConfidence,
    TrajectoryPolicy,
    crop_size_for,
)
from onevoicecut.domain.media import FrameSize
from onevoicecut.ports.subject_tracker import BoundingBox, SubjectDetection
from onevoicecut.usecases import plan_trajectory
from onevoicecut.usecases.plan_trajectory import build_trajectory

FRAME = FrameSize(width=1920, height=1080)
POLICY = TrajectoryPolicy()
SPAN = TimeSpan(start_s=0.0, end_s=10.0)

CROP_W, CROP_H = crop_size_for(FRAME, POLICY)
MAX_X = FRAME.width - CROP_W
CENTRED_X = (FRAME.width - CROP_W) // 2

BOX_W = 200
BOX_H = 500

# The sampling grid every fixture here uses. Derived, so a change to the rate
# cannot leave a hand-written time behind.
SAMPLE_HZ = 4.0


def _at(index: int) -> float:
    return index / SAMPLE_HZ


def _hit(index: int, centre_x: float) -> SubjectDetection:
    return SubjectDetection(
        at_s=_at(index),
        box=BoundingBox(
            x=int(centre_x - BOX_W / 2), y=300, width=BOX_W, height=BOX_H
        ),
        confidence=0.9,
    )


def _miss(index: int) -> SubjectDetection:
    return SubjectDetection(at_s=_at(index), box=None, confidence=None)


def _origins(detections: tuple[SubjectDetection, ...]) -> list[KeyframeOrigin]:
    return [k.origin for k in build_trajectory(detections, FRAME, SPAN, POLICY).keyframes]


class TestStageFiveClampsIntoTheFrame:
    def test_a_subject_at_the_left_edge_does_not_push_the_crop_out(self) -> None:
        """A centred crop around x=0 would start at a negative offset. The rect
        has to stay inside the picture that exists."""
        trajectory = build_trajectory((_hit(0, 0.0),), FRAME, SPAN, POLICY)

        assert trajectory.keyframes[0].rect.x == 0

    def test_a_subject_at_the_right_edge_does_not_push_the_crop_out(self) -> None:
        trajectory = build_trajectory(
            (_hit(0, float(FRAME.width)),), FRAME, SPAN, POLICY
        )

        assert trajectory.keyframes[0].rect.x == MAX_X

    def test_a_subject_in_the_middle_is_not_moved(self) -> None:
        """Clamping must be a bound, not a nudge. A rect already inside the frame
        comes through untouched, or the crop follows the frame instead of the
        preacher."""
        middle = FRAME.width / 2
        trajectory = build_trajectory((_hit(0, middle),), FRAME, SPAN, POLICY)

        assert trajectory.keyframes[0].rect.x == int(middle - CROP_W / 2)

    def test_the_crop_size_is_the_one_computed_for_the_clip(self) -> None:
        trajectory = build_trajectory((_hit(0, 500.0),), FRAME, SPAN, POLICY)

        assert (trajectory.keyframes[0].rect.width, trajectory.keyframes[0].rect.height) == (
            CROP_W,
            CROP_H,
        )

    def test_a_landscape_source_has_nowhere_vertical_to_go(self) -> None:
        """`crop_size_for` gives a full-height crop for anything 9:16 or wider,
        so `frame.height - crop_h` is zero and `y` has exactly one legal value.
        Asserted rather than assumed, because it is the reason stages 2 to 4
        could ignore the vertical axis at all."""
        assert CROP_H == FRAME.height

        trajectory = build_trajectory((_hit(0, 500.0),), FRAME, SPAN, POLICY)

        assert trajectory.keyframes[0].rect.y == 0

    def test_a_source_narrower_than_the_target_is_centred_vertically(self) -> None:
        """Here `y` genuinely has room, and nothing tracks vertically — stages 2
        to 4 produce a horizontal centre and nothing else. Centred is the only
        position not derived from evidence that does not claim to be."""
        # Narrower than 9:16, not equal to it. 1080x1920 is *exactly* 9:16 and
        # takes the wide branch, giving a full-height crop and no vertical room
        # at all — which would have made this test pass for the wrong reason.
        tall = FrameSize(width=1000, height=1920)
        crop_w, crop_h = crop_size_for(tall, POLICY)
        assert crop_h < tall.height  # the branch this test exists for

        trajectory = build_trajectory((_hit(0, 500.0),), tall, SPAN, POLICY)

        assert trajectory.keyframes[0].rect.y == (tall.height - crop_h) // 2


class TestStageSixBridgesAGapItCanSee:
    def _bounded_gap(self) -> tuple[SubjectDetection, ...]:
        """Tracked, two misses, tracked — a half-second gap at 4 Hz."""
        return (_hit(0, 400.0), _miss(1), _miss(2), _hit(3, 800.0))

    def test_the_gap_is_interpolated(self) -> None:
        assert _origins(self._bounded_gap()) == [
            KeyframeOrigin.TRACKED,
            KeyframeOrigin.INTERPOLATED,
            KeyframeOrigin.INTERPOLATED,
            KeyframeOrigin.TRACKED,
        ]

    def test_the_positions_move_continuously_between_the_bounds(self) -> None:
        """Monotone between the two tracked rects, and strictly inside them. A
        fill that jumped to one endpoint would satisfy "interpolated" as a label
        while showing a cut."""
        rects = [k.rect.x for k in build_trajectory(self._bounded_gap(), FRAME, SPAN, POLICY).keyframes]

        assert rects == sorted(rects)
        assert rects[0] < rects[1] < rects[2] < rects[3]

    def test_a_gap_longer_than_the_policy_allows_is_not_bridged(self) -> None:
        """The bound exists because a bridge over a long gap is a guess wearing
        a real keyframe's origin: the subject may have crossed the stage twice
        while nobody was looking."""
        far = int(POLICY.max_gap_s * SAMPLE_HZ) + 4
        detections = (_hit(0, 400.0), *(_miss(i) for i in range(1, far)), _hit(far, 800.0))

        assert set(_origins(detections)[1:-1]) == {KeyframeOrigin.FALLBACK_CENTER}

    def test_the_gap_is_measured_between_the_bounding_samples(self) -> None:
        """The discriminating case, and it pins a decision rather than an
        implementation detail.

        Six misses at 4 Hz: 1.75 s between the two tracked samples, but only
        1.25 s from the first miss to the last. Measuring the run instead would
        bridge a gap the policy forbids — the run always spans one sample
        interval less than the gap containing it, so the two measures straddle
        the threshold exactly here. The distance being inferred over is the one
        between the endpoints, and it is also the conservative choice.
        """
        detections = (_hit(0, 400.0), *(_miss(i) for i in range(1, 7)), _hit(7, 800.0))

        assert set(_origins(detections)[1:-1]) == {KeyframeOrigin.FALLBACK_CENTER}


class TestStageSixFallsBackWhenItCannotSee:
    def test_a_leading_gap_falls_back(self) -> None:
        """Nothing precedes it, so there is nothing to interpolate from. The
        spec names this case explicitly."""
        assert _origins((_miss(0), _miss(1), _hit(2, 800.0)))[:2] == [
            KeyframeOrigin.FALLBACK_CENTER,
            KeyframeOrigin.FALLBACK_CENTER,
        ]

    def test_a_trailing_gap_falls_back(self) -> None:
        assert _origins((_hit(0, 800.0), _miss(1), _miss(2)))[1:] == [
            KeyframeOrigin.FALLBACK_CENTER,
            KeyframeOrigin.FALLBACK_CENTER,
        ]

    def test_a_clip_with_no_detections_at_all_falls_back_throughout(self) -> None:
        assert set(_origins(tuple(_miss(i) for i in range(6)))) == {
            KeyframeOrigin.FALLBACK_CENTER
        }

    def test_a_fallback_rect_is_centred(self) -> None:
        trajectory = build_trajectory((_miss(0),), FRAME, SPAN, POLICY)

        assert trajectory.keyframes[0].rect.x == CENTRED_X

    def test_a_fallback_is_never_labelled_tracked(self) -> None:
        """The requirement in one line. A centred guess presented as a detection
        is the failure the whole provenance axis exists to prevent."""
        origins = _origins((_miss(0), _miss(1), _hit(2, 800.0)))

        assert KeyframeOrigin.TRACKED not in origins[:2]


class TestProvenanceIsCompleteAndHonest:
    def test_every_sampled_point_becomes_exactly_one_keyframe(self) -> None:
        """Fewer keyframes than samples would mean some moments went unanswered,
        and an unanswered moment has no origin to report."""
        detections = (_hit(0, 400.0), _miss(1), _hit(2, 800.0), _miss(3), _miss(4))

        trajectory = build_trajectory(detections, FRAME, SPAN, POLICY)

        assert len(trajectory.keyframes) == len(detections)

    def test_each_keyframe_reports_exactly_one_known_origin(self) -> None:
        detections = (_miss(0), _hit(1, 400.0), _miss(2), _hit(3, 800.0), _miss(4))

        for origin in _origins(detections):
            assert origin in set(KeyframeOrigin)

    def test_a_hit_is_reported_tracked(self) -> None:
        assert _origins((_hit(0, 400.0),)) == [KeyframeOrigin.TRACKED]

    def test_keyframe_times_are_the_sampled_times(self) -> None:
        """A position at the right place and the wrong second is a reframe that
        follows the preacher a beat late for the whole clip."""
        detections = (_hit(0, 400.0), _miss(1), _hit(2, 800.0))

        trajectory = build_trajectory(detections, FRAME, SPAN, POLICY)

        assert [k.at_s for k in trajectory.keyframes] == [d.at_s for d in detections]


class TestConfidenceIsReportedBeforeRendering:
    def test_a_predominantly_fallback_trajectory_is_flagged(self) -> None:
        detections = (_hit(0, 400.0), *(_miss(i) for i in range(1, 9)))

        assert (
            build_trajectory(detections, FRAME, SPAN, POLICY).tracking
            is TrackingConfidence.LOW_CONFIDENCE
        )

    def test_a_well_tracked_trajectory_is_not_flagged(self) -> None:
        detections = tuple(_hit(i, 400.0 + i * 8) for i in range(8))

        assert (
            build_trajectory(detections, FRAME, SPAN, POLICY).tracking
            is TrackingConfidence.WELL_TRACKED
        )

    def test_interpolated_keyframes_count_as_tracked(self) -> None:
        """They are bridged between two real detections, so the subject *was*
        found either side. Counting them as guesses would flag a clip that
        followed the preacher accurately throughout.

        Three misses, not two: with two the interpolated keyframes are exactly
        half the trajectory, which is not *above* the default threshold either
        way — so the assertion would hold whether or not they were miscounted.
        Three makes them the majority, which is the only arrangement that can
        tell the two behaviours apart.
        """
        detections = (_hit(0, 400.0), _miss(1), _miss(2), _miss(3), _hit(4, 420.0))

        assert (
            build_trajectory(detections, FRAME, SPAN, POLICY).tracking
            is TrackingConfidence.WELL_TRACKED
        )

    def test_a_ratio_exactly_at_the_threshold_is_not_flagged(self) -> None:
        """"Exceeds" in the spec is strictly greater, and the boundary has to
        fall on one side. Two fallbacks of four is exactly the default 0.5, and
        both values are exactly representable — so this is a real boundary
        rather than one a float lands near."""
        detections = (_miss(0), _miss(1), _hit(2, 400.0), _hit(3, 400.0))

        assert (
            build_trajectory(detections, FRAME, SPAN, POLICY).tracking
            is TrackingConfidence.WELL_TRACKED
        )

    def test_an_empty_trajectory_is_not_reported_as_well_tracked(self) -> None:
        """No keyframes is no evidence, and the safe reading of no evidence is
        the weaker claim. Reporting `WELL_TRACKED` for an empty trajectory is
        the "looks like success" failure in its purest form — and the ratio has
        no denominator to compute from either."""
        assert (
            build_trajectory((), FRAME, SPAN, POLICY).tracking
            is TrackingConfidence.LOW_CONFIDENCE
        )

    def test_the_threshold_comes_from_the_policy(self) -> None:
        """Derived, never a literal 0.5. The same trajectory has to flip when
        the policy moves, or the policy is decoration.

        A *leading* miss on purpose: a bounded one would interpolate and never
        reach the ratio at all, which would make this pass for a reason that has
        nothing to do with the threshold.
        """
        detections = (_miss(0), _hit(1, 400.0), _hit(2, 400.0), _hit(3, 400.0))
        strict = TrajectoryPolicy(max_fallback_ratio=0.1)

        assert (
            build_trajectory(detections, FRAME, SPAN, POLICY).tracking
            is TrackingConfidence.WELL_TRACKED
        )
        assert (
            build_trajectory(detections, FRAME, SPAN, strict).tracking
            is TrackingConfidence.LOW_CONFIDENCE
        )


class TestNothingLeavesTheFrame:
    @pytest.mark.parametrize(
        "pattern",
        [
            (0, 1, 0, 1, 0),
            (1, 1, 1, 0, 0),
            (0, 0, 0, 0, 0),
            (1, 0, 0, 0, 1),
            (1, 1, 1, 1, 1),
        ],
    )
    @pytest.mark.parametrize("centre_x", [-500.0, 0.0, 960.0, 1920.0, 5000.0])
    def test_every_keyframe_rect_is_inside_the_frame(
        self, pattern: tuple[int, ...], centre_x: float
    ) -> None:
        """The property, over hit/miss patterns crossed with subjects far outside
        the picture. Stage 6 does not re-clamp — it does not need to, and this is
        what proves the convexity argument rather than restating it."""
        detections = tuple(
            _hit(i, centre_x) if hit else _miss(i) for i, hit in enumerate(pattern)
        )

        for keyframe in build_trajectory(detections, FRAME, SPAN, POLICY).keyframes:
            assert 0 <= keyframe.rect.x <= FRAME.width - keyframe.rect.width
            assert 0 <= keyframe.rect.y <= FRAME.height - keyframe.rect.height


class TestATimeSpanCannotRunBackwards:
    def test_a_reversed_span_is_refused(self) -> None:
        """Found in review of 12a-i rather than in this slice's own scope. A
        span whose end precedes its start yields a negative `duration_s`, and
        the precedent is direct: `plan_chunks` raises on a non-positive duration
        to protect its own division, and `CropTrajectory` validates its
        invariant in `__post_init__`."""
        with pytest.raises(ValueError):
            TimeSpan(start_s=10.0, end_s=5.0)

    def test_an_empty_span_is_still_legal(self) -> None:
        """Degenerate, not incoherent: a zero-length span is a request to look
        at nothing, which is answerable. Reversed is a request that cannot be
        satisfied in any order."""
        assert TimeSpan(start_s=10.0, end_s=10.0).duration_s == 0.0


def test_the_module_loads_no_vision_weights() -> None:
    """The spec requirement, asserted structurally. A test that merely ran the
    arithmetic and passed would pass equally on a module that imports a detector
    behind a flag nobody set."""
    source = Path(inspect.getsourcefile(plan_trajectory) or "").read_text(encoding="utf-8")
    imported = {
        node.module or ""
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert not any(
        name.startswith(("onevoicecut.adapters", "onevoicecut.runtime", "torch", "cv2"))
        for name in imported
    )
