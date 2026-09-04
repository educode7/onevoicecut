"""Reframing a fixed wide shot into a vertical clip: the vocabulary and stage 1.

A sermon is filmed once, wide, and a short-form clip is nine-by-sixteen. Something
has to decide where inside the wide frame the vertical window sits at each moment,
and this module holds the entities that answer plus the one piece of arithmetic
that runs before any detection does.

**The crop size is computed once for the whole clip.** Only `x` and `y` ever move.
That is why `CropTrajectory` carries an invariant rather than a comment: a
trajectory whose keyframes disagreed about width or height would make "native or
upscaled" vary *within* one clip, and it would stop being one declarable fact
about the rendered file.

**`even()` is the load-bearing operator, and its direction is the reason the later
clamp is safe.** Rounding down is not a preference. On an odd frame width, rounding
*up* returns `frame.width + 1`, which makes `frame.width - crop_w` negative and
inverts the clamp into `min(max(x, 0), -1) == -1` — a crop rect starting outside
the frame, which is exactly what clamping exists to prevent. A separate
"re-even after clamping" step has the same defect from the other side. Neither is
needed, because the derivation's own postcondition already puts both dimensions
inside the frame.

**That postcondition is non-negative, not positive.** `even(v) == 0` for every `v`
below 2, so a one-pixel-tall frame yields a `(0, 0)` crop. That is the honest
answer — a 1-pixel-tall picture has no 9:16 crop and no rounding invents one — and
it is refused at the render-worker boundary rather than repaired here. Repairing
it would need a different minimum frame size per branch, and a caller comparing
against the wrong one is precisely the arithmetic error being guarded against.

**Provenance is the third no-silent-degradation axis**, after diarization and
non-speech classification. Every keyframe reports where its position actually came
from, so a centred fallback can never be delivered as a real detection — which is
what stops a clip framed on an empty pulpit from reading as a successful render.
"""

import math
from dataclasses import dataclass
from enum import StrEnum

from onevoicecut.domain.media import FrameSize


class KeyframeOrigin(StrEnum):
    """Where a keyframe's position actually came from.

    A single member rather than a set of flags, which is what makes "exactly one
    of the three" true by construction rather than by convention.
    """

    TRACKED = "tracked"  # a detector found the subject at this moment
    INTERPOLATED = "interpolated"  # bridged between two tracked keyframes
    FALLBACK_CENTER = "fallback_center"  # nothing to track from; centred instead


class TrackingConfidence(StrEnum):
    """Whether a finished trajectory followed a subject or mostly guessed.

    `INTERPOLATED` counts as well tracked: it is bridged *between two real
    detections*, so the subject was found on both sides. Only `FALLBACK_CENTER`
    means nobody was located, which is why the ratio is computed over that
    origin alone.
    """

    WELL_TRACKED = "well_tracked"
    LOW_CONFIDENCE = "low_confidence"


@dataclass(frozen=True, slots=True)
class TimeSpan:
    """A range of the source, in seconds.

    Used source-absolute when asking a detector to seek, and clip-local
    everywhere after — the same pairing `AudioExtractorPort.slice` and
    `TranscriptionPort.transcribe` already ship.
    """

    start_s: float
    end_s: float

    def __post_init__(self) -> None:
        """A span that ends before it starts is not a short span, it is nonsense.

        Found reviewing 12a-i rather than reached by this module's own scope. A
        reversed pair yields a negative `duration_s`, and every consumer treats
        that quantity as a length: it scales interpolation, it decides a sample
        count, and slice 13's renderer will cut with it. The precedent is
        already here — `plan_chunks` raises on a non-positive duration to
        protect its own division, and `CropTrajectory` validates its invariant
        the same way.

        Zero length stays legal. A request to look at nothing is answerable;
        a request to look backwards is not.
        """
        if self.end_s < self.start_s:
            raise ValueError(
                f"a time span cannot end before it starts: "
                f"{self.start_s}s to {self.end_s}s"
            )

    @property
    def duration_s(self) -> float:
        """Derived, never stored. Two representations of one fact drift, and
        this one would have to stay in step with a pair that is already the
        thing the detector seeks with and the renderer cuts with."""
        return self.end_s - self.start_s


