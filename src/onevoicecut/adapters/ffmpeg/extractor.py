"""`AudioExtractorPort` over the ffmpeg binaries. ffmpeg lives here and nowhere else.

Process spawning is injected as `runner` rather than calling `subprocess.run`
directly. That is not test decoration: it is what lets the invocation contract —
which flags, which timeout, and that containment is checked *before* anything is
launched — be proven on a machine with no ffmpeg installed. Whether ffmpeg itself
behaves is a separate claim, and only the `integration`-marked test makes it.

Every failure crossing this boundary is a domain error. A caller must never have
to catch `subprocess.CalledProcessError` or parse ffmpeg's stderr.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Protocol

from onevoicecut.adapters.ffmpeg.argv import (
    AUDIO_CODEC,
    CHANNELS,
    FFMPEG_BINARY,
    FFPROBE_BINARY,
    SAMPLE_RATE_HZ,
    build_extract_argv,
    build_probe_argv,
    build_slice_argv,
    resolve_inside,
)
from onevoicecut.domain.chunking import AudioChunk, PlannedChunk
from onevoicecut.domain.errors import (
    ExtractionFailed,
    FfmpegUnavailable,
    UnsupportedContainer,
)
from onevoicecut.domain.ids import JobId
from onevoicecut.domain.media import AudioTrack, FrameSize, MediaProbe, SourceMedia

# Generous, because multi-hour input is the normal case: this bounds a hung
# process, it does not bound expected work. Per-chunk timeouts are slice 4's job.
DEFAULT_TIMEOUT_S = 4 * 60 * 60.0


def _missing_binary_message(binary: str) -> str:
    return (
        f"{binary} was not found on PATH. ffmpeg is a system binary and is NOT a "
        f"pip dependency, so `pip install -r requirements.txt` does not provide "
        f"it. Install ffmpeg (https://ffmpeg.org/download.html — on Windows, "
        f"`winget install Gyan.FFmpeg`) and make sure {binary} is on PATH, then "
        f"retry the job."
    )


def require_binaries() -> None:
    """Check both binaries once, at startup, rather than at first use.

    The adapter already fails with the same message when a job reaches it, but by
    then the operator has uploaded a multi-hour file and walked away. Failing when
    the app boots costs nothing and is the difference between a clear error and a
    job that dies an hour later.
    """
    for binary in (FFMPEG_BINARY, FFPROBE_BINARY):
        if shutil.which(binary) is None:
            raise FfmpegUnavailable(_missing_binary_message(binary))


class ProcessRunner(Protocol):
    def __call__(
        self, argv: list[str], timeout_s: float | None
    ) -> subprocess.CompletedProcess[str]: ...


def _run(
    argv: list[str], timeout_s: float | None
) -> subprocess.CompletedProcess[str]:
    """The real runner. List form, and `shell` is never passed — not even False,
    so no future edit can flip it without appearing in a diff."""
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


class FfmpegAudioExtractor:
    def __init__(
        self,
        job_dir: Path,
        *,
        job_id: JobId,
        runner: ProcessRunner = _run,
        timeout_s: float | None = DEFAULT_TIMEOUT_S,
    ) -> None:
        # `AudioChunk` carries a job id that none of `slice()`'s arguments has:
        # `AudioTrack` knows only its media id and `PlannedChunk` is pure geometry.
        # The adapter is per-job already, so it belongs here rather than being
        # inferred from the directory name.
        self._job_id = job_id
        self._job_dir = job_dir
        self._runner = runner
        self._timeout_s = timeout_s
        self._verified: set[str] = set()

    def probe(self, media: SourceMedia) -> MediaProbe:
        source = resolve_inside(self._job_dir, media.stored_path)
        completed = self._invoke(build_probe_argv(source), UnsupportedContainer)

        try:
            payload = json.loads(completed.stdout)
            container = str(payload["format"]["format_name"])
            duration_s = float(payload["format"]["duration"])
            streams = payload["streams"]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise UnsupportedContainer(
                f"could not read container metadata from {source.name}: {error}"
            ) from error

        return MediaProbe(
            duration_s=duration_s,
            container=container,
            has_audio=any(s.get("codec_type") == "audio" for s in streams),
            frame=_frame_size(streams),
        )

    def extract(self, media: SourceMedia, dest: Path) -> AudioTrack:
        source = resolve_inside(self._job_dir, media.stored_path)
        destination = resolve_inside(self._job_dir, dest)
        duration_s = self.probe(media).duration_s

        destination.parent.mkdir(parents=True, exist_ok=True)
        self._invoke(build_extract_argv(source, destination), ExtractionFailed)

        if not destination.exists():
            raise ExtractionFailed(
                f"ffmpeg reported success but produced no output at {destination}"
            )

        return AudioTrack(
            media_id=media.media_id,
            path=destination,
            duration_s=duration_s,
            size_bytes=destination.stat().st_size,
            sample_rate=SAMPLE_RATE_HZ,
            channels=CHANNELS,
            codec=AUDIO_CODEC,
        )

    def slice(
        self, track: AudioTrack, planned: PlannedChunk, dest: Path
    ) -> AudioChunk:
        source = resolve_inside(self._job_dir, track.path)
        destination = resolve_inside(self._job_dir, dest)

        destination.parent.mkdir(parents=True, exist_ok=True)
        self._invoke(build_slice_argv(source, planned, destination), ExtractionFailed)

        if not destination.exists():
            raise ExtractionFailed(
                f"ffmpeg reported success but produced no output at {destination}"
            )

        return AudioChunk(
            job_id=self._job_id,
            index=planned.index,
            path=destination,
            # The plan's boundaries, not the file's: the stitcher computes its
            # contested window from the plan, so reporting anything else here
            # would desynchronise the two.
            start_s=planned.start_s,
            end_s=planned.end_s,
            size_bytes=destination.stat().st_size,
        )

    def _require_available(self, binary: str) -> None:
        """Fail with an instruction, not a stack trace, when the binary is gone.

        Cached per instance: a three-hour job slices dozens of chunks, and
        re-scanning PATH before each one is pure waste.
        """
        if binary in self._verified:
            return
        if shutil.which(binary) is None:
            raise FfmpegUnavailable(_missing_binary_message(binary))
        self._verified.add(binary)

    def _invoke(
        self, argv: list[str], failure: type[Exception]
    ) -> subprocess.CompletedProcess[str]:
        binary = argv[0]
        self._require_available(binary)
        try:
            completed = self._runner(argv, self._timeout_s)
        except FileNotFoundError as error:
            # `which` succeeded but the spawn did not: the binary can vanish in
            # between, and a stale PATH entry can point at a deleted directory.
            raise FfmpegUnavailable(_missing_binary_message(binary)) from error
        except subprocess.TimeoutExpired as error:
            raise ExtractionFailed(
                f"{argv[0]} timed out after {self._timeout_s}s"
            ) from error

        if completed.returncode != 0:
            raise failure(
                f"{argv[0]} exited {completed.returncode}: "
                f"{completed.stderr.strip() or 'no diagnostics'}"
            )
        return completed


def _frame_size(streams: list[dict[str, object]]) -> FrameSize | None:
    """The displayed geometry of the source picture, or `None` if there is none.

    Two traps, both of which return a confidently wrong number rather than an
    error, and both measured against ffprobe 9 rather than assumed.

    **Attached cover art is a video stream.** An mp3 with artwork probes as an
    audio stream plus a 600x600 video stream carrying
    `disposition.attached_pic == 1`. Taking the first video stream reports a
    sermon as square, and a renderer believing that would letterbox footage
    nobody ever shot square.

    **Coded geometry is not display geometry.** A phone filming vertical writes
    1920x1080 plus a Display Matrix saying to rotate a quarter turn; what a
    viewer sees is 1080x1920. Cropping toward a subject on the coded numbers
    targets the wrong axis, and a service filmed on a phone is ordinary input.
    """
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        disposition = stream.get("disposition")
        if isinstance(disposition, dict) and disposition.get("attached_pic"):
            continue

        width, height = stream.get("width"), stream.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            continue
        if width <= 0 or height <= 0:
            continue

        if _quarter_turned(stream.get("side_data_list")):
            return FrameSize(width=height, height=width)
        return FrameSize(width=width, height=height)

    return None


def _quarter_turned(side_data: object) -> bool:
    """Whether a Display Matrix rotates the picture onto its other axis.

    Only the Display Matrix is consulted: `side_data_list` carries several kinds,
    and reading `rotation` off whichever entry happens to have one would swap
    axes on unrelated metadata. A half turn is still landscape, so only a quarter
    turn swaps.

    An unreadable rotation is ignored rather than fatal. Probing is how this
    system learns a file is usable at all, and a stream whose rotation cannot be
    parsed is still a stream with dimensions — failing the probe over it would
    reject a source that plays.
    """
    if not isinstance(side_data, list):
        return False

    for entry in side_data:
        if not isinstance(entry, dict) or entry.get("side_data_type") != "Display Matrix":
            continue
        try:
            rotation = abs(int(float(str(entry.get("rotation", 0)))))
        except (TypeError, ValueError):
            continue
        if rotation % 180 == 90:
            return True

    return False
