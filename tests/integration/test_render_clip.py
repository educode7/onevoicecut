"""Real ffmpeg, a real render. The only place that claims a rendered clip is
watchable rather than merely well-argued.

Every other render test substitutes the runner and proves how the adapter
*invokes* ffmpeg; none of them proves ffmpeg agrees with what was commanded.
That gap is exactly where `build_render_argv`'s `cwd` bug hid — every unit
fixture puts its `.ass` flat in `job_dir/render/`, which is also where the
pre-fix `cwd` pointed, so the mismatch between that `cwd` and the per-profile
layout `render_worker._export_from_trajectory` actually writes into
(`render/{profile}/{clip_id}.*`) was invisible to every one of them.

Three questions, three tests, because a fixture that varies more than one axis
at a time cannot say which axis the assertion actually read:

- Does the crop the adapter was handed land where it was told to, not merely
  somewhere?  Answered by cropping a frame whose two halves are unmistakably
  different colours and reading the colour back.
- Does supplying subtitle cues change what is on the frame, relative to an
  identical request carrying none?  Answered by decoding a frame to raw RGB
  with ffmpeg itself (already a hard dependency; no imaging library is added)
  and counting near-white pixels in the region the profile's safe area
  reserves for a caption. This proves a measurable pixel difference
  attributable to the cues -- it does **not** prove the glyphs are legible
  text, which would need OCR this repo does not depend on.
- Does the output container actually carry the commanded 9:16 geometry?

The fixture source is two solid-colour lavfi frames stacked side by side,
generated at run time for the same reason `test_ffmpeg_extraction.py`
generates its fixture: `.gitignore` excludes media, so a checked-in one would
be silently ignored by git, and every test here already requires ffmpeg to
mean anything.
"""

import json
import struct
import subprocess
from pathlib import Path
from time import monotonic

import pytest

from onevoicecut.adapters.ffmpeg.process import BinaryInvoker, real_process
from onevoicecut.adapters.ffmpeg.subtitles import render_ass
from onevoicecut.adapters.ffmpeg.video_render import FfmpegVideoRenderer
from onevoicecut.domain.errors import RenderFailed
from onevoicecut.domain.framing import (
    CropKeyframe,
    CropRect,
    CropTrajectory,
    KeyframeOrigin,
    TimeSpan,
    TrackingConfidence,
)
from onevoicecut.domain.ids import make_clip_id, make_media_id
from onevoicecut.domain.media import SourceMedia
from onevoicecut.domain.rendering import (
    OutputSpec,
    RenderProfile,
    SafeArea,
    SubtitleCue,
)
from onevoicecut.ports.video_render import RenderRequest

pytestmark = pytest.mark.integration

JOB_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")

# Landscape, split into two solid-colour halves the width of a NTSC-ish frame.
# Small, so a render costs milliseconds, and even, so the crop below lands on
# a whole number of pixels on both sides of the split.
FRAME_W, FRAME_H = 480, 270
HALF_W = FRAME_W // 2
SOURCE_SECONDS = 3
CLIP_SECONDS = 2.0

# 9:16, and small enough that scaling and subtitle burn-in stay fast.
OUTPUT = OutputSpec(width=180, height=320, fps=30.0)

CAPTION_PROFILE = RenderProfile(
    name="vertical",
    output=OUTPUT,
    safe_area=SafeArea(top=0.06, bottom=0.18, left=0.05, right=0.14),
    max_duration_s=90.0,
)

CLIP_ID = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFG")
CLIP_ID_WITH_TEXT = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCF1")
CLIP_ID_WITHOUT_TEXT = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCF2")


def _synthesize_split_source(dest: Path) -> None:
    """Left half blue, right half red -- unmistakable under a crop.

    Built with `-filter_complex` in the fixture generator, which is not the
    single-pass code under test and carries no obligation to stay simple the
    way `argv.py`'s graph does.
    """
    completed = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c=blue:s={HALF_W}x{FRAME_H}:d={SOURCE_SECONDS}",
            "-f", "lavfi", "-i", f"color=c=red:s={HALF_W}x{FRAME_H}:d={SOURCE_SECONDS}",
            "-filter_complex", "[0:v][1:v]hstack=inputs=2[v]",
            "-map", "[v]", "-c:v", "mpeg4",
            "-y", str(dest),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0 or not dest.exists():
        pytest.skip(
            f"this ffmpeg build cannot synthesize the fixture: {completed.stderr.strip()}"
        )


