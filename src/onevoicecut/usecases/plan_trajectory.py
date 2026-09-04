"""Turning a detection series into a stable crop path. Stages 2 to 4.

Stage 1 — the one crop size for the whole clip — is `crop_size_for` in
`domain/framing.py`. Stages 5 and 6 — clamping, and filling the runs nobody was
detected in — are slice 12b-ii. What lives here is the part that decides *where
the window wants to be* at every moment a subject was actually found.

**Pure arithmetic over a detection series, and that is a spec requirement rather
than a convenience.** Nothing in this module imports a detector or knows one
exists; it takes the tuple a `SubjectTrackerPort` returned and computes over it.
That is what lets smoothing and the dead-zone be proven in the default suite with
no vision weights loaded.

**The stage order is load-bearing, and it only runs one way.** Smoothing comes
before the dead-zone. Raw jitter trips a dead-zone on every oscillation, so the
stabilised crop shakes *more* than the unstabilised one would; smoothed drift
trips it once. The functions are separate and the composition is the caller's, so
nothing here can be run in the wrong order — but a test composes them backwards
to show what that would cost, because the claim was prose in design.md and is now
executable.

**Misses are absent, never filled.** A miss carries no desired centre and simply
does not appear in stages 3 and 4. Averaging across a placeholder would drag the
smoothed centre toward whatever the placeholder was, which is how a "smoothed"
trajectory walks off the subject slowly enough to read as camera movement. Runs of
misses become keyframes in stage 6, from the outside, where the choice between
bridging and falling back to centre can be made with both endpoints in view.

Only the horizontal axis moves here. For a landscape source the crop is full
height, so `y` has nowhere to go; where it does — a source narrower than 9:16 —
it is settled in 12b-ii alongside the clamp, which is the stage that owns
in-frame positioning on both axes.
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace

from onevoicecut.domain.framing import (
    CropKeyframe,
    CropRect,
    CropTrajectory,
    KeyframeOrigin,
    TimeSpan,
    TrackingConfidence,
    TrajectoryPolicy,
    crop_size_for,
)
from onevoicecut.domain.media import FrameSize
from onevoicecut.ports.subject_tracker import SubjectDetection


@dataclass(frozen=True, slots=True)
class Centre:
    """Where the crop window wants to be centred, at one moment.

    Carries a float `x` because it is an intermediate: three stages refine it
    before stage 5 turns it into an integer `CropRect`. Rounding at each step
    would accumulate a bias in the direction of the rounding, and this axis is
    the one a viewer sees.

    Not a domain entity. It has no life outside this pipeline, and promoting it
    would put a stage's working value into the vocabulary every other module
    reads.
    """

    at_s: float
    x: float


def desired_centres(
    detections: tuple[SubjectDetection, ...],
) -> tuple[Centre, ...]:
    """Stage 2. Each hit's box becomes the centre the crop wants.

    Misses are dropped rather than placeheld. A placeholder would be
    indistinguishable from a real detection by the time it reached smoothing,
    and the whole point of `box is None` being explicit at the port is that this
    distinction survives.
    """
    return tuple(
        Centre(at_s=detection.at_s, x=detection.box.x + detection.box.width / 2)
        for detection in detections
        if detection.box is not None
    )


def smooth_centres(centres: tuple[Centre, ...], *, window_s: float) -> tuple[Centre, ...]:
    """Stage 3. Centred moving average over the tracked subsequence.

    **Centred, not trailing.** A trailing average lags the subject by half a
    window — a third of a second at the default rate — which reads as a camera
    operator reacting late rather than as stabilisation.

    **Over the tracked subsequence only**, which is already what the input is:
    misses never became centres, so a window spanning a gap simply averages the
    real samples on either side of it. Nothing is dragged toward a fill value,
    because there is no fill value to be dragged toward.

    The window is measured in *time*, not in samples, so the result does not
    change when a caller samples more finely. Times are never moved — only
    positions — because a shifted timestamp puts the crop in the right place at
    the wrong second.
    """
    half = window_s / 2
    return tuple(
        replace(
            centre,
            x=_mean(
                other.x
                for other in centres
                if abs(other.at_s - centre.at_s) <= half
            ),
        )
        for centre in centres
    )


def apply_dead_zone(
    centres: tuple[Centre, ...], *, frame: FrameSize, policy: TrajectoryPolicy
) -> tuple[Centre, ...]:
    """Stage 4. Forward hysteresis: hold until the subject has really moved.

    Measured from the **last committed** position, never from the last sample.
    That is what forward hysteresis means here, and it is the difference between
    ignoring jitter and following a slow drift one sub-threshold step at a time —
    a series of steps each under the threshold sums to any distance at all.

    When it does commit, it commits **to the desired centre**, not by the
    threshold. Moving by the dead-zone would leave the crop permanently trailing
    the subject by one dead-zone width: a stabiliser that is always slightly
    wrong, and always in the direction of travel.

    The comparison is strict, so displacement exactly at the threshold holds. The
    boundary has to fall on one side; design.md writes `>` and this follows it
    rather than re-deciding.
    """
    if not centres:
        return ()

    threshold = policy.dead_zone_fraction * frame.width
    held = centres[0].x
    committed: list[Centre] = []

    for centre in centres:
        if abs(centre.x - held) > threshold:
            held = centre.x
        committed.append(replace(centre, x=held))

    return tuple(committed)


def _mean(values: Iterable[float]) -> float:
    """Never called with an empty window: a centre is always inside its own."""
    sample = list(values)
    return sum(sample) / len(sample)


def build_trajectory(
    detections: tuple[SubjectDetection, ...],
    frame: FrameSize,
    span: TimeSpan,
    policy: TrajectoryPolicy,
) -> CropTrajectory:
    """The whole pipeline, stages 1 to 6, for one clip.

    **Every sampled point becomes exactly one keyframe.** A trajectory with fewer
    keyframes than samples has silently decided some moments do not need
    answering, and an unanswered moment has no origin to report - which is the
    one thing the provenance axis forbids.

    Stage 6 does not re-clamp, and that is a proof rather than an omission. The
    frame is convex: both interpolation endpoints are inside it, so every point
    on the segment between them is inside it, and rounding a value already inside
    an integer interval keeps it there. A centred rect is inside by construction.
    Re-clamping would be the same class of error as re-evening a clamped value -
    an operation that looks defensive and can only move a correct answer.

    `span` is accepted because design.md fixes this signature and slice 13 seeks
    with it. Nothing here reads it: detection times are already clip-local, so
    re-offsetting them would be the second translation point the port docstring
    warns against.
    """
    crop_w, crop_h = crop_size_for(frame, policy)
    tracked = _tracked_rects(detections, frame, policy, crop_w, crop_h)
    fallback = _centred(frame, crop_w, crop_h)

    keyframes: list[CropKeyframe] = [
        CropKeyframe(
            at_s=detection.at_s,
            rect=tracked.get(index, fallback),
            origin=KeyframeOrigin.TRACKED,
        )
        for index, detection in enumerate(detections)
    ]

    for start, end in _miss_runs(detections):
        for index in range(start, end):
            keyframes[index] = _fill(
                detections, index, start, end, tracked, fallback, policy
            )

    return CropTrajectory(
        keyframes=tuple(keyframes), tracking=_confidence(tuple(keyframes), policy)
    )


def _tracked_rects(
    detections: tuple[SubjectDetection, ...],
    frame: FrameSize,
    policy: TrajectoryPolicy,
    crop_w: int,
    crop_h: int,
) -> dict[int, CropRect]:
    """Stages 2 to 5 over the hits, keyed by index in the *full* series.

    Keyed rather than listed because stage 6 has to ask "was this sample
    tracked" while walking a series that still contains its misses, and the two
    sequences have different lengths by construction.
    """
    hits = [i for i, detection in enumerate(detections) if detection.box is not None]
    committed = apply_dead_zone(
        smooth_centres(desired_centres(detections), window_s=policy.smoothing_window_s),
        frame=frame,
        policy=policy,
    )
    return {
        index: _rect_at(centre.x, frame, crop_w, crop_h)
        for index, centre in zip(hits, committed)
    }


def _rect_at(centre_x: float, frame: FrameSize, crop_w: int, crop_h: int) -> CropRect:
    """Stage 5. Both axes clamped, last among the position stages.

    The postcondition of `crop_size_for` makes both differences non-negative, so
    `min(max(v, 0), limit)` is well-formed. That is exactly what rounding *down*
    in `even()` buys, and it is why nothing here re-rounds afterwards.

    Vertically there is nothing to track: stages 2 to 4 produce a horizontal
    centre and nothing else. For a landscape source the crop is full height and
    the clamp forces `y` to its only legal value anyway; for a source narrower
    than the target, centring is the one position that does not claim to have
    been derived from evidence.
    """
    return CropRect(
        x=_clamp(round(centre_x - crop_w / 2), frame.width - crop_w),
        y=_clamp((frame.height - crop_h) // 2, frame.height - crop_h),
        width=crop_w,
        height=crop_h,
    )


def _centred(frame: FrameSize, crop_w: int, crop_h: int) -> CropRect:
    """The fallback rect. Inside the frame by construction, never clamped."""
    return CropRect(
        x=(frame.width - crop_w) // 2,
        y=(frame.height - crop_h) // 2,
        width=crop_w,
        height=crop_h,
    )


def _clamp(value: int, limit: int) -> int:
    return min(max(value, 0), limit)


def _miss_runs(
    detections: tuple[SubjectDetection, ...],
) -> tuple[tuple[int, int], ...]:
    """Half-open index ranges of consecutive misses.

    Shared by both fills because the choice between them is a property of the
    *run*, never of a sample inside it: whether it is bounded, and how long it
    is, can only be asked with both ends in view.
    """
    runs: list[tuple[int, int]] = []
    start: int | None = None

    for index, detection in enumerate(detections):
        if detection.box is None:
            start = index if start is None else start
        elif start is not None:
            runs.append((start, index))
            start = None

    if start is not None:
        runs.append((start, len(detections)))
    return tuple(runs)


def _fill(
    detections: tuple[SubjectDetection, ...],
    index: int,
    start: int,
    end: int,
    tracked: dict[int, CropRect],
    fallback: CropRect,
    policy: TrajectoryPolicy,
) -> CropKeyframe:
    """Stage 6. Bridge a gap that has two ends, centre one that does not.

    The gap is measured **between the bounding tracked samples**, not across the
    misses themselves. That is the distance actually being inferred over, and it
    is the larger of the two measures - a run of misses spans one sample interval
    less than the gap containing it, so measuring the run would let a gap
    slightly over the policy bridge anyway.
    """
    before, after = start - 1, end
    bounded = before in tracked and after in tracked

    if bounded and detections[after].at_s - detections[before].at_s <= policy.max_gap_s:
        return CropKeyframe(
            at_s=detections[index].at_s,
            rect=_between(
                tracked[before],
                tracked[after],
                detections[before].at_s,
                detections[after].at_s,
                detections[index].at_s,
            ),
            origin=KeyframeOrigin.INTERPOLATED,
        )

    return CropKeyframe(
        at_s=detections[index].at_s,
        rect=fallback,
        origin=KeyframeOrigin.FALLBACK_CENTER,
    )


def _between(
    first: CropRect, last: CropRect, first_s: float, last_s: float, at_s: float
) -> CropRect:
    """Linear in time between two rects already inside the frame.

    Only `x` moves: `y` is identical at both ends by construction, because
    nothing tracks vertically and the crop size is fixed for the clip.
    """
    fraction = (at_s - first_s) / (last_s - first_s)
    return replace(first, x=round(first.x + (last.x - first.x) * fraction))


def _confidence(
    keyframes: tuple[CropKeyframe, ...], policy: TrajectoryPolicy
) -> TrackingConfidence:
    """Computed once on the finished trajectory, before anything is rendered.

    `INTERPOLATED` counts as tracked: it is bridged between two real detections,
    so the subject was found on both sides. Only `FALLBACK_CENTER` means nobody
    was located, which is why the ratio is taken over that origin alone.

    An empty trajectory is `LOW_CONFIDENCE`. There is no ratio to compute, and
    the safe reading of no evidence is the weaker claim - reporting a trajectory
    with no keyframes as well tracked is the "looks like success" failure this
    axis exists to prevent, in its purest form.
    """
    if not keyframes:
        return TrackingConfidence.LOW_CONFIDENCE

    fallbacks = sum(
        1 for frame in keyframes if frame.origin is KeyframeOrigin.FALLBACK_CENTER
    )
    return (
        TrackingConfidence.LOW_CONFIDENCE
        if fallbacks / len(keyframes) > policy.max_fallback_ratio
        else TrackingConfidence.WELL_TRACKED
    )
