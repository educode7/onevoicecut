"""How big the picture is, and the two ways that question is asked wrongly.

Rev 4 put vertical clip rendering in scope, so the renderer needs to know the
source geometry before it can crop toward a subject. `MediaProbe` gains a
`frame`, and this module is about the two cases where the obvious implementation
returns a number that is confidently wrong.

**Attached cover art is a video stream.** An mp3 with artwork probes as an audio
stream plus a 600×600 video stream, and taking "the first video stream" gives the
artwork's square dimensions as the source geometry. A renderer told the source is
square would letterbox a sermon nobody ever shot square.

**Coded geometry is not display geometry.** A phone recording vertical writes a
landscape frame plus a display matrix saying "rotate this". `width`/`height` stay
1920×1080; what a viewer sees is 1080×1920. Cropping toward a subject using the
coded numbers targets the wrong axis entirely — and a service filmed on a phone
is not an unusual input here.

Both fixture shapes were read off ffprobe 9 rather than assumed, and the
`integration` test at the bottom re-reads them from the real binary. A
hand-written fixture proves the parser; only the real thing proves the fixture.
"""

import json
import subprocess
from pathlib import Path

import pytest

from onevoicecut.adapters.ffmpeg.extractor import FfmpegAudioExtractor, ProcessRunner
from onevoicecut.domain.ids import make_job_id, make_media_id
from onevoicecut.domain.media import FrameSize, SourceMedia

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")


def _media(job_dir: Path) -> SourceMedia:
    path = job_dir / "source"
    path.write_bytes(b"not really media; the probe is faked")
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicacion.mp4",
        stored_path=path,
        size_bytes=path.stat().st_size,
        container="mp4",
        checksum="deadbeef",
    )


def _stream(**overrides: object) -> dict[str, object]:
    """A video stream as ffprobe 9 actually emits one, trimmed to what is read."""
    stream: dict[str, object] = {
        "codec_type": "video",
        "width": 1920,
        "height": 1080,
        "disposition": {"attached_pic": 0, "default": 1},
    }
    stream.update(overrides)
    return stream


AUDIO_STREAM: dict[str, object] = {
    "codec_type": "audio",
    "disposition": {"attached_pic": 0, "default": 1},
}


def _probe_returning(*streams: dict[str, object]) -> ProcessRunner:
    payload = {
        "format": {"format_name": "mov,mp4,m4a", "duration": "120.5"},
        "streams": list(streams),
    }

    def runner(argv: list[str], timeout_s: float | None) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout=json.dumps(payload), stderr=""
        )

    return runner


def _frame(tmp_path: Path, *streams: dict[str, object]) -> FrameSize | None:
    extractor = FfmpegAudioExtractor(
        tmp_path, job_id=JOB_ID, runner=_probe_returning(*streams)
    )
    return extractor.probe(_media(tmp_path)).frame


class TestANormalVideo:
    def test_its_frame_is_the_streams_dimensions(self, tmp_path: Path) -> None:
        assert _frame(tmp_path, AUDIO_STREAM, _stream()) == FrameSize(1920, 1080)

    def test_the_rest_of_the_probe_is_unchanged(self, tmp_path: Path) -> None:
        """`frame` is additive. Slices 1 through 10 read `duration_s`,
        `container` and `has_audio` and must keep reading the same values."""
        extractor = FfmpegAudioExtractor(
            tmp_path, job_id=JOB_ID, runner=_probe_returning(AUDIO_STREAM, _stream())
        )

        probe = extractor.probe(_media(tmp_path))

        assert (probe.duration_s, probe.container, probe.has_audio) == (
            120.5,
            "mov,mp4,m4a",
            True,
        )


class TestAttachedCoverArt:
    def test_artwork_is_not_the_source_geometry(self, tmp_path: Path) -> None:
        """An mp3 with a 600×600 cover probes as audio plus a *video* stream.
        Taking the first video stream reports the sermon as square."""
        cover = _stream(
            width=600, height=600, disposition={"attached_pic": 1, "default": 0}
        )

        assert _frame(tmp_path, AUDIO_STREAM, cover) is None

    def test_a_real_stream_beside_the_artwork_still_wins(
        self, tmp_path: Path
    ) -> None:
        """Skipping the artwork must not mean skipping to nothing. A container
        can carry both, and the artwork often comes first."""
        cover = _stream(
            width=600, height=600, disposition={"attached_pic": 1, "default": 0}
        )

        assert _frame(tmp_path, cover, _stream()) == FrameSize(1920, 1080)


class TestRotation:
    @pytest.mark.parametrize("rotation", [90, -90, 270, -270])
    def test_a_quarter_turn_swaps_the_axes(
        self, rotation: int, tmp_path: Path
    ) -> None:
        """Display geometry, not coded geometry. A phone filming vertical writes
        a landscape frame plus a matrix saying to rotate it, and a renderer
        cropping on the coded numbers targets the wrong axis."""
        rotated = _stream(
            side_data_list=[
                {"side_data_type": "Display Matrix", "rotation": rotation}
            ]
        )

        assert _frame(tmp_path, rotated) == FrameSize(1080, 1920)

    @pytest.mark.parametrize("rotation", [0, 180, -180])
    def test_a_half_turn_leaves_the_axes_alone(
        self, rotation: int, tmp_path: Path
    ) -> None:
        """Upside down is still landscape. Swapping here would invent a vertical
        source out of a camera that was merely mounted badly."""
        rotated = _stream(
            side_data_list=[
                {"side_data_type": "Display Matrix", "rotation": rotation}
            ]
        )

        assert _frame(tmp_path, rotated) == FrameSize(1920, 1080)

    def test_side_data_that_is_not_a_display_matrix_is_ignored(
        self, tmp_path: Path
    ) -> None:
        """`side_data_list` carries several kinds. Reading a `rotation` key from
        whichever entry happens to have one would swap axes on unrelated
        metadata."""
        other = _stream(
            side_data_list=[{"side_data_type": "Stereo 3D", "rotation": 90}]
        )

        assert _frame(tmp_path, other) == FrameSize(1920, 1080)

    def test_a_malformed_rotation_is_ignored_rather_than_fatal(
        self, tmp_path: Path
    ) -> None:
        """Probing is how this system learns a file is usable at all. A stream
        whose rotation is unreadable is still a stream with dimensions, and
        failing the whole probe over it would reject a playable source."""
        odd = _stream(
            side_data_list=[
                {"side_data_type": "Display Matrix", "rotation": "noventa"}
            ]
        )

        assert _frame(tmp_path, odd) == FrameSize(1920, 1080)


class TestWhenThereIsNoPicture:
    def test_an_audio_only_source_has_no_frame(self, tmp_path: Path) -> None:
        """`None` rather than a zero size. A renderer must be able to tell
        "there is no picture" from "the picture is nothing"."""
        assert _frame(tmp_path, AUDIO_STREAM) is None

    def test_a_video_stream_without_dimensions_has_no_frame(
        self, tmp_path: Path
    ) -> None:
        """Some containers list a video stream and no geometry. Defaulting to
        zero would hand the renderer a frame it would divide by."""
        assert _frame(tmp_path, _stream(width=None, height=None)) is None

    def test_a_zero_dimension_is_treated_as_absent(self, tmp_path: Path) -> None:
        assert _frame(tmp_path, _stream(width=0, height=1080)) is None
