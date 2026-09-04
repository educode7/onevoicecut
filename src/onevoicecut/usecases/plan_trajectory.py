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

from onevoicecut.domain.framing import TrajectoryPolicy
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
