"""Command-line composition and path containment for the ffmpeg adapter.

Kept separate from the adapter that spawns processes, and kept pure, so the two
applicable threat-matrix rows — hostile filenames on a command line, and paths
escaping the job directory — are asserted without ffmpeg installed and without
launching anything.

Two rules carry the safety here:

1. **List form, never a shell.** `subprocess.run([...])` passes argv straight to
   the OS. No token is ever split on `;`, `&&`, a backtick or a newline, because
   nothing parses the tokens as a command line.
2. **Absolute resolved paths only.** An absolute path cannot begin with `-`, so
   the positional output file can never be re-read by ffmpeg as an option — the
   one place where a leading-dash filename would otherwise matter.
"""

from dataclasses import dataclass
from pathlib import Path

from onevoicecut.domain.chunking import PlannedChunk
from onevoicecut.domain.errors import ClipRangeInvalid, ExtractionFailed
from onevoicecut.domain.framing import TimeSpan
from onevoicecut.domain.ids import make_clip_id
from onevoicecut.domain.rendering import OutputSpec

FFMPEG_BINARY = "ffmpeg"
FFPROBE_BINARY = "ffprobe"

# The normalization target. `plan_chunks` derives its byte cap from the bitrate
# this produces, so changing it is a planning decision, not a format preference.
SAMPLE_RATE_HZ = 16000
CHANNELS = 1
AUDIO_CODEC = "flac"


def resolve_inside(job_dir: Path, candidate: Path) -> Path:
    """Resolve `candidate` and require it to stay within `job_dir`.

    Resolution happens first so that `..` segments and symlinks are collapsed
    before the check — only where a path actually lands decides, never how it is
    spelled. Neither path needs to exist: extraction resolves its destination
    before ffmpeg has created it.
    """
    root = job_dir.resolve()
    resolved = candidate.resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise ExtractionFailed(
            f"refusing to use {resolved}: outside the job directory {root}"
        )
    return resolved


def build_probe_argv(source: Path) -> list[str]:
    """Inspect a container without decoding it."""
    return [
        FFPROBE_BINARY,
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        "-i",
        str(source.resolve()),
    ]


def _ffmpeg_prefix() -> list[str]:
    """The hardening flags every ffmpeg invocation carries.

    `-nostdin` so a worker process can never block on a terminal read, and
    `-protocol_whitelist file` so a crafted container cannot make ffmpeg fetch
    http or concat inputs of its own.
    """
    return [
        FFMPEG_BINARY,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file",
    ]


