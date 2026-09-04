"""The vocabulary of reframing a wide shot into a vertical clip.

Six entities and one rounding operator. The operator is the interesting part:
`even()` decides the crop size, and its *direction* is what makes the clamp two
stages later well-formed. Rounding down is not a style choice — rounding up on an
odd frame width returns `frame.width + 1`, which makes `frame.width - crop_w`
negative and inverts the clamp into `min(max(x, 0), -1) == -1`, a crop rect
starting outside the frame. That is precisely what *Clamping to Frame Edges*
exists to prevent, so the guarantee is established here, at the source, rather
than repaired downstream.

The postcondition those later stages depend on is `crop_w <= frame.width` and
`crop_h <= frame.height`, both even and **non-negative** — and non-negative is
the exact word. `even(v) == 0` for every `v` below 2, so a one-pixel-tall frame
yields a `(0, 0)` crop. That is the correct output for a degenerate frame: a
1-pixel-tall picture has no 9:16 crop, and no amount of rounding invents one. It
is refused at the render-worker boundary, never repaired in the arithmetic, so
this module pins the property as written rather than quietly strengthening it to
positivity.

`CropTrajectory` carries the domain's first `__post_init__` invariant, and it
guards a fact the pipeline relies on everywhere: the crop *size* is computed once
for the whole clip. Only `x` and `y` are ever commanded downstream. A trajectory
whose keyframes disagreed about width or height would make "native or upscaled"
vary within a single clip and therefore undeclarable as one fact.
"""

import dataclasses
import math

import pytest

from onevoicecut.domain.framing import (
    CropKeyframe,
    CropRect,
    CropTrajectory,
    KeyframeOrigin,
    TimeSpan,
    TrackingConfidence,
    TrajectoryPolicy,
    crop_size_for,
    even,
)
from onevoicecut.domain.media import FrameSize

POLICY = TrajectoryPolicy()


def _rect(x: int = 0, y: int = 0, width: int = 606, height: int = 1080) -> CropRect:
    return CropRect(x=x, y=y, width=width, height=height)


def _keyframe(
    at_s: float = 0.0,
    rect: CropRect | None = None,
    origin: KeyframeOrigin = KeyframeOrigin.TRACKED,
) -> CropKeyframe:
    return CropKeyframe(at_s=at_s, rect=rect if rect is not None else _rect(), origin=origin)


