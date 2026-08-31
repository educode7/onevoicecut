"""ffmpeg missing from PATH must be an actionable error, not a stack trace.

This is a runtime failure mode, not only an install-time one: ffmpeg is a system
binary that a machine can lose between one job and the next. An operator three
hours into a transcription deserves a sentence telling them what to install, not
`FileNotFoundError: [WinError 2]`.

Both directions are monkeypatched. Asserting against the real PATH would make
these tests pass or fail depending on whether the machine happens to have ffmpeg,
which is precisely the coupling the fixture exists to remove.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from onevoicecut.adapters.ffmpeg.extractor import FfmpegAudioExtractor
from onevoicecut.domain.errors import FfmpegUnavailable
from onevoicecut.domain.ids import make_job_id, make_media_id
from onevoicecut.domain.media import SourceMedia

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "jobs" / "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    directory.mkdir(parents=True)
    return directory


@pytest.fixture
def media(job_dir: Path) -> SourceMedia:
    source = job_dir / "source.mp4"
    source.write_bytes(b"x")
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="clip.mp4",
        stored_path=source,
        size_bytes=1,
        container="mp4",
        checksum="d",
    )


class NeverCalled:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], timeout_s: float | None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def without_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: None)


@pytest.fixture
def with_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")


def test_missing_binary_raises_a_domain_error(
    without_ffmpeg: None, job_dir: Path, media: SourceMedia
) -> None:
    with pytest.raises(FfmpegUnavailable):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=NeverCalled()).probe(media)


def test_the_error_names_the_missing_binary(
    without_ffmpeg: None, job_dir: Path, media: SourceMedia
) -> None:
    with pytest.raises(FfmpegUnavailable, match="ffprobe"):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=NeverCalled()).probe(media)


def test_the_error_tells_the_operator_what_to_do(
    without_ffmpeg: None, job_dir: Path, media: SourceMedia
) -> None:
    """A message that only says 'not found' leaves the operator guessing whether
    it is a pip package."""
    with pytest.raises(FfmpegUnavailable) as caught:
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=NeverCalled()).probe(media)
    message = str(caught.value).lower()
    assert "install" in message
    assert "path" in message
    assert "pip" in message  # states explicitly that it is not one


def test_nothing_is_spawned_when_the_binary_is_missing(
    without_ffmpeg: None, job_dir: Path, media: SourceMedia
) -> None:
    runner = NeverCalled()
    with pytest.raises(FfmpegUnavailable):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner).probe(media)
    assert runner.calls == []


def test_extract_also_checks_availability(
    without_ffmpeg: None, job_dir: Path, media: SourceMedia
) -> None:
    with pytest.raises(FfmpegUnavailable):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=NeverCalled()).extract(
            media, job_dir / "audio.flac"
        )


def test_a_binary_removed_after_the_check_still_fails_cleanly(
    with_ffmpeg: None, job_dir: Path, media: SourceMedia
) -> None:
    """`which` succeeding does not guarantee the spawn will: the binary can vanish
    in between, and on Windows a PATH entry can point at a stale directory."""

    def vanished(
        argv: list[str], timeout_s: float | None
    ) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "The system cannot find the file specified")

    with pytest.raises(FfmpegUnavailable, match="ffprobe"):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=vanished).probe(media)


def test_availability_is_checked_once_per_binary(
    with_ffmpeg: None, job_dir: Path, media: SourceMedia, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 3-hour job slices ~87 chunks; re-scanning PATH for each is pure waste."""
    lookups: list[str] = []

    def counting_which(name: str) -> str:
        lookups.append(name)
        return f"/usr/bin/{name}"

    monkeypatch.setattr(shutil, "which", counting_which)

    extractor = FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=NeverCalled())
    with pytest.raises(Exception):  # probe fails on empty stdout, which is fine here
        extractor.probe(media)
    with pytest.raises(Exception):
        extractor.probe(media)

    assert lookups.count("ffprobe") == 1
