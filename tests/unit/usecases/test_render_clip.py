"""The guards that run before a render process exists.

Every refusal here is checked against the *probe* and the configured bound, not
against ffmpeg's opinion -- which is the point. A three-hour span, a span past
the end of the source, or a negative start are all things ffmpeg would happily
begin working on: it seeks, allocates, and finds out late, having spent the
machine on a clip nobody can use. The threat-matrix row this closes is render
resource exhaustion, and a guard that fires after the spawn closes nothing.

**`ClipRangeInvalid`, never `RenderFailed`.** The two exist as separate types so
a caller can decide "retry or refuse" without reading a message, and every
condition here fails identically on every retry.

The no-spawn half is proven against the real adapter with an injected runner,
not against a fake port. A fake that records calls proves the use case did not
call *it*; only the runner proves no process was created.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.ffmpeg.video_render import FfmpegVideoRenderer
from onevoicecut.domain.errors import ClipRangeInvalid
from onevoicecut.domain.framing import (
    CropKeyframe,
    CropRect,
    CropTrajectory,
    KeyframeOrigin,
    TimeSpan,
    TrackingConfidence,
)
from onevoicecut.domain.ids import make_media_id
from onevoicecut.domain.media import MediaProbe, SourceMedia
from onevoicecut.domain.rendering import OutputSpec, SubtitleCue
from onevoicecut.ports.capabilities import RenderCapabilities, RenderSupport
from onevoicecut.ports.video_render import RenderedFile, RenderRequest
from onevoicecut.usecases.render_clip import DEFAULT_MAX_CLIP_SECONDS, render_clip

CLIP_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFD"
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")
OUTPUT = OutputSpec(width=1080, height=1920)
CROP = CropRect(x=40, y=0, width=606, height=1080)
CUES = (SubtitleCue(start_s=0.0, end_s=2.0, text="hermanos, buenos dias"),)
SOURCE_DURATION_S = 7200.0


class RecordingRenderer:
    """A `VideoRenderPort` that records instead of rendering."""

    def __init__(self) -> None:
        self.calls: list[tuple[RenderRequest, Path]] = []

    def capabilities(self) -> RenderCapabilities:
        return RenderCapabilities(
            renderer_id="recording",
            rendering=RenderSupport.AVAILABLE,
            max_clip_seconds=DEFAULT_MAX_CLIP_SECONDS,
        )

    def render(self, request: RenderRequest, dest: Path) -> RenderedFile:
        self.calls.append((request, dest))
        return RenderedFile(
            path=dest,
            width=request.output.width,
            height=request.output.height,
            duration_s=request.span.duration_s,
        )


class NeverSpawned:
    """The render adapter's runner shape, refusing to be called."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], *, cwd: Path, timeout_s: float
    ) -> object:  # pragma: no cover - a call here is the failure
        self.calls.append(argv)
        raise AssertionError("a process was spawned past a range guard")


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "jobs" / "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    directory.mkdir(parents=True)
    return directory


@pytest.fixture
def media(job_dir: Path) -> SourceMedia:
    source = job_dir / "source"
    source.write_bytes(b"not really a video")
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicacion.mp4",
        stored_path=source,
        size_bytes=18,
        container="mp4",
        checksum="deadbeef",
    )


def _probe(*, duration_s: float = SOURCE_DURATION_S) -> MediaProbe:
    return MediaProbe(
        duration_s=duration_s,
        container="mov,mp4,m4a",
        has_audio=True,
    )


def _request(media: SourceMedia, span: TimeSpan) -> RenderRequest:
    return RenderRequest(
        media=media,
        span=span,
        trajectory=CropTrajectory(
            keyframes=(
                CropKeyframe(at_s=0.0, rect=CROP, origin=KeyframeOrigin.TRACKED),
            ),
            tracking=TrackingConfidence.WELL_TRACKED,
        ),
        cues=CUES,
        output=OUTPUT,
    )


# Each pair is a span and the reason no renderer should ever see it.
INVALID_SPANS = [
    pytest.param(TimeSpan(-5.0, 25.0), id="starts_before_the_source_does"),
    pytest.param(TimeSpan(120.0, 120.0), id="has_no_footage_in_it"),
    # Deliberately inside the duration ceiling. A span that broke both rules at
    # once would still be refused with the source-length check gone, and would
    # tell nobody which rule did it.
    pytest.param(TimeSpan(7190.0, 7250.0), id="ends_past_the_end_of_the_source"),
    pytest.param(TimeSpan(0.0, 7300.0), id="is_longer_than_the_source"),
    pytest.param(TimeSpan(60.0, 300.0), id="is_longer_than_the_configured_bound"),
]


