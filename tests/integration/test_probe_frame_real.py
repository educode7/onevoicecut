"""The same two guards, against what ffprobe actually emits.

The hand-written fixtures next door prove the parser. They cannot prove the
fixtures: every field name, every nesting level and every value type in them is a
belief about ffprobe's JSON, and a belief that is wrong makes sixteen tests pass
over a probe that returns `None` for every real file.

So both traps are rebuilt with the real binary. The cover-art case is an mp3 with
a red square attached; the rotation case is a landscape clip carrying a Display
Matrix. Both are synthesised at run time — `.gitignore` keeps media out of this
repository, so a committed fixture would be silently ignored by git, and the test
already needs ffmpeg to mean anything.

Free and fast, so it runs in the default suite and skips cleanly without ffmpeg.
"""

import subprocess
from pathlib import Path

import pytest

from onevoicecut.adapters.ffmpeg.extractor import FfmpegAudioExtractor
from onevoicecut.domain.ids import make_job_id, make_media_id
from onevoicecut.domain.media import FrameSize, MediaProbe, SourceMedia

pytestmark = pytest.mark.integration

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")

WIDTH, HEIGHT = 320, 240
COVER_SIDE = 600


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", *args],
        check=True,
        capture_output=True,
        timeout=120,
    )


def _probe(job_dir: Path, path: Path) -> MediaProbe:
    media = SourceMedia(
        media_id=MEDIA_ID,
        original_filename=path.name,
        stored_path=path,
        size_bytes=path.stat().st_size,
        container="unverified",
        checksum="deadbeef",
    )
    return FfmpegAudioExtractor(job_dir, job_id=JOB_ID).probe(media)


def _landscape(dest: Path) -> None:
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c=blue:s={WIDTH}x{HEIGHT}:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-shortest", "-c:v", "mpeg4", "-c:a", "aac", str(dest),
    )


def test_a_normal_video_reports_its_real_dimensions(
    tmp_path: Path, ffmpeg_available: None
) -> None:
    _landscape(source := tmp_path / "plain.mp4")

    assert _probe(tmp_path, source).frame == FrameSize(WIDTH, HEIGHT)


def test_attached_cover_art_is_not_reported_as_the_frame(
    tmp_path: Path, ffmpeg_available: None
) -> None:
    """An mp3 with artwork really does probe as audio plus a video stream. The
    naive read hands a renderer a square sermon."""
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-f", "lavfi", "-i", f"color=c=red:s={COVER_SIDE}x{COVER_SIDE}:d=1",
        "-map", "0:a", "-map", "1:v", "-frames:v", "1",
        "-c:v", "png", "-disposition:v:0", "attached_pic",
        str(source := tmp_path / "cover.mp3"),
    )

    probe = _probe(tmp_path, source)

    assert probe.frame is None
    assert probe.has_audio is True


def test_a_rotated_source_reports_display_geometry(
    tmp_path: Path, ffmpeg_available: None
) -> None:
    """`-display_rotation` writes the Display Matrix a phone would. The coded
    frame stays landscape; what a viewer sees is portrait, and that is what a
    renderer has to crop within."""
    _landscape(plain := tmp_path / "plain.mp4")
    _ffmpeg(
        "-display_rotation:v:0", "90", "-i", str(plain), "-c", "copy",
        str(rotated := tmp_path / "rotated.mp4"),
    )

    assert _probe(tmp_path, rotated).frame == FrameSize(HEIGHT, WIDTH)


def test_an_audio_only_source_has_no_frame(
    tmp_path: Path, ffmpeg_available: None
) -> None:
    _ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        str(source := tmp_path / "audio.m4a"),
    )

    assert _probe(tmp_path, source).frame is None