class TestEveryEntityIsFrozen:
    """Consistent with every other domain entity, and stated per type rather
    than assumed from the decorator: a `slots=True` dataclass that lost
    `frozen=True` would still look right at a glance."""

    @pytest.mark.parametrize(
        ("entity", "field", "value"),
        [
            (TimeSpan(start_s=0.0, end_s=1.0), "start_s", 2.0),
            (_rect(), "x", 5),
            (_keyframe(), "at_s", 5.0),
            (TrajectoryPolicy(), "aspect_w", 4),
        ],
    )
    def test_mutation_is_refused(
        self, entity: object, field: str, value: object
    ) -> None:
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(entity, field, value)

    def test_a_trajectory_is_frozen_too(self) -> None:
        trajectory = CropTrajectory(
            keyframes=(_keyframe(),), tracking=TrackingConfidence.WELL_TRACKED
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            trajectory.keyframes = ()  # type: ignore[misc]

    @pytest.mark.parametrize(
        "entity",
        [TimeSpan(start_s=0.0, end_s=1.0), _rect(), _keyframe(), TrajectoryPolicy()],
    )
    def test_they_are_slotted(self, entity: object) -> None:
        """A trajectory over a three-hour service is thousands of keyframes."""
        assert not hasattr(entity, "__dict__")


class TestAKeyframeCarriesItsProvenance:
    def test_it_exposes_a_timestamp_a_rect_and_one_origin(self) -> None:
        """The third no-silent-degradation axis, alongside diarization and
        non-speech classification. A keyframe that could not say where its
        position came from would let a centred fallback read as a real
        detection."""
        keyframe = _keyframe(at_s=1.5, rect=_rect(x=12), origin=KeyframeOrigin.INTERPOLATED)

        assert keyframe.at_s == 1.5
        assert keyframe.rect.x == 12
        assert keyframe.origin is KeyframeOrigin.INTERPOLATED

    def test_there_are_exactly_three_origins(self) -> None:
        assert {member.value for member in KeyframeOrigin} == {
            "tracked",
            "interpolated",
            "fallback_center",
        }

    def test_an_origin_is_never_two_things_at_once(self) -> None:
        """`origin` is a single member rather than a set of flags, which is what
        makes "exactly one of the three" true by construction rather than by
        convention."""
        assert all(isinstance(_keyframe(origin=o).origin, KeyframeOrigin) for o in KeyframeOrigin)


class TestTheTrajectoryInvariant:
    def test_keyframes_disagreeing_on_width_are_refused(self) -> None:
        """The crop size is computed once for the whole clip and only `x`/`y`
        are commanded downstream. Keyframes disagreeing about size would make
        "native or upscaled" vary *within* one clip, and therefore stop being
        one declarable fact."""
        with pytest.raises(ValueError):
            CropTrajectory(
                keyframes=(_keyframe(rect=_rect(width=606)), _keyframe(rect=_rect(width=608))),
                tracking=TrackingConfidence.WELL_TRACKED,
            )

    def test_keyframes_disagreeing_on_height_are_refused(self) -> None:
        with pytest.raises(ValueError):
            CropTrajectory(
                keyframes=(_keyframe(rect=_rect(height=1080)), _keyframe(rect=_rect(height=1082))),
                tracking=TrackingConfidence.WELL_TRACKED,
            )

    def test_the_refusal_names_the_sizes_that_disagreed(self) -> None:
        """A trajectory is built from hundreds of keyframes. "Sizes differ" says
        nothing an implementer can act on."""
        with pytest.raises(ValueError) as refusal:
            CropTrajectory(
                keyframes=(_keyframe(rect=_rect(width=606)), _keyframe(rect=_rect(width=608))),
                tracking=TrackingConfidence.WELL_TRACKED,
            )

        assert "606" in str(refusal.value) and "608" in str(refusal.value)

    def test_moving_the_crop_is_exactly_what_a_trajectory_is_for(self) -> None:
        """Position varies, size does not. The invariant must not be so eager
        that it forbids the movement the type exists to express."""
        trajectory = CropTrajectory(
            keyframes=(_keyframe(0.0, _rect(x=0)), _keyframe(1.0, _rect(x=400))),
            tracking=TrackingConfidence.WELL_TRACKED,
        )

        assert [k.rect.x for k in trajectory.keyframes] == [0, 400]

    def test_an_empty_trajectory_is_allowed(self) -> None:
        """There is nothing to disagree about. Refusing here would make the
        invariant reject a legitimately empty span rather than an inconsistent
        one."""
        assert CropTrajectory(
            keyframes=(), tracking=TrackingConfidence.LOW_CONFIDENCE
        ).keyframes == ()

    def test_confidence_is_carried_on_the_trajectory(self) -> None:
        """A field rather than a derivation at render time, because the spec
        requires it to be available *before* rendering — the alternative is a
        report observable only once the file exists."""
        assert CropTrajectory(
            keyframes=(_keyframe(),), tracking=TrackingConfidence.LOW_CONFIDENCE
        ).tracking is TrackingConfidence.LOW_CONFIDENCE


class TestEven:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0, 0), (1, 0), (2, 2), (3, 2), (1215.0, 1214), (607.5, 606), (2160, 2160)],
    )
    def test_it_rounds_down_to_the_nearest_even_integer(
        self, value: float, expected: int
    ) -> None:
        assert even(value) == expected

    def test_there_is_no_tie_case(self) -> None:
        """`floor` is total, so no value sits between two candidates. That is
        why the direction can be stated once and never re-specified."""
        assert all(even(v / 2) == 2 * math.floor(v / 4) for v in range(0, 40))

    def test_it_never_returns_more_than_it_was_given(self) -> None:
        """The property the whole clamp rests on. Rounding *up* would return
        `frame.width + 1` on an odd width, invert stage 5's clamp, and produce a
        crop rect starting outside the frame."""
        assert all(even(v) <= v for v in range(0, 200))

    def test_it_is_monotone(self) -> None:
        results = [even(v) for v in range(0, 200)]
        assert results == sorted(results)


