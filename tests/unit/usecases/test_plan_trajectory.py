"""Stages 2 to 4 of the reframe pipeline: centres, smoothing, dead-zone.

Pure arithmetic over a detection series, which is what the spec means by
"testable with no model weights". Nothing here loads a detector; the fake scripts
the motion so that whatever survives smoothing is attributable to smoothing
rather than to what the subject happened to do.

**The stage order is the slice.** Design.md fixes smoothing *before* the
dead-zone and says reversing them "produces a crop that twitches at exactly the
threshold". That claim was prose until now — the last class here runs both
orderings over the same jitter and shows the reversed one committing on every
oscillation while the specified one commits never.

Three failures this module exists to catch, none of which look like failures in
the output:

- **Averaging across a miss.** A smoothed centre dragged toward whatever filled
  the gap walks off the subject slowly enough to read as camera movement.
- **A dead-zone applied to raw jitter.** It trips on every oscillation, so the
  "stabilised" crop shakes more than the unstabilised one would.
- **A dead-zone that commits partway.** Holding then moving by the threshold
  instead of to the target leaves the crop permanently trailing the subject by
  one dead-zone width.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.framing import TimeSpan, TrajectoryPolicy
from onevoicecut.domain.ids import make_media_id
from onevoicecut.domain.media import FrameSize, SourceMedia
from onevoicecut.ports.subject_tracker import BoundingBox, SubjectDetection
from onevoicecut.usecases.plan_trajectory import (
    Centre,
    apply_dead_zone,
    desired_centres,
    smooth_centres,
)
from tests.fakes.subject_tracker import FakeSubjectTrackerPort

FRAME = FrameSize(width=1920, height=1080)
POLICY = TrajectoryPolicy()

# Derived exactly as the production code derives it, never written as a literal.
# A literal 76.8 is equal to `0.04 * 1920`, but `(900.0 + 76.8) - 900.0` is
# 76.79999999999995 — so a boundary case built by adding it to a base lands just
# *under* the threshold and silently stops testing the boundary at all.
DEAD_ZONE_PX = POLICY.dead_zone_fraction * FRAME.width

BOX_W = 200
BOX_H = 500


def _hit(at_s: float, centre_x: float) -> SubjectDetection:
    """A detection whose box is centred on `centre_x`."""
    return SubjectDetection(
        at_s=at_s,
        box=BoundingBox(
            x=int(centre_x - BOX_W / 2), y=300, width=BOX_W, height=BOX_H
        ),
        confidence=0.9,
    )


def _miss(at_s: float) -> SubjectDetection:
    return SubjectDetection(at_s=at_s, box=None, confidence=None)


def _media() -> SourceMedia:
    return SourceMedia(
        media_id=make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ"),
        original_filename="predicacion.mp4",
        stored_path=Path("source"),
        size_bytes=4096,
        container="mp4",
        checksum="deadbeef",
    )


def _jitter(count: int, *, around: float, amplitude: float, hz: float = 4.0
) -> tuple[SubjectDetection, ...]:
    """A subject standing still, detected badly.

    The amplitude is chosen by the caller relative to the dead-zone, because
    every interesting question here is about which side of that threshold the
    raw and the smoothed series land on.
    """
    return tuple(
        _hit(index / hz, around + (amplitude if index % 2 == 0 else -amplitude))
        for index in range(count)
    )


class TestStageTwoCentres:
    def test_a_hit_maps_to_its_boxs_horizontal_centre(self) -> None:
        """Pure geometry. The vertical axis is settled in 12b-ii along with the
        clamp, because for a landscape source the crop is full height and `y`
        has nowhere to go."""
        centres = desired_centres((_hit(0.0, 900.0),))

        assert centres == (Centre(at_s=0.0, x=900.0),)

    def test_a_miss_contributes_no_centre(self) -> None:
        """Absent, not zero-filled and not centred. Stages 3 and 4 operate on the
        tracked subsequence, and a placeholder here would be indistinguishable
        from a real detection by the time it reached them."""
        centres = desired_centres((_hit(0.0, 900.0), _miss(0.25), _hit(0.5, 908.0)))

        assert [c.at_s for c in centres] == [0.0, 0.5]

    def test_an_all_miss_series_produces_nothing(self) -> None:
        """Not one centred placeholder. A clip nobody was found in has no tracked
        subsequence at all, and inventing one would make the fallback stage
        (12b-ii) unable to tell that from a subject who never moved."""
        assert desired_centres((_miss(0.0), _miss(0.25))) == ()

    def test_order_and_times_survive(self) -> None:
        centres = desired_centres(
            (_hit(0.0, 900.0), _hit(0.25, 950.0), _hit(0.5, 1000.0))
        )

        assert [(c.at_s, c.x) for c in centres] == [
            (0.0, 900.0),
            (0.25, 950.0),
            (0.5, 1000.0),
        ]

    def test_it_reads_the_fake_detectors_own_output(self) -> None:
        """The task's wording: stage 2 over the fake detector's output rather
        than over hand-built detections only. The fake drifts 4 px per sample
        from a box centred at 900."""
        detections = FakeSubjectTrackerPort().detect(
            _media(), TimeSpan(10.0, 11.0), sample_hz=4.0
        )

        centres = desired_centres(detections)

        assert [c.x for c in centres] == [900.0, 904.0, 908.0, 912.0]


class TestStageThreeSmoothing:
    def test_jitter_does_not_reproduce_in_the_output(self) -> None:
        """The spec scenario. A subject standing still, detected badly, must not
        produce a trajectory that shakes."""
        centres = desired_centres(_jitter(12, around=1000.0, amplitude=90.0))

        smoothed = smooth_centres(centres, window_s=POLICY.smoothing_window_s)

        spread = max(c.x for c in smoothed) - min(c.x for c in smoothed)
        assert spread < 90.0

    def test_genuine_movement_survives(self) -> None:
        """Smoothing must not become deletion. A subject who actually crossed the
        stage has to be followed, or the whole reframe is a fixed centre crop
        with extra steps."""
        centres = desired_centres(
            tuple(_hit(i / 4.0, 600.0 + i * 40.0) for i in range(12))
        )

        smoothed = smooth_centres(centres, window_s=POLICY.smoothing_window_s)

        assert smoothed[-1].x - smoothed[0].x > 300.0

    def test_the_window_is_centred_not_trailing(self) -> None:
        """A trailing average lags the subject by half a window — a third of a
        second at the default — which reads as the camera operator reacting late
        rather than as stabilisation."""
        centres = desired_centres(
            tuple(_hit(i / 4.0, 100.0 * i) for i in range(9))
        )

        smoothed = smooth_centres(centres, window_s=1.0)

        # The middle sample of a symmetric ramp is unmoved by a centred window.
        assert smoothed[4].x == pytest.approx(centres[4].x)

    def test_a_miss_is_excluded_rather_than_averaged_over(self) -> None:
        """The failure design.md names: averaging across a miss drags the centre
        toward whatever filled it. Here the gap is where the subject moved most,
        and the smoothed series must reflect only the samples that saw him.
        """
        with_gap = desired_centres(
            (_hit(0.0, 500.0), _miss(0.25), _miss(0.5), _hit(0.75, 500.0))
        )

        smoothed = smooth_centres(with_gap, window_s=1.0)

        assert all(c.x == pytest.approx(500.0) for c in smoothed)

    def test_times_are_preserved_exactly(self) -> None:
        """Smoothing moves positions, never moments. A shifted timestamp would
        put the crop in the right place at the wrong second."""
        centres = desired_centres(_jitter(8, around=1000.0, amplitude=50.0))

        smoothed = smooth_centres(centres, window_s=1.0)

        assert [c.at_s for c in smoothed] == [c.at_s for c in centres]

    def test_an_empty_series_smooths_to_nothing(self) -> None:
        assert smooth_centres((), window_s=1.0) == ()

    def test_a_single_centre_is_its_own_average(self) -> None:
        assert smooth_centres((Centre(0.0, 900.0),), window_s=1.0) == (
            Centre(0.0, 900.0),
        )


class TestStageFourDeadZone:
    def test_movement_within_tolerance_does_not_move_the_crop(self) -> None:
        """The spec scenario. Displacement under `dead_zone_fraction * width`
        leaves the committed centre exactly where it was."""
        centres = (
            Centre(0.0, 900.0),
            Centre(0.25, 900.0 + DEAD_ZONE_PX - 1.0),
            Centre(0.5, 900.0 - DEAD_ZONE_PX + 1.0),
        )

        committed = apply_dead_zone(centres, frame=FRAME, policy=POLICY)

        assert [c.x for c in committed] == [900.0, 900.0, 900.0]

    def test_movement_beyond_tolerance_moves_the_crop(self) -> None:
        """The other spec scenario. A subject who genuinely walked must be
        followed."""
        centres = (Centre(0.0, 900.0), Centre(0.25, 900.0 + DEAD_ZONE_PX + 1.0))

        committed = apply_dead_zone(centres, frame=FRAME, policy=POLICY)

        assert committed[1].x == pytest.approx(900.0 + DEAD_ZONE_PX + 1.0)

    def test_it_commits_to_the_target_not_to_the_threshold(self) -> None:
        """Moving by the dead-zone instead of to the desired centre would leave
        the crop permanently trailing the subject by one dead-zone width — a
        stabiliser that is always slightly wrong in the direction of travel."""
        centres = (Centre(0.0, 900.0), Centre(0.25, 1500.0))

        committed = apply_dead_zone(centres, frame=FRAME, policy=POLICY)

        assert committed[1].x == pytest.approx(1500.0)

    def test_the_threshold_is_strict(self) -> None:
        """Displacement *exactly* at the dead-zone holds. Design.md writes
        `> dead_zone`, and the boundary has to land somewhere — pinned so a
        later reader cannot flip it while believing it made no difference.

        Held at zero on purpose. Any other base makes `(base + t) - base` differ
        from `t` in binary floating point, which puts the case just under the
        threshold and stops it testing the boundary — the first version of this
        test did exactly that, and a `>=` mutation survived it.
        """
        centres = (Centre(0.0, 0.0), Centre(0.25, DEAD_ZONE_PX))

        committed = apply_dead_zone(centres, frame=FRAME, policy=POLICY)

        assert committed[1].x == 0.0

    def test_the_hold_is_measured_from_the_last_commit_not_the_last_sample(
        self,
    ) -> None:
        """Forward hysteresis, which is what stops a slow drift from being
        followed one sub-threshold step at a time. Each step here is under the
        threshold; their sum is well over it, and the crop still must not move.
        """
        step = DEAD_ZONE_PX / 4
        centres = tuple(Centre(i / 4.0, 900.0 + i * step) for i in range(4))

        committed = apply_dead_zone(centres, frame=FRAME, policy=POLICY)

        assert [c.x for c in committed] == [900.0, 900.0, 900.0, 900.0]

    def test_a_drift_that_accumulates_past_the_threshold_eventually_commits(
        self,
    ) -> None:
        """The same hysteresis from the other side: holding is not refusing.
        Once the accumulated distance from the *held* position clears the
        threshold, the crop catches up in one move."""
        step = DEAD_ZONE_PX / 4
        centres = tuple(Centre(i / 4.0, 900.0 + i * step) for i in range(8))

        committed = apply_dead_zone(centres, frame=FRAME, policy=POLICY)

        assert committed[-1].x > 900.0

    def test_times_are_preserved(self) -> None:
        centres = tuple(Centre(i / 4.0, 900.0 + i * 200.0) for i in range(4))

        committed = apply_dead_zone(centres, frame=FRAME, policy=POLICY)

        assert [c.at_s for c in committed] == [c.at_s for c in centres]

    def test_an_empty_series_commits_nothing(self) -> None:
        assert apply_dead_zone((), frame=FRAME, policy=POLICY) == ()

    def test_the_first_centre_is_always_committed(self) -> None:
        """There is nothing to hold against yet, and starting from a centred
        default would put the first keyframe somewhere no detection asked for."""
        committed = apply_dead_zone((Centre(0.0, 42.0),), frame=FRAME, policy=POLICY)

        assert committed[0].x == 42.0


class TestTheStageOrderIsLoadBearing:
    """Design.md's claim, run rather than quoted.

    "Raw jitter trips a dead-zone repeatedly; smoothed drift trips it once.
    Reversing stages 3 and 4 produces a crop that twitches at exactly the
    threshold."
    """

    def _jittering_just_past_the_threshold(self) -> tuple[Centre, ...]:
        # Peak-to-peak is 2 x amplitude, so an amplitude just over half the
        # dead-zone makes consecutive raw samples clear it and nothing else.
        return desired_centres(_jitter(12, around=1000.0, amplitude=DEAD_ZONE_PX * 0.6))

    def test_smoothing_first_leaves_the_crop_still(self) -> None:
        centres = self._jittering_just_past_the_threshold()

        committed = apply_dead_zone(
            smooth_centres(centres, window_s=POLICY.smoothing_window_s),
            frame=FRAME,
            policy=POLICY,
        )

        assert len({c.x for c in committed}) == 1

    def test_the_reverse_order_twitches_on_every_oscillation(self) -> None:
        """Composed here rather than in production, because the wrong order is
        not something this module should be able to do — only something a test
        needs to be able to demonstrate."""
        centres = self._jittering_just_past_the_threshold()

        twitchy = smooth_centres(
            apply_dead_zone(centres, frame=FRAME, policy=POLICY),
            window_s=POLICY.smoothing_window_s,
        )

        assert len({round(c.x, 6) for c in twitchy}) > 1