def _media(path: Path) -> SourceMedia:
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="split.mp4",
        stored_path=path,
        size_bytes=path.stat().st_size,
        container="mp4",
        checksum="not-verified-here",
    )


def _trajectory(crop: CropRect) -> CropTrajectory:
    return CropTrajectory(
        keyframes=(
            CropKeyframe(at_s=0.0, rect=crop, origin=KeyframeOrigin.TRACKED),
            CropKeyframe(at_s=CLIP_SECONDS, rect=crop, origin=KeyframeOrigin.TRACKED),
        ),
        tracking=TrackingConfidence.WELL_TRACKED,
    )


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "jobs" / JOB_ID
    directory.mkdir(parents=True)
    return directory


@pytest.fixture
def source(job_dir: Path, ffmpeg_available: None) -> Path:
    path = job_dir / "source.mp4"
    _synthesize_split_source(path)
    return path


def _render(
    job_dir: Path,
    source: Path,
    *,
    clip_id: str,
    crop: CropRect,
    cues: tuple[SubtitleCue, ...],
) -> Path:
    """One render, laid out exactly the way `render_worker` lays a real one
    out: `render/{profile}/{clip_id}.{ass,mp4}` -- not `render/{clip_id}.*`,
    which is the layout every unit fixture uses and the one the `cwd` bug
    could not be seen from.

    `clip_id` must be a valid ULID: `dest.stem` becomes the clip id
    `build_render_argv` composes into the filter string, so two renders that
    are meant to differ (with cues, without) need two distinct ids rather than
    two arbitrary filenames.
    """
    profile_dir = job_dir / "render" / CAPTION_PROFILE.name
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / f"{clip_id}.ass").write_text(
        render_ass(cues, profile=CAPTION_PROFILE), encoding="utf-8", newline="\n"
    )

    dest = profile_dir / f"{clip_id}.mp4"
    request = RenderRequest(
        media=_media(source),
        span=TimeSpan(0.0, CLIP_SECONDS),
        trajectory=_trajectory(crop),
        cues=cues,
        output=OUTPUT,
    )
    rendered = FfmpegVideoRenderer(job_dir).render(request, dest)
    return rendered.path


def _decode_frame_rgb(path: Path, *, at_s: float, width: int, height: int) -> bytes:
    """One frame, raw and uncompressed, read straight from ffmpeg's stdout.

    No imaging library: ffmpeg is already a hard dependency of this project
    and nothing else is, so shelling back out to it for the verification is
    the same call this whole test file already requires to exist at all.
    """
    completed = subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-ss", f"{at_s:.3f}", "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-",
        ],
        capture_output=True,
        timeout=60,
        check=True,
    )
    frame = completed.stdout
    expected = width * height * 3
    assert len(frame) == expected, (
        f"expected {expected} raw bytes for a {width}x{height} rgb24 frame, "
        f"got {len(frame)}"
    )
    return frame


def _pixel(frame: bytes, *, width: int, x: int, y: int) -> tuple[int, int, int]:
    offset = (y * width + x) * 3
    return struct.unpack_from("BBB", frame, offset)


