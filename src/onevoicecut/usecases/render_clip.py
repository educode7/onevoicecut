"""The guarded way into `VideoRenderPort`. Nothing calls the port directly.

Two facts about rendering make this layer necessary rather than decorative.
A render is the one operation in this system whose cost is chosen by whoever
names the range, and ffmpeg will begin work on any range at all -- it seeks,
allocates and finds out late, having spent the machine on a clip nobody can use.
So the range is checked against the source's own probe and against a configured
ceiling **before a process exists**, which is the only moment a refusal costs
nothing.

**`ClipRangeInvalid`, never `RenderFailed`.** The two types exist so a caller
decides "retry or refuse" without reading a message, and every condition here
fails identically on every retry: a span past the end of the source is still past
it on the third attempt.

**The span is refused, never trimmed.** Cutting an over-long candidate to fit
removes either its setup or its payoff, and which one is a judgement about the
material no rule at this layer is positioned to make. The spec says the same
thing about a profile's duration ceiling one layer up.

**Where the ceiling comes from is the caller's business.** It is a parameter,
defaulting to the value design.md fixes, because two different things want to
say it: a render profile carries a destination's own `max_duration_s`, and the
deployment carries a resource bound. `RenderCapabilities.max_clip_seconds` is the
same number seen from the other side -- what a renderer *declares* it will
accept -- and it is declared precisely so this layer, not the adapter, can
enforce it.
"""

from pathlib import Path

from onevoicecut.domain.errors import ClipRangeInvalid
from onevoicecut.domain.media import MediaProbe
from onevoicecut.ports.video_render import (
    RenderedFile,
    RenderRequest,
    VideoRenderPort,
)

# design.md's own figure. Three minutes is already far longer than any
# short-form destination accepts, so a candidate past it is a mistake rather
# than an ambitious clip.
DEFAULT_MAX_CLIP_SECONDS = 180.0


def render_clip(
    request: RenderRequest,
    *,
    renderer: VideoRenderPort,
    probe: MediaProbe,
    dest: Path,
    max_clip_seconds: float = DEFAULT_MAX_CLIP_SECONDS,
) -> RenderedFile:
    """Refuse an impossible or ruinous range, then hand the request on unchanged.

    `0 <= start_s < end_s <= probe.duration_s` and
    `end_s - start_s <= max_clip_seconds`. Both bounds are inclusive on the side
    that names a real value: a ceiling that refused the number it advertises
    would make the configured figure wrong by a second, and the closing appeal
    of a sermon ends exactly at the end of the source.

    The ceiling is not optional. `RenderCapabilities` may declare `None` for
    "this renderer states no bound of its own", but a guard that can be switched
    off is not a guard, and this one is the whole of the render-resource-
    exhaustion answer.
    """
    span = request.span

    if span.start_s < 0:
        raise ClipRangeInvalid(
            f"clip range {span.start_s}s..{span.end_s}s starts before the "
            f"source does"
        )
    if span.end_s <= span.start_s:
        raise ClipRangeInvalid(
            f"clip range {span.start_s}s..{span.end_s}s has no footage in it"
        )
    if span.end_s > probe.duration_s:
        raise ClipRangeInvalid(
            f"clip range {span.start_s}s..{span.end_s}s ends past the end of a "
            f"source {probe.duration_s}s long"
        )
    if span.duration_s > max_clip_seconds:
        raise ClipRangeInvalid(
            f"clip range {span.start_s}s..{span.end_s}s is {span.duration_s}s "
            f"long, past the {max_clip_seconds}s ceiling; a candidate this long "
            f"is refused rather than trimmed, because which end to cut is a "
            f"judgement about the material"
        )

    return renderer.render(request, dest)