class TestAnInvalidRangeIsRefused:
    @pytest.mark.parametrize("span", INVALID_SPANS)
    def test_it_raises_clip_range_invalid(
        self, span: TimeSpan, job_dir: Path, media: SourceMedia
    ) -> None:
        renderer = RecordingRenderer()
        with pytest.raises(ClipRangeInvalid):
            render_clip(
                _request(media, span),
                renderer=renderer,
                probe=_probe(),
                dest=job_dir / "render" / f"{CLIP_ID}.mp4",
            )

    @pytest.mark.parametrize("span", INVALID_SPANS)
    def test_the_renderer_is_never_reached(
        self, span: TimeSpan, job_dir: Path, media: SourceMedia
    ) -> None:
        renderer = RecordingRenderer()
        with pytest.raises(ClipRangeInvalid):
            render_clip(
                _request(media, span),
                renderer=renderer,
                probe=_probe(),
                dest=job_dir / "render" / f"{CLIP_ID}.mp4",
            )

        assert renderer.calls == []

    @pytest.mark.parametrize("span", INVALID_SPANS)
    def test_no_process_is_created(
        self, span: TimeSpan, job_dir: Path, media: SourceMedia
    ) -> None:
        """The claim the whole guard exists to make, against the real adapter.

        A recording fake proves only that the use case did not call the port it
        was handed. This wires the shipped renderer to a runner that fails on
        contact, so the assertion is about processes rather than about which
        object was consulted.
        """
        runner = NeverSpawned()
        renderer = FfmpegVideoRenderer(job_dir, runner=runner)  # type: ignore[arg-type]
        with pytest.raises(ClipRangeInvalid):
            render_clip(
                _request(media, span),
                renderer=renderer,
                probe=_probe(),
                dest=job_dir / "render" / f"{CLIP_ID}.mp4",
            )

        assert runner.calls == []

    def test_the_message_names_the_source_duration_it_was_measured_against(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """An operator reading "invalid range" learns nothing. The two numbers
        that decided it are the range and what it was compared to."""
        with pytest.raises(ClipRangeInvalid, match="7200"):
            render_clip(
                _request(media, TimeSpan(7190.0, 7250.0)),
                renderer=RecordingRenderer(),
                probe=_probe(),
                dest=job_dir / "render" / f"{CLIP_ID}.mp4",
            )


class TestTheBoundsAreInclusiveWhereItMatters:
    def test_a_clip_exactly_at_the_bound_renders(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """`<=`, not `<`. A ceiling that refused the value it names would make
        the configured number a lie by one second."""
        renderer = RecordingRenderer()
        render_clip(
            _request(media, TimeSpan(60.0, 60.0 + DEFAULT_MAX_CLIP_SECONDS)),
            renderer=renderer,
            probe=_probe(),
            dest=job_dir / "render" / f"{CLIP_ID}.mp4",
        )

        assert len(renderer.calls) == 1

    def test_a_clip_ending_exactly_at_the_end_of_the_source_renders(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """The last thirty seconds of a sermon are a clip candidate like any
        other, and the closing appeal is where they usually are."""
        renderer = RecordingRenderer()
        render_clip(
            _request(media, TimeSpan(SOURCE_DURATION_S - 30.0, SOURCE_DURATION_S)),
            renderer=renderer,
            probe=_probe(),
            dest=job_dir / "render" / f"{CLIP_ID}.mp4",
        )

        assert len(renderer.calls) == 1

    def test_a_clip_starting_at_zero_renders(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        renderer = RecordingRenderer()
        render_clip(
            _request(media, TimeSpan(0.0, 30.0)),
            renderer=renderer,
            probe=_probe(),
            dest=job_dir / "render" / f"{CLIP_ID}.mp4",
        )

        assert len(renderer.calls) == 1


class TestTheBoundIsConfigurable:
    def test_a_tighter_bound_refuses_a_clip_the_default_would_allow(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """A destination with a shorter ceiling is the ordinary case, not a
        hypothetical -- `RenderProfile.max_duration_s` exists for it."""
        renderer = RecordingRenderer()
        with pytest.raises(ClipRangeInvalid):
            render_clip(
                _request(media, TimeSpan(60.0, 150.0)),
                renderer=renderer,
                probe=_probe(),
                dest=job_dir / "render" / f"{CLIP_ID}.mp4",
                max_clip_seconds=60.0,
            )

        assert renderer.calls == []


class TestAValidRangeIsHandedOnUntouched:
    def test_the_request_reaches_the_renderer_unchanged(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """The use case guards; it never edits. Trimming an over-long span to
        fit would remove either the setup or the payoff, and which one is a
        judgement about the material no rule here is positioned to make."""
        renderer = RecordingRenderer()
        request = _request(media, TimeSpan(120.0, 150.0))
        dest = job_dir / "render" / f"{CLIP_ID}.mp4"

        render_clip(request, renderer=renderer, probe=_probe(), dest=dest)

        assert renderer.calls == [(request, dest)]

    def test_the_renderers_own_answer_is_returned(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        rendered = render_clip(
            _request(media, TimeSpan(120.0, 150.0)),
            renderer=RecordingRenderer(),
            probe=_probe(),
            dest=job_dir / "render" / f"{CLIP_ID}.mp4",
        )

        assert (rendered.width, rendered.height) == (OUTPUT.width, OUTPUT.height)
        assert rendered.duration_s == 30.0
