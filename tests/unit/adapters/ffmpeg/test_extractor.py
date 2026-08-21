"""The ffmpeg adapter's contract with `subprocess`, asserted without ffmpeg.

Every test here substitutes the runner, so the suite proves how the adapter
*invokes* ffmpeg and how it maps failures — never that ffmpeg itself works. That
second claim needs the real binary and lives in the `integration`-marked test,
which skips when ffmpeg is absent.
"""

import json
import subprocess
from pathlib import Path

import pytest

from transcribe.adapters.ffmpeg.extractor import FfmpegAudioExtractor
from transcribe.domain.errors import ExtractionFailed, UnsupportedContainer
from transcribe.domain.ids import make_job_id, make_media_id
from transcribe.domain.media import SourceMedia

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")

PROBE_JSON = json.dumps(
    {
        "format": {"duration": "3600.5", "format_name": "mov,mp4,m4a"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
)

SILENT_JSON = json.dumps(
    {"format": {"duration": "12.0", "format_name": "mov,mp4"}, "streams": []}
)


class RecordingRunner:
    """Captures calls instead of spawning anything.

    Probe and extract are configured separately because `extract` probes first
    for the duration, so an extraction test has to get past a successful probe
    before it can exercise the ffmpeg call at all.
    """

    def __init__(
        self,
        *,
        probe_stdout: str = PROBE_JSON,
        probe_returncode: int = 0,
        probe_stderr: str = "",
        extract_returncode: int = 0,
        extract_stderr: str = "",
        writes: Path | None = None,
    ) -> None:
        self.calls: list[tuple[list[str], float | None]] = []
        self._probe = (probe_returncode, probe_stdout, probe_stderr)
        self._extract = (extract_returncode, "", extract_stderr)
        self._writes = writes

    def __call__(
        self, argv: list[str], timeout_s: float | None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, timeout_s))
        if argv[0] == "ffprobe":
            code, out, err = self._probe
        else:
            code, out, err = self._extract
            if self._writes is not None and code == 0:
                self._writes.write_bytes(b"x" * 4096)  # stands in for ffmpeg's output
        return subprocess.CompletedProcess(argv, code, out, err)

    @property
    def argv(self) -> list[str]:
        return self.calls[-1][0]


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "jobs" / "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    directory.mkdir(parents=True)
    return directory


@pytest.fixture
def media(job_dir: Path) -> SourceMedia:
    source = job_dir / "source.mp4"
    source.write_bytes(b"not really a video")
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="clip; rm -rf ~.mp4",
        stored_path=source,
        size_bytes=18,
        container="mp4",
        checksum="deadbeef",
    )


def test_probe_reports_duration_container_and_audio_presence(
    job_dir: Path, media: SourceMedia
) -> None:
    runner = RecordingRunner()
    probe = FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner).probe(media)
    assert probe.duration_s == 3600.5
    assert probe.has_audio is True
    assert probe.container == "mov,mp4,m4a"


def test_probe_reports_a_container_with_no_audio_stream(
    job_dir: Path, media: SourceMedia
) -> None:
    """A video-only upload is detectable here rather than failing mid-extraction."""
    runner = RecordingRunner(probe_stdout=SILENT_JSON)
    assert FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner).probe(media).has_audio is False


def test_probe_rejects_unparseable_output(job_dir: Path, media: SourceMedia) -> None:
    runner = RecordingRunner(probe_stdout="this is not json")
    with pytest.raises(UnsupportedContainer):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner).probe(media)


def test_probe_failure_becomes_a_domain_error(
    job_dir: Path, media: SourceMedia
) -> None:
    runner = RecordingRunner(probe_returncode=1, probe_stderr="Invalid data found")
    with pytest.raises(UnsupportedContainer, match="Invalid data"):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner).probe(media)


def test_extract_returns_a_normalized_track(job_dir: Path, media: SourceMedia) -> None:
    dest = job_dir / "audio.flac"
    runner = RecordingRunner(writes=dest)
    track = FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner).extract(media, dest)
    assert track.media_id == MEDIA_ID
    assert track.path == dest.resolve()
    assert track.duration_s == 3600.5
    assert track.size_bytes == 4096
    assert (track.sample_rate, track.channels, track.codec) == (16000, 1, "flac")


def test_extract_failure_becomes_a_domain_error(
    job_dir: Path, media: SourceMedia
) -> None:
    runner = RecordingRunner(extract_returncode=1, extract_stderr="Output file is empty")
    with pytest.raises(ExtractionFailed, match="Output file is empty"):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner).extract(media, job_dir / "a.flac")


def test_extract_fails_when_ffmpeg_reports_success_but_writes_nothing(
    job_dir: Path, media: SourceMedia
) -> None:
    """A zero-exit run that produced no file is a failure, not an empty track."""
    runner = RecordingRunner()  # succeeds, but writes nothing
    with pytest.raises(ExtractionFailed, match="no output"):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner).extract(media, job_dir / "a.flac")


def test_a_timeout_becomes_a_domain_error(job_dir: Path, media: SourceMedia) -> None:
    def runner(argv: list[str], timeout_s: float | None) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout_s or 0.0)

    with pytest.raises(ExtractionFailed, match="timed out"):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner).extract(media, job_dir / "a.flac")


def test_the_configured_timeout_reaches_the_runner(
    job_dir: Path, media: SourceMedia
) -> None:
    """An unbounded ffmpeg call would hang a worker with no watchdog until slice 7b."""
    runner = RecordingRunner()
    FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner, timeout_s=42.0).probe(media)
    assert runner.calls[-1][1] == 42.0


def test_destination_outside_the_job_dir_is_refused_before_spawning(
    job_dir: Path, media: SourceMedia, tmp_path: Path
) -> None:
    runner = RecordingRunner()
    with pytest.raises(ExtractionFailed, match="outside"):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner).extract(
            media, tmp_path / "escaped.flac"
        )
    assert runner.calls == []  # nothing was ever launched


def test_source_outside_the_job_dir_is_refused_before_spawning(
    job_dir: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"x")
    stray = SourceMedia(
        media_id=MEDIA_ID,
        original_filename="elsewhere.mp4",
        stored_path=outside,
        size_bytes=1,
        container="mp4",
        checksum="d",
    )
    runner = RecordingRunner()
    with pytest.raises(ExtractionFailed, match="outside"):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner).probe(stray)
    assert runner.calls == []


def test_hostile_original_filename_never_reaches_the_command_line(
    job_dir: Path, media: SourceMedia
) -> None:
    """`original_filename` is metadata; the stored path is what gets invoked."""
    runner = RecordingRunner()
    FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner).probe(media)
    assert media.original_filename == "clip; rm -rf ~.mp4"
    assert not any("rm -rf" in token for token in runner.argv)