def _audio_encoding() -> list[str]:
    """The normalization target, shared by extraction and slicing so a chunk can
    never end up in a different format from the track it came from."""
    return ["-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE_HZ), "-c:a", AUDIO_CODEC]


def _seconds(value: float) -> str:
    """Millisecond precision — finer than any boundary the planner produces."""
    return f"{value:.3f}"


def build_extract_argv(source: Path, dest: Path) -> list[str]:
    """Video container in, normalized 16 kHz mono FLAC out."""
    return [
        *_ffmpeg_prefix(),
        "-i",
        str(source.resolve()),
        "-vn",  # drop video
        "-map",
        "0:a:0",  # first audio stream only
        *_audio_encoding(),
        "-y",  # the destination is server-generated, so overwriting is intended
        str(dest.resolve()),
    ]


def build_slice_argv(track: Path, planned: PlannedChunk, dest: Path) -> list[str]:
    """Cut one planned chunk out of the normalized track.

    `-ss` goes **before** `-i` so ffmpeg seeks the container instead of decoding
    everything up to the start point. On a three-hour track, reaching chunk 15 by
    decoding would cost more than transcribing it.

    Re-encoded rather than stream-copied: a copy would land each cut on the
    nearest frame boundary, and the stitcher derives its contested window from the
    plan, so drifted boundaries would desynchronise the two.
    """
    return [
        *_ffmpeg_prefix(),
        "-ss",
        _seconds(planned.start_s),
        "-i",
        str(track.resolve()),
        "-t",
        _seconds(planned.end_s - planned.start_s),
        *_audio_encoding(),
        "-y",
        str(dest.resolve()),
    ]


# The subdirectory a clip's own files live in, and the working directory the
# filter graph resolves its bare filenames against.
RENDER_DIRNAME = "render"


@dataclass(frozen=True, slots=True)
class RenderInvocation:
    """An argv and the directory it must be run from.

    The two travel together because neither is correct alone: the filter graph
    references its command and subtitle files by bare filename, so a caller that
    ran this argv from anywhere else would have ffmpeg look for them in the wrong
    place. Returning the directory makes that the caller's obligation rather than
    a fact buried in a docstring.
    """

    argv: list[str]
    cwd: Path


def build_render_argv(
    *,
    source: Path,
    dest: Path,
    job_dir: Path,
    clip_id: str,
    span: TimeSpan,
    crop_size: tuple[int, int],
    output: OutputSpec,
) -> RenderInvocation:
    """One native pass: seek, crop along the trajectory, scale, burn in captions.

    **`-filter_complex` is where argv's safety argument stops applying.** List
    form protects the *tokens* — nothing splits them on `;` or a backtick,
    because nothing parses them as a command line. But the filter string is a
    single token that ffmpeg itself parses, with its own grammar: `:` separates
    options, `,` separates filters, and quoting and escaping are its own.

    A job directory on Windows carries a drive-letter colon and backslashes, both
    of which are syntax inside that grammar, and a directory an operator named
    with an apostrophe would close a quote. So
    **no path reaches the graph**: the command and subtitle files are referenced
    by bare filename and resolved against `cwd`.

    That leaves the id itself as the only value composed into the string, which
    is why it is validated first. A `clip_id` carrying `/`, `..` or a quote would
    escape the directory or the quoting; after `make_clip_id` it is twenty-six
    characters from a fixed alphabet.

    The crop size is taken, never recomputed. Stage 1 fixed it and
    `CropTrajectory` holds it for the whole clip, so deriving it again here would
    be a second answer to a question already settled — and the quality
    declaration divides by it.
    """
    validated = make_clip_id(clip_id)

    if span.duration_s <= 0:
        # Zero length only, in practice: `TimeSpan` already refuses a reversed
        # pair in `__post_init__`, so `-t` can never go negative — which matters
        # because ffmpeg reads a negative duration as "until the end". What
        # reaches here is a span that is coherent to *construct* and meaningless
        # to *render*, and `ClipRangeInvalid` says whose error it is.
        raise ClipRangeInvalid(
            f"clip range {span.start_s}s..{span.end_s}s has no footage in it"
        )

    crop_w, crop_h = crop_size
    return RenderInvocation(
        argv=[
            *_ffmpeg_prefix(),
            "-ss",
            _seconds(span.start_s),
            "-i",
            str(resolve_inside(job_dir, source)),
            "-t",
            _seconds(span.duration_s),
            "-filter_complex",
            _render_graph(validated, crop_w, crop_h, output),
            "-y",
            str(resolve_inside(job_dir, dest)),
        ],
        cwd=resolve_inside(job_dir, job_dir / RENDER_DIRNAME),
    )


def _render_graph(
    clip_id: str, crop_w: int, crop_h: int, output: OutputSpec
) -> str:
    """The four stages, in the only order that works.

    `sendcmd` first because it drives the `crop` that follows it — a command
    stream cannot address a filter declared before it. `scale` after `crop` so
    the upscale happens once, on the smaller picture. `subtitles` last so the
    captions are burned at output resolution rather than scaled up with the
    frame, which would soften every glyph.

    Bare filenames throughout. See `build_render_argv` for why.
    """
    return ",".join(
        (
            f"sendcmd=f={clip_id}.cmds",
            f"crop=w={crop_w}:h={crop_h}:x=0:y=0",
            f"scale={output.width}:{output.height}",
            f"subtitles={clip_id}.ass",
        )
    )
