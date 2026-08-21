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

from transcribe.domain.errors import ExtractionFailed

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


def build_extract_argv(source: Path, dest: Path) -> list[str]:
    """Video container in, normalized 16 kHz mono FLAC out."""
    return [
        FFMPEG_BINARY,
        "-nostdin",  # never block on a terminal read inside a worker process
        "-hide_banner",
        "-loglevel",
        "error",
        "-protocol_whitelist",
        "file",  # a container must not be able to pull in http/concat inputs
        "-i",
        str(source.resolve()),
        "-vn",  # drop video
        "-map",
        "0:a:0",  # first audio stream only
        "-ac",
        str(CHANNELS),
        "-ar",
        str(SAMPLE_RATE_HZ),
        "-c:a",
        AUDIO_CODEC,
        "-y",  # the destination is server-generated, so overwriting is intended
        str(dest.resolve()),
    ]