class TestCropSizeFor:
    def test_four_k_is_the_first_authoritative_pair(self) -> None:
        """`crop_h = even(2160) = 2160`, `crop_w = even(2160 * 9/16) = even(1215.0) = 1214`."""
        assert crop_size_for(FrameSize(3840, 2160), POLICY) == (1214, 2160)

    def test_ten_eighty_p_is_the_second(self) -> None:
        """`crop_h = even(1080) = 1080`, `crop_w = even(1080 * 9/16) = even(607.5) = 606`.

        The `.5` is why the direction matters: rounding up gives 608, which is
        what an earlier revision of the proposal carried and had to correct.
        """
        assert crop_size_for(FrameSize(1920, 1080), POLICY) == (606, 1080)

    def test_a_frame_already_nine_sixteen_is_taken_whole(self) -> None:
        assert crop_size_for(FrameSize(1080, 1920), POLICY) == (1080, 1920)

    def test_a_frame_narrower_than_nine_sixteen_swaps_the_derivation_axis(self) -> None:
        """Width becomes the constrained dimension and height is derived from
        it. Deriving from height in this branch would produce a crop wider than
        the frame — the postcondition's failure mode."""
        assert crop_size_for(FrameSize(500, 1920), POLICY) == (500, 888)

    @pytest.mark.parametrize("width", [1, 2, 3, 499, 500, 1919, 1920, 3839, 3840])
    @pytest.mark.parametrize("height", [1, 2, 3, 1079, 1080, 2159, 2160])
    def test_the_postcondition_holds_over_odd_dimensions_too(
        self, width: int, height: int
    ) -> None:
        """The property stages 5 and 6 depend on, asserted over odd values on
        both axes rather than only at the two worked examples.

        **Non-negative, not positive** — see the degenerate case below.
        """
        crop_w, crop_h = crop_size_for(FrameSize(width, height), POLICY)

        assert crop_w <= width and crop_h <= height
        assert crop_w >= 0 and crop_h >= 0
        assert crop_w % 2 == 0 and crop_h % 2 == 0

    def test_a_degenerate_frame_yields_a_degenerate_crop(self) -> None:
        """`even(1) == 0`, so a one-pixel-tall frame has no 9:16 crop and the
        arithmetic says so instead of inventing one. Refused at the render-worker
        boundary, never repaired here — a repair would need a different minimum
        per branch, and a caller comparing against the wrong one is exactly the
        error being guarded against."""
        assert crop_size_for(FrameSize(1920, 1), POLICY) == (0, 0)

    def test_it_does_not_clamp(self) -> None:
        """No clamping and no re-evening step: the derivation's postcondition
        already puts both dimensions inside the frame, and a re-evening step can
        push a clamped width back above it."""
        crop_w, crop_h = crop_size_for(FrameSize(3841, 2161), POLICY)

        assert (crop_w, crop_h) == (1214, 2160)


class TestTrajectoryPolicy:
    def test_the_documented_defaults_are_the_ones_in_the_design(self) -> None:
        """Pinned because every one of them changes what a viewer sees, and a
        silent change to any would be invisible in the artifact."""
        policy = TrajectoryPolicy()

        assert (policy.aspect_w, policy.aspect_h) == (9, 16)
        assert policy.smoothing_window_s == 1.0
        assert policy.dead_zone_fraction == 0.04
        assert policy.max_gap_s == 1.5
        assert policy.max_fallback_ratio == 0.5
        assert policy.punch_in == 1.0


class TestTimeSpan:
    def test_it_carries_a_start_and_an_end(self) -> None:
        span = TimeSpan(start_s=12.0, end_s=42.0)

        assert (span.start_s, span.end_s) == (12.0, 42.0)

    def test_its_duration_is_derived_rather_than_stored(self) -> None:
        """Two representations of the same fact drift. The detector seeks with
        the pair and the renderer cuts with it; a third stored number would be
        one more thing to keep in step."""
        assert TimeSpan(start_s=12.0, end_s=42.0).duration_s == 30.0
