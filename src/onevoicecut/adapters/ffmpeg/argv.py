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

from pathlib import Path

from onevoicecut.domain.chunking import PlannedChunk
from onevoicecut.domain.errors import ExtractionFailed

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
