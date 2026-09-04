"""Turn a clip's range, trajectory and cues into a vertical file. Nothing else.

The port is deliberately thin, and the spec says why: it "MUST know nothing
about why the trajectory says what it says". Smoothing, dead-zone and fallback
decisions were made by a use case that could be proven with no ffmpeg and no
vision weights, and a renderer free to second-guess them would put that
arithmetic back behind an `integration` marker.

**The four declarations are not here.** Quality, caption coverage, subtitle
timing source and tracking confidence are all known *before* this port is
called — from the frame and the target, from the segments' `SegmentKind`, from
whether those segments carried words, from the trajectory. The use case
assembles them into a `RenderedClip`. An adapter asked to report them would be
an adapter capable of lying about arithmetic it never ran.

**A request carries only source-derived material, and that is structural.**
"Every frame and every word appearing in a rendered clip MUST originate from the
source sermon media" is the binding non-goal of this whole project. A field able
to hold an image, a URL or a blob would make it a matter of discipline instead,
so there is none — and a test parses this module to keep it that way, because an
absence cannot be proven by calling something.

`span` is source-absolute because the renderer seeks in the source file; the cues
inside it are clip-local. That split is not an inconsistency, it is mechanical:
`-ss` placed before `-i` resets output timestamps to zero, so everything measured
against the produced file starts there.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from onevoicecut.domain.framing import CropTrajectory, TimeSpan
from onevoicecut.domain.media import SourceMedia
from onevoicecut.domain.rendering import OutputSpec, SubtitleCue
from onevoicecut.ports.capabilities import RenderCapabilities


@dataclass(frozen=True, slots=True)
class RenderRequest:
    """Everything a render needs, and provably nothing more.

    The spec's own list — a source media reference, a time range, a
    `CropTrajectory`, and subtitle cues — plus the target to deliver into. Each
    of the four is derived from the same source sermon: the trajectory was
    computed from its frames, the cues from its transcript.
    """

    media: SourceMedia
    span: TimeSpan  # SOURCE-ABSOLUTE; the cues inside it are clip-local
    trajectory: CropTrajectory  # opaque geometry; never recomputed here
    cues: tuple[SubtitleCue, ...]
    output: OutputSpec


@dataclass(frozen=True, slots=True)
class RenderedFile:
    """What the produced file measurably is.

    Only facts an adapter can observe about its own output. Everything an
    operator reads about the clip's *quality* is assembled above the port, which
    is what stops a renderer from declaring a value it did not compute.
    """

    path: Path
    width: int
    height: int
    duration_s: float


class VideoRenderPort(Protocol):
    def capabilities(self) -> RenderCapabilities: ...

    def render(self, request: RenderRequest, dest: Path) -> RenderedFile:
        """INVARIANT: one ffmpeg process; no raw frames cross a process boundary.

        Crop, reframe and subtitle burn-in are applied within a single native
        invocation — the same constraint `AudioExtractorPort` already carries,
        applied to rendering. Piping decoded frames between processes would cost
        more than the render and lose the timestamps the cues are placed against.

        Only `request.span` is cut. A renderer that decoded the whole source to
        reach minute ninety would make the cost of a clip depend on the length of
        the sermon it came from.

        Raises RenderFailed, ClipRangeInvalid.
        """
        ...
