"""`VideoRenderPort` over the ffmpeg binary. The second native process in this app.

Deliberately a sibling of `FfmpegAudioExtractor` rather than a new dialect: the
runner is injected for the same reason, so the invocation contract -- one
process, which flags, which working directory, and that containment is checked
*before* anything is launched -- is proven on a machine with no ffmpeg installed.
Whether ffmpeg then produces a watchable file is a separate claim, and only the
`integration`-marked unit makes it.

**The graph's two sidecars have different authors, and the split is not
arbitrary.** `build_render_argv` keeps every path out of `-filter_complex` and
names `<clip>.cmds` and `<clip>.ass` as bare filenames, returning the directory
they resolve against.

The command file is written here, because `RenderRequest.trajectory` determines
it completely -- nothing outside the request is consulted, so making a caller
write it would invent a filesystem precondition for no gain.

The subtitle document is **not**, because it is not determined by the request at
all. An ASS document needs the destination's caption safe area, and a safe area
lives on a `RenderProfile` that `RenderRequest` deliberately does not carry: the
port takes an `OutputSpec`, which is the geometry alone. A renderer that filled
in a margin of its own would be introducing precisely the inherited-default
failure the safe-area axis exists to refuse -- right for the profile it was
measured against, silently wrong for every other, and visible only after the
clip is published. So the document is prepared by the caller that resolved the
profile, and this adapter refuses to spawn without it rather than rendering a
clip with no captions. When the port grows the profile the spec says it must
accept, the writing moves back in here beside the command file.

**The clip id comes from the destination's stem.** `RenderRequest` carries no
id -- it is source-derived material only -- and the graph needs one, so the file
being written names the clip. `build_render_argv` validates it as a ULID before
composing, which is what keeps an arbitrary stem out of a string ffmpeg parses.
The `.mp4`, the `.cmds` and the `.ass` therefore share a stem, which is the
whole reason the bare filenames find each other.

**What it reports, it commanded.** `RenderedFile` carries the geometry the argv
asked for and the span's own duration, not a measurement: measuring would mean a
second process, and the single-native-pass requirement forbids one. Existence is
checked, because a zero exit that wrote nothing is a failure rather than an empty
clip.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from onevoicecut.adapters.ffmpeg.argv import (
    FFMPEG_BINARY,
    build_render_argv,
)
from onevoicecut.adapters.ffmpeg.process import BinaryInvoker, real_process
from onevoicecut.adapters.ffmpeg.sendcmd import build_sendcmd_script
from onevoicecut.domain.errors import RenderFailed
from onevoicecut.domain.framing import CropTrajectory
from onevoicecut.ports.capabilities import RenderCapabilities, RenderSupport
from onevoicecut.ports.video_render import RenderedFile, RenderRequest

RENDERER_ID = "ffmpeg"

# The floor under the per-render timeout. A one-second clip still pays codec
# startup, so a bound proportional to duration alone would kill the shortest
# renders first.
MIN_RENDER_TIMEOUT_S = 60.0

# Multiples of the clip's own length. Two orders of magnitude tighter than
# extraction's four-hour ceiling, and deliberately: extraction bounds a hung
# process over a multi-hour input, while a clip is minutes. A render still going
# after twenty times the footage it was handed is hung, not slow.
RENDER_TIMEOUT_REALTIME_FACTOR = 20.0


class RenderProcessRunner(Protocol):
    """Declared here rather than in `ports/`, and the reason is the hexagon.

    `ports/` holds contracts the core depends on and imports domain only; this
    one names `subprocess.CompletedProcess`, which would put process spawning
    into the core's vocabulary. `ProcessRunner` in the extractor exists for the
    same reason and is deliberately a separate shape: only a render needs a
    working directory, and widening the shipped one would churn every fake in
    four test modules to carry an argument the extractor never uses.
    """

    def __call__(
        self, argv: list[str], *, cwd: Path, timeout_s: float
    ) -> subprocess.CompletedProcess[str]: ...


def _run(
    argv: list[str], *, cwd: Path, timeout_s: float
) -> subprocess.CompletedProcess[str]:
    """This adapter's runner shape over the shared spawn. The working directory
    is the whole difference: the filter graph names its two files bare."""
    return real_process(argv, cwd=cwd, timeout_s=timeout_s)


class FfmpegVideoRenderer:
    def __init__(
        self,
        job_dir: Path,
        *,
        runner: RenderProcessRunner = _run,
        max_clip_seconds: float | None = None,
    ) -> None:
        self._job_dir = job_dir
        self._runner = runner
        # Declared, never enforced here. `render_clip` refuses an over-long span
        # before this adapter is reached, which is the only place a refusal can
        # happen *before* a process exists; a bound applied inside would be one
        # nobody upstream could read, and slice 13c reads this one.
        self._max_clip_seconds = max_clip_seconds
        self._invoker = BinaryInvoker()

    def capabilities(self) -> RenderCapabilities:
        return RenderCapabilities(
            renderer_id=RENDERER_ID,
            rendering=(
                RenderSupport.AVAILABLE
                if shutil.which(FFMPEG_BINARY) is not None
                else RenderSupport.REQUIRES_SETUP
            ),
            max_clip_seconds=self._max_clip_seconds,
        )

    def render(self, request: RenderRequest, dest: Path) -> RenderedFile:
        clip_id = dest.stem
        invocation = build_render_argv(
            source=request.media.stored_path,
            dest=dest,
            job_dir=self._job_dir,
            clip_id=clip_id,
            span=request.span,
            crop_size=_crop_size(request.trajectory),
            output=request.output,
        )
        destination = Path(invocation.argv[-1])

        _require_subtitle_document(invocation.cwd, clip_id)
        _write_command_file(invocation.cwd, clip_id, request.trajectory)
        destination.parent.mkdir(parents=True, exist_ok=True)

        timeout_s = render_timeout_for(request.span.duration_s)
        self._invoker.invoke(
            invocation.argv,
            spawn=lambda: self._runner(
                invocation.argv, cwd=invocation.cwd, timeout_s=timeout_s
            ),
            timeout_s=timeout_s,
            # Both are `RenderFailed`, unlike extraction's pair: a render that
            # overran and a render that exited non-zero are the same news to a
            # caller, and both are worth another attempt on a quieter machine.
            on_timeout=RenderFailed,
            on_failure=RenderFailed,
        )

        if not destination.exists():
            raise RenderFailed(
                f"ffmpeg reported success but produced no output at {destination}"
            )

        return RenderedFile(
            path=destination,
            width=request.output.width,
            height=request.output.height,
            duration_s=request.span.duration_s,
        )


def _require_subtitle_document(render_dir: Path, clip_id: str) -> None:
    """Refuse before spawning when the caller's `.ass` is not there.

    ffmpeg's own answer to a missing subtitle file is to fail the filter graph
    after it has opened the source and seeked -- a diagnostic about a bare
    filename, with nothing in it about profiles. Checking here costs one `stat`
    and names the actual obligation.

    A `RenderFailed` that is not retryable, which the type says less precisely
    than it should: there is no domain error for "this request was prepared
    wrong", and adding one is a change this unit does not own.
    """
    document = render_dir / f"{clip_id}.ass"
    if not document.is_file():
        raise RenderFailed(
            f"no subtitle document at {document.name}; the caption safe area "
            f"belongs to a render profile the request does not carry, so the "
            f"caller that resolved the profile writes this file before "
            f"rendering, and a clip is never rendered without it"
        )


def _write_command_file(
    render_dir: Path, clip_id: str, trajectory: CropTrajectory
) -> None:
    """The crop path, into the directory the graph resolves against.

    Line feeds regardless of platform: ffmpeg parses this file, not this project,
    and the platform default would put a carriage return inside every `sendcmd`
    line on the only machine this app runs on.
    """
    (render_dir / f"{clip_id}.cmds").write_text(
        build_sendcmd_script(trajectory), encoding="utf-8", newline="\n"
    )


def render_timeout_for(clip_duration_s: float) -> float:
    """The clip's own length decides its bound, floored so a short one survives.

    Public, not `_`-private: `runtime/app.py`'s render drain sweep needs the
    identical formula to derive when a claimed render's own claim has gone
    stale, and duplicating the two constants there would let the drain's
    notion of "too long" silently drift from the timeout that actually kills
    the process.
    """
    return max(MIN_RENDER_TIMEOUT_S, RENDER_TIMEOUT_REALTIME_FACTOR * clip_duration_s)


def _crop_size(trajectory: CropTrajectory) -> tuple[int, int]:
    """The one size the trajectory holds constant, read rather than recomputed.

    `CropTrajectory.__post_init__` already refuses a set of keyframes carrying
    more than one, so the first is the whole clip's. An empty trajectory has
    none at all, and no crop size means no render: it is the caller's error and
    fails identically on retry, which `RenderFailed` says less precisely than it
    should -- there is no third domain error for "this request was malformed",
    and inventing one is a domain change this unit does not own.
    """
    if not trajectory.keyframes:
        raise RenderFailed(
            "a trajectory with no keyframes carries no crop size, so there is "
            "nothing to render; trajectory building always emits at least one"
        )
    first = trajectory.keyframes[0].rect
    return first.width, first.height
