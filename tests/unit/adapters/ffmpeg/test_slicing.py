"""Slicing the normalized track into the chunks the plan asked for.

`AudioChunk` carries a `job_id` that none of `slice()`'s three arguments has:
`AudioTrack` knows its `media_id`, and `PlannedChunk` is pure geometry. The
adapter is already per-job — it is constructed with that job's directory — so the
job id belongs on the constructor alongside it rather than being smuggled out of
the directory name.
"""

import subprocess
from pathlib import Path

import pytest

from transcribe.adapters.ffmpeg.argv import build_slice_argv
from transcribe.adapters.ffmpeg.extractor import FfmpegAudioExtractor
from transcribe.domain.chunking import PlannedChunk
from transcribe.domain.errors import ExtractionFailed
from transcribe.domain.ids import make_job_id, make_media_id
from transcribe.domain.media import AudioTrack

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")

# A chunk from deep inside a three-hour track: 2h30m in, ten minutes plus overlap.
DEEP_CHUNK = PlannedChunk(index=15, start_s=9000.0, end_s=9605.0)


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "jobs" / "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    directory.mkdir(parents=True)
    return directory


@pytest.fixture
def track(job_dir: Path) -> AudioTrack:
    path = job_dir / "audio.flac"
    path.write_bytes(b"x" * 1024)
    return AudioTrack(
        media_id=MEDIA_ID, path=path, duration_s=10800.0, size_bytes=1024
    )


class Runner:
    def __init__(self, *, returncode: int = 0, writes: Path | None = None) -> None:
        self.calls: list[list[str]] = []
        self._returncode = returncode
        self._writes = writes

    def __call__(
        self, argv: list[str], timeout_s: float | None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if self._writes is not None and self._returncode == 0:
            self._writes.write_bytes(b"y" * 2048)
        return subprocess.CompletedProcess(argv, self._returncode, "", "boom")


def _extractor(job_dir: Path, runner: Runner) -> FfmpegAudioExtractor:
    return FfmpegAudioExtractor(job_dir, job_id=JOB_ID, runner=runner)


# --- argv ---------------------------------------------------------------------


def test_seek_comes_before_the_input(job_dir: Path) -> None:
    """Input seeking, not decode-and-discard.

    Reaching 2h30m by decoding everything before it would cost more than the
    transcription. With `-ss` ahead of `-i`, ffmpeg seeks the container instead.
    """
    argv = build_slice_argv(job_dir / "audio.flac", DEEP_CHUNK, job_dir / "0015.flac")
    assert argv.index("-ss") < argv.index("-i")


def test_duration_is_a_length_not_an_end_time(job_dir: Path) -> None:
    """`-t` takes a duration; passing the end time would slice past the track."""
    argv = build_slice_argv(job_dir / "audio.flac", DEEP_CHUNK, job_dir / "0015.flac")
    assert argv[argv.index("-ss") + 1] == "9000.000"
    assert argv[argv.index("-t") + 1] == "605.000"


def test_slice_argv_keeps_the_normalized_format(job_dir: Path) -> None:
    """Re-encoded rather than stream-copied, so boundaries land where the plan
    says instead of on the nearest frame."""
    argv = build_slice_argv(job_dir / "audio.flac", DEEP_CHUNK, job_dir / "0015.flac")
    assert argv[argv.index("-ar") + 1] == "16000"
    assert argv[argv.index("-ac") + 1] == "1"
    assert argv[argv.index("-c:a") + 1] == "flac"


def test_slice_argv_carries_the_hardening_flags(job_dir: Path) -> None:
    argv = build_slice_argv(job_dir / "audio.flac", DEEP_CHUNK, job_dir / "0015.flac")
    assert "-nostdin" in argv
    assert argv[argv.index("-protocol_whitelist") + 1] == "file"
    assert argv[-1] == str((job_dir / "0015.flac").resolve())


def test_sub_second_boundaries_survive_formatting(job_dir: Path) -> None:
    planned = PlannedChunk(index=1, start_s=600.25, end_s=1205.125)
    argv = build_slice_argv(job_dir / "audio.flac", planned, job_dir / "0001.flac")
    assert argv[argv.index("-ss") + 1] == "600.250"
    assert argv[argv.index("-t") + 1] == "604.875"


# --- slice() ------------------------------------------------------------------


def test_slice_returns_a_chunk_matching_the_plan(
    job_dir: Path, track: AudioTrack
) -> None:
    dest = job_dir / "chunks" / "0015.flac"
    runner = Runner(writes=dest)

    chunk = _extractor(job_dir, runner).slice(track, DEEP_CHUNK, dest)

    assert chunk.job_id == JOB_ID
    assert chunk.index == 15
    assert (chunk.start_s, chunk.end_s) == (9000.0, 9605.0)
    assert chunk.path == dest.resolve()
    assert chunk.size_bytes == 2048


def test_chunk_boundaries_come_from_the_plan_not_the_file(
    job_dir: Path, track: AudioTrack
) -> None:
    """The stitcher derives its contested window from the plan, so a chunk that
    reported its own drifted boundaries would desynchronise the two."""
    dest = job_dir / "chunks" / "0015.flac"
    chunk = _extractor(job_dir, Runner(writes=dest)).slice(track, DEEP_CHUNK, dest)
    assert chunk.end_s - chunk.start_s == DEEP_CHUNK.end_s - DEEP_CHUNK.start_s


def test_slice_creates_the_destination_directory(
    job_dir: Path, track: AudioTrack
) -> None:
    dest = job_dir / "chunks" / "0015.flac"
    assert not dest.parent.exists()
    _extractor(job_dir, Runner(writes=dest)).slice(track, DEEP_CHUNK, dest)
    assert dest.parent.is_dir()


def test_slice_failure_becomes_a_domain_error(
    job_dir: Path, track: AudioTrack
) -> None:
    with pytest.raises(ExtractionFailed, match="boom"):
        _extractor(job_dir, Runner(returncode=1)).slice(
            track, DEEP_CHUNK, job_dir / "chunks" / "0015.flac"
        )


def test_slice_fails_when_ffmpeg_writes_nothing(
    job_dir: Path, track: AudioTrack
) -> None:
    with pytest.raises(ExtractionFailed, match="no output"):
        _extractor(job_dir, Runner()).slice(
            track, DEEP_CHUNK, job_dir / "chunks" / "0015.flac"
        )


def test_destination_outside_the_job_dir_is_refused_before_spawning(
    job_dir: Path, track: AudioTrack, tmp_path: Path
) -> None:
    runner = Runner()
    with pytest.raises(ExtractionFailed, match="outside"):
        _extractor(job_dir, runner).slice(track, DEEP_CHUNK, tmp_path / "escaped.flac")
    assert runner.calls == []


def test_a_track_outside_the_job_dir_is_refused_before_spawning(
    job_dir: Path, tmp_path: Path
) -> None:
    stray = AudioTrack(
        media_id=MEDIA_ID,
        path=tmp_path / "elsewhere.flac",
        duration_s=10.0,
        size_bytes=1,
    )
    runner = Runner()
    with pytest.raises(ExtractionFailed, match="outside"):
        _extractor(job_dir, runner).slice(
            stray, DEEP_CHUNK, job_dir / "chunks" / "0015.flac"
        )
    assert runner.calls == []
