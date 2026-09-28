"""The command file that drives `crop`, densified from detection rate.

Detection is expensive, so a `CropTrajectory` carries one keyframe per detection
sample — four a second by default. `sendcmd` holds a commanded value until the
next command arrives, so feeding it those directly moves the crop in visible
250 ms steps. Smooth motion wants commands at roughly frame rate.

**Densifying the trajectory instead would have destroyed the confidence signal.**
Resampling 4 Hz up to 25 Hz marks about 84% of its keyframes as fills; whatever
origin those fills carried, the tracked ratio stops describing how much of the
clip a detector actually found the subject in, and every trajectory reads
low-confidence. So `CropTrajectory` stays one-to-one with detection samples and
the extra commands are generated here, at the edge, where nothing persists them.

**This module decides nothing, and that is what makes it legitimate.** The spec
forbids the renderer recomputing smoothing, the dead zone or clamping. Linear
interpolation between two already-committed rects introduces no judgement: the
value between them was implied by the pair, and by convexity a point between two
positions inside the frame is itself inside the frame. No policy is imported, no
threshold is read, and a test parses this file to prove it — an absence cannot be
demonstrated by calling something.

A command is a pixel position at a time and carries no provenance. `KeyframeOrigin`
never appears here, because attaching one to an interpolated command is exactly
the confusion the split above exists to prevent.
"""

from onevoicecut.domain.framing import CropKeyframe, CropTrajectory

# "About frame rate", per design.md, which records it as a guess rather than a
# measurement — `MediaProbe` does not report frame rate yet. Named here so a
# change to how every clip moves is a change to one line.
DEFAULT_COMMAND_HZ = 25.0

# `sendcmd`'s own line format: `<time> <filter> <property> '<value>';`. The file
# is parsed by ffmpeg rather than by us, so a drift here fails on a real render.
_LINE = "{at:.3f} crop {prop} '{value}';"


def build_sendcmd_script(
    trajectory: CropTrajectory, *, command_hz: float = DEFAULT_COMMAND_HZ
) -> str:
    """The `sendcmd` script for one clip's crop path.

    Only `x` and `y` are commanded. Stage 1 fixed the crop size for the whole
    clip and `CropTrajectory` enforces it, so commanding `w` or `h` here would
    contradict the invariant the quality declaration rests on.

    Keyframes are sorted before interpolating. An unsorted pair interpolates to
    values *outside* both endpoints, which is how a rect leaves the frame without
    any clamp having been removed — the convexity argument holds only over an
    ordered sequence.
    """
    if command_hz <= 0:
        raise ValueError(
            f"command_hz must be positive, got {command_hz}; zero divides and a "
            f"negative rate steps backwards"
        )

    ordered = sorted(trajectory.keyframes, key=lambda frame: frame.at_s)
    if not ordered:
        # An empty script, not a malformed one: ffmpeg reads this file, so a
        # stray line would fail the render rather than the composition.
        return ""

    lines: list[str] = []
    for at_s, rect_x, rect_y in _positions(ordered, command_hz):
        lines.append(_LINE.format(at=at_s, prop="x", value=rect_x))
        lines.append(_LINE.format(at=at_s, prop="y", value=rect_y))
    return "".join(f"{line}\n" for line in lines)


def _positions(
    ordered: list[CropKeyframe], command_hz: float
) -> list[tuple[float, int, int]]:
    """One position per command tick, from the first keyframe to the last.

    A single keyframe still emits once. Emitting nothing would leave `crop` at
    the `x=0` its argv default carries, framing every clip shorter than one
    detection interval on the left edge of the source.
    """
    first, last = ordered[0].at_s, ordered[-1].at_s
    if last <= first:
        return [(first, ordered[0].rect.x, ordered[0].rect.y)]

    step = 1.0 / command_hz
    ticks = int((last - first) / step)
    at_seconds = [first + index * step for index in range(ticks + 1)]
    # The final keyframe's own time, when the tick grid does not land on it. Its
    # position is committed data; ending early would hold the previous command
    # through the clip's last frames.
    if at_seconds[-1] < last:
        at_seconds.append(last)

    return [(at_s, *_interpolate(ordered, at_s)) for at_s in at_seconds]


def _interpolate(ordered: list[CropKeyframe], at_s: float) -> tuple[int, int]:
    """The position implied by the surrounding pair — no decision of its own.

    Linear, and only linear. Anything smoothed or eased would be this module
    choosing how the crop moves, which is the geometric re-decision the spec
    forbids the renderer from making.
    """
    later = next((i for i, f in enumerate(ordered) if f.at_s > at_s), None)
    if later is None:
        return ordered[-1].rect.x, ordered[-1].rect.y
    if later == 0:
        return ordered[0].rect.x, ordered[0].rect.y

    before, after = ordered[later - 1], ordered[later]
    span = after.at_s - before.at_s
    fraction = (at_s - before.at_s) / span
    return (
        round(before.rect.x + (after.rect.x - before.rect.x) * fraction),
        round(before.rect.y + (after.rect.y - before.rect.y) * fraction),
    )
