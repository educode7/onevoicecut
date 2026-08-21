"""Real ffmpeg, real files. The only place that claims ffmpeg itself works.

The fixture container is **generated at run time rather than checked in**, a
deliberate deviation from the task plan: `.gitignore` excludes `*.mp4`/`*.wav`/
`*.mp3` to keep operator media out of the repository, so a committed media
fixture would be silently ignored by git. Synthesizing it costs nothing here
because the test already requires ffmpeg to run at all, and it keeps a binary
blob out of the history.
"""

import json
import subprocess
from pathlib import Path

import pytest

from transcribe.adapters.ffmpeg.extractor import FfmpegAudioExtractor
from transcribe.domain.ids import make_job_id, make_media_id
from transcribe.domain.media import SourceMedia

pytestmark = pytest.mark.integration

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")
CLIP_SECONDS = 2

# Windows-legal, so the same names run on every platform. `|`, `>` and `"` are
# excluded because the filesystem rejects them, not because they would be safe.
HOSTILE_NAMES = [
    "clip; rm -rf ~.mp4",
    "-i.mp4",
    "clip with spaces.mp4",
    "clip $(whoami).mp4",
    "clip & background.mp4",
]


def _synthesize(dest: Path) -> None:
    """A tiny silent-video-plus-tone container, built by ffmpeg itself."""
    completed = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={CLIP_SECONDS}",
            "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={CLIP_SECONDS}",
            "-shortest", "-c:v", "mpeg4", "-c:a", "aac",
            "-y", str(dest),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0 or not dest.exists():
        pytest.skip(f"this ffmpeg build cannot synthesize the fixture: {completed.stderr.strip()}")


def _media(path: Path, original_filename: str = "clip.mp4") -> SourceMedia:
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename=original_filename,
        stored_path=path,
        size_bytes=path.stat().st_size,
        container="mp4",
        checksum="not-verified-here",
    )


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "jobs" / "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    directory.mkdir(parents=True)
    return directory


def test_probe_reads_a_real_container(ffmpeg_available: None, job_dir: Path) -> None:
    source = job_dir / "source.mp4"
    _synthesize(source)

    probe = FfmpegAudioExtractor(job_dir, job_id=JOB_ID).probe(_media(source))
    assert probe.has_audio is True
    assert probe.duration_s == pytest.approx(CLIP_SECONDS, abs=0.5)


def test_extraction_produces_a_real_normalized_track(
    ffmpeg_available: None, job_dir: Path
) -> None:
    source = job_dir / "source.mp4"
    _synthesize(source)
    dest = job_dir / "audio.flac"

    track = FfmpegAudioExtractor(job_dir, job_id=JOB_ID).extract(_media(source), dest)

    assert track.path.exists()
    assert track.size_bytes > 0
    assert track.duration_s == pytest.approx(CLIP_SECONDS, abs=0.5)


def test_the_extracted_file_really_is_16k_mono_flac(
    ffmpeg_available: None, job_dir: Path
) -> None:
    """Asserts the bytes on disk, not the AudioTrack fields we filled in ourselves.

    `plan_chunks` derives its byte cap from this bitrate, so a silent change in
    what ffmpeg actually writes would move the chunk arithmetic underneath it.
    """
    source = job_dir / "source.mp4"
    _synthesize(source)
    dest = job_dir / "audio.flac"
    FfmpegAudioExtractor(job_dir, job_id=JOB_ID).extract(_media(source), dest)

    completed = subprocess.run(
        [
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-print_format", "json", "-show_streams", str(dest),
        ],
        capture_output=True, text=True, timeout=60, check=True,
    )
    stream = json.loads(completed.stdout)["streams"][0]
    assert stream["codec_name"] == "flac"
    assert int(stream["sample_rate"]) == 16000
    assert int(stream["channels"]) == 1


def test_video_stream_is_dropped(ffmpeg_available: None, job_dir: Path) -> None:
    source = job_dir / "source.mp4"
    _synthesize(source)
    dest = job_dir / "audio.flac"
    FfmpegAudioExtractor(job_dir, job_id=JOB_ID).extract(_media(source), dest)

    completed = subprocess.run(
        [
            "ffprobe", "-hide_banner", "-loglevel", "error",
            "-print_format", "json", "-show_streams", str(dest),
        ],
        capture_output=True, text=True, timeout=60, check=True,
    )
    streams = json.loads(completed.stdout)["streams"]
    assert all(s["codec_type"] == "audio" for s in streams)


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_hostile_filenames_survive_a_real_invocation(
    ffmpeg_available: None, job_dir: Path, name: str
) -> None:
    """The end-to-end proof that list form holds: a file literally named
    `clip; rm -rf ~.mp4` is extracted, and nothing is interpreted."""
    source = job_dir / name
    _synthesize(source)

    track = FfmpegAudioExtractor(job_dir, job_id=JOB_ID).extract(
        _media(source, original_filename=name), job_dir / "audio.flac"
    )
    assert track.size_bytes > 0
    assert source.exists()  # the shell never got a chance to touch it


def test_a_non_media_file_is_refused(ffmpeg_available: None, job_dir: Path) -> None:
    """Content decides, not the extension — the threat-matrix row for a text file
    wearing a media extension."""
    from transcribe.domain.errors import UnsupportedContainer

    source = job_dir / "source.mp4"
    source.write_text("this is plainly not a video", encoding="utf-8")

    with pytest.raises(UnsupportedContainer):
        FfmpegAudioExtractor(job_dir, job_id=JOB_ID).probe(_media(source))