def _is_reddish(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return r > 150 and b < 100


def _is_blueish(pixel: tuple[int, int, int]) -> bool:
    r, g, b = pixel
    return b > 150 and r < 100


class TestTheCommandedCropIsWhatLands:
    """The crop's `x` decides which half of the source survives. The two
    halves are exact solid colours with no gradient at the seam, so whichever
    one the output frame shows says exactly which `x` ffmpeg actually used --
    not merely that some window was cut."""

    def test_a_crop_over_the_red_half_produces_a_red_frame(
        self, job_dir: Path, source: Path
    ) -> None:
        crop = CropRect(x=HALF_W, y=0, width=HALF_W, height=FRAME_H)
        rendered = _render(
            job_dir, source, clip_id=CLIP_ID, crop=crop, cues=()
        )

        frame = _decode_frame_rgb(
            rendered, at_s=0.5, width=OUTPUT.width, height=OUTPUT.height
        )
        centre = _pixel(frame, width=OUTPUT.width, x=OUTPUT.width // 2, y=OUTPUT.height // 2)
        assert _is_reddish(centre), f"expected a red pixel from the commanded crop, got {centre}"

    def test_a_crop_over_the_blue_half_produces_a_blue_frame(
        self, job_dir: Path, source: Path
    ) -> None:
        """The same request, differing only in `crop.x` -- proving the pixel
        above tracks the trajectory's own value rather than being red no
        matter what was asked for."""
        crop = CropRect(x=0, y=0, width=HALF_W, height=FRAME_H)
        rendered = _render(
            job_dir, source, clip_id=CLIP_ID, crop=crop, cues=()
        )

        frame = _decode_frame_rgb(
            rendered, at_s=0.5, width=OUTPUT.width, height=OUTPUT.height
        )
        centre = _pixel(frame, width=OUTPUT.width, x=OUTPUT.width // 2, y=OUTPUT.height // 2)
        assert _is_blueish(centre), f"expected a blue pixel from the commanded crop, got {centre}"


class TestBurnedInText:
    """Holds the crop, the span and the profile fixed, and varies only the
    cues -- so a pixel difference between the two renders is attributable to
    the subtitle document alone, not to anything else changing at the same
    time."""

    _CROP = CropRect(x=HALF_W, y=0, width=HALF_W, height=FRAME_H)
    _CUES = (SubtitleCue(start_s=0.0, end_s=CLIP_SECONDS, text="hermanos, buenos dias"),)

    @staticmethod
    def _white_pixels_in_caption_band(frame: bytes) -> int:
        """The default template is bottom-anchored (`Alignment 2`); scan the
        band the profile's own safe area reserves rather than the exact glyph
        box, so this does not depend on font metrics this test does not
        control. Measured (not hand-picked): a bottom-anchored cue against this
        profile's margin lands at rows 233-258 of a 320-tall frame -- *above*
        the safe-area margin line itself, because `MarginV` places the
        baseline and a glyph's ink sits above its baseline. Scanning the
        bottom half rather than only the margin strip is what keeps this
        assertion from silently reading zero pixels while the render is
        correct, the way a margin-only band did on the first run of this
        test."""
        band_top = OUTPUT.height // 2
        count = 0
        for y in range(band_top, OUTPUT.height):
            for x in range(OUTPUT.width):
                r, g, b = _pixel(frame, width=OUTPUT.width, x=x, y=y)
                if r > 200 and g > 200 and b > 200:
                    count += 1
        return count

    def test_cues_burn_visibly_different_pixels_than_no_cues(
        self, job_dir: Path, source: Path
    ) -> None:
        with_text = _render(
            job_dir, source, clip_id=CLIP_ID_WITH_TEXT, crop=self._CROP, cues=self._CUES
        )
        without_text = _render(
            job_dir, source, clip_id=CLIP_ID_WITHOUT_TEXT, crop=self._CROP, cues=()
        )

        with_frame = _decode_frame_rgb(
            with_text, at_s=0.5, width=OUTPUT.width, height=OUTPUT.height
        )
        without_frame = _decode_frame_rgb(
            without_text, at_s=0.5, width=OUTPUT.width, height=OUTPUT.height
        )

        with_white = self._white_pixels_in_caption_band(with_frame)
        without_white = self._white_pixels_in_caption_band(without_frame)

        # The default style's primary colour is opaque white on a red crop, so
        # a cue in the frame should leave many more near-white pixels in the
        # caption band than an identical render whose subtitle document is
        # empty. This shows the cues measurably changed the pixels in the
        # region reserved for them -- not that the glyphs spell the source
        # text, which no assertion in this suite claims.
        assert without_white < 5, (
            f"expected the no-caption control to be free of near-white "
            f"pixels in the caption band, found {without_white}"
        )
        assert with_white > without_white + 20, (
            f"expected supplying cues to burn visibly more near-white pixels "
            f"into the caption band than the no-caption control "
            f"({with_white} vs {without_white})"
        )


class TestTheOutputIsVertical:
    def test_the_rendered_file_reports_the_commanded_9_16_geometry(
        self, job_dir: Path, source: Path
    ) -> None:
        crop = CropRect(x=HALF_W, y=0, width=HALF_W, height=FRAME_H)
        rendered = _render(
            job_dir, source, clip_id=CLIP_ID, crop=crop, cues=()
        )

        completed = subprocess.run(
            [
                "ffprobe", "-hide_banner", "-loglevel", "error",
                "-print_format", "json", "-show_streams", str(rendered),
            ],
            capture_output=True, text=True, timeout=60, check=True,
        )

        stream = json.loads(completed.stdout)["streams"][0]
        assert (int(stream["width"]), int(stream["height"])) == (OUTPUT.width, OUTPUT.height)
        assert int(stream["width"]) * 16 == int(stream["height"]) * 9


class TestARealJobDirectoryCarriesAColon:
    """Every Windows absolute path carries a drive-letter colon --
    `resolve_inside` and `build_render_argv` keep it out of the filter string
    by construction, but only a real render under a real path proves ffmpeg
    itself never chokes on the working directory it was spawned from carrying
    one."""

    def test_graph_composition_succeeds_under_a_real_colon_bearing_path(
        self, job_dir: Path, source: Path
    ) -> None:
        if ":" not in str(job_dir):
            pytest.skip("this platform's tmp_path carries no drive-letter colon to test against")

        crop = CropRect(x=HALF_W, y=0, width=HALF_W, height=FRAME_H)
        rendered = _render(
            job_dir, source, clip_id=CLIP_ID, crop=crop, cues=()
        )

        assert rendered.exists()
        assert rendered.stat().st_size > 0


class TestARealHungProcessIsKilledAtTimeout:
    """`process.py`'s `BinaryInvoker` and `real_process` are exactly what
    `FfmpegVideoRenderer.render` delegates its timeout policy to -- its
    `self._invoker.invoke(..., on_timeout=RenderFailed, ...)` call is the
    same shape exercised here.

    Driven directly against those two rather than through a full `render()`
    call, deliberately: `_timeout_for` floors every render at 60 seconds
    (already proven against a fake runner in
    `test_video_render.py::TestTheRenderTimeout`, which needs no real clock to
    check arithmetic), and waiting out that floor on a real hung process would
    cost a minute for no further proof. What a fake runner cannot prove is
    that `subprocess.run`'s own `timeout=` really terminates a hung ffmpeg and
    that the resulting `TimeoutExpired` really becomes `RenderFailed` --
    that is what is real here, with a real clock and a real kill.
    """

    # `sine` with no `duration=` and `-f null -` consuming it is a genuinely
    # unbounded real ffmpeg process -- not a script that merely sleeps, which
    # would prove the timeout plumbing kills a process without proving it can
    # interrupt ffmpeg itself mid-work.
    _HUNG_ARGV = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "sine=frequency=440",
        "-f", "null", "-",
    ]

    def test_a_hung_ffmpeg_process_is_killed_and_surfaces_as_render_failed(
        self, ffmpeg_available: None, job_dir: Path
    ) -> None:
        invoker = BinaryInvoker()
        started = monotonic()

        with pytest.raises(RenderFailed, match="timed out"):
            invoker.invoke(
                self._HUNG_ARGV,
                spawn=lambda: real_process(self._HUNG_ARGV, cwd=job_dir, timeout_s=1.5),
                timeout_s=1.5,
                on_timeout=RenderFailed,
                on_failure=RenderFailed,
            )

        elapsed = monotonic() - started
        # A process that was merely *waited out* rather than killed would
        # still return around here, since `subprocess.run`'s `timeout=` raises
        # regardless. The bound is generous precisely because it is not the
        # thing under test -- it exists to catch a regression that reintroduces
        # a blocking wait after the kill, not to time the kill itself.
        assert elapsed < 20.0, (
            f"expected the hung process to be killed and surfaced within "
            f"seconds of its own 1.5s timeout, took {elapsed:.1f}s"
        )