@dataclass(frozen=True, slots=True)
class CropRect:
    """A window into the source frame, in pixels.

    `width` and `height` are constant across a whole trajectory — see
    `CropTrajectory`. Only `x` and `y` are ever commanded downstream.
    """

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class CropKeyframe:
    at_s: float
    rect: CropRect
    origin: KeyframeOrigin


@dataclass(frozen=True, slots=True)
class CropTrajectory:
    """Where the vertical window sits, over the life of one clip.

    `tracking` is a field rather than something derived at render time because
    the spec requires it *before* rendering. Deriving it later would make a
    mostly-guessed reframe observable only once the file already exists.
    """

    keyframes: tuple[CropKeyframe, ...]
    tracking: TrackingConfidence

    def __post_init__(self) -> None:
        """The crop size is decided once per clip, and this holds it there.

        Downstream only commands `x` and `y`, so a keyframe carrying a different
        `width` or `height` would be silently ignored rather than rejected — and
        the output quality declaration, which divides the target width by the
        crop width, would describe a size that applied to only part of the clip.
        """
        sizes = {(frame.rect.width, frame.rect.height) for frame in self.keyframes}
        if len(sizes) > 1:
            raise ValueError(
                f"a trajectory's crop size is fixed for the whole clip, but its "
                f"keyframes carry {sorted(sizes)}; only x and y may vary"
            )


@dataclass(frozen=True, slots=True)
class TrajectoryPolicy:
    """The knobs, defaulted to the values design.md fixes.

    Every one of them changes what a viewer sees, so each is named here rather
    than spelled inline at a call site where a silent change would be invisible.
    """

    aspect_w: int = 9
    aspect_h: int = 16
    smoothing_window_s: float = 1.0
    # Of frame width. Below this, the subject moved but the crop should not.
    dead_zone_fraction: float = 0.04
    # Longer runs of no-detection are filled with a centred crop rather than
    # bridged, because a bridge over a long gap is a guess wearing a real
    # keyframe's origin.
    max_gap_s: float = 1.5
    # "Predominantly", per the spec's own wording.
    max_fallback_ratio: float = 0.5
    # 1.0 is no punch-in. The factor is unmeasured, so the default does nothing.
    punch_in: float = 1.0


def even(value: float) -> int:
    """Round **down** to the nearest even integer. Total, with no tie case.

    Defined once and never restated informally, because the whole subsystem's
    clamp safety rests on the direction. `floor` is total, so no value sits
    between two candidates and there is no tie behaviour to specify.
    """
    return 2 * math.floor(value / 2)


def crop_size_for(frame: FrameSize, policy: TrajectoryPolicy) -> tuple[int, int]:
    """The one crop size for a whole clip — pipeline stage 1.

    Even dimensions because H.264 requires them. No clamping and no re-evening
    step: `even(v) <= v` and `even` is monotone, so in the first branch
    `crop_h <= frame.height` directly and
    `crop_w <= crop_h * 9/16 <= frame.height * 9/16 <= frame.width` by the branch
    condition — the second branch is the same argument with the axes swapped.
    Both differences `frame.width - crop_w` and `frame.height - crop_h` are
    therefore non-negative, which is what makes stage 5's clamp well-formed.

    Total by construction: a degenerate frame gets a degenerate crop rather than
    an exception. See the module docstring for why that refusal belongs at the
    boundary instead.
    """
    if frame.width * policy.aspect_h >= frame.height * policy.aspect_w:
        # The frame is 9:16 or wider, so height is the constrained dimension.
        crop_h = even(frame.height)
        crop_w = even(crop_h * policy.aspect_w / policy.aspect_h)
    else:
        # Narrower than 9:16: width constrains, and deriving from height instead
        # would produce a crop wider than the frame.
        crop_w = even(frame.width)
        crop_h = even(crop_w * policy.aspect_h / policy.aspect_w)
    return crop_w, crop_h
