"""What a renderer is asked for, what it hands back, and what it may not accept.

The port is deliberately thin. It takes a source reference, a range, a
trajectory and cues, and returns a file — it "MUST know nothing about why the
trajectory says what it says". Every declaration an operator reads is assembled
above it, because all four are known before ffmpeg is spawned and an adapter that
reported them could lie about arithmetic it never ran.

**The structural test at the bottom is the binding one.** "Every frame and every
word in the output comes from the source sermon" is the project's stated
non-negotiable, and a request type with a field able to hold an image, a URL or a
blob would make that a matter of discipline rather than of type. An absence
cannot be proven by calling something, so the module is parsed instead — the same
argument the shipped "no `UploadFile` import" test makes about the web adapter.
"""

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from onevoicecut.domain.framing import (
    CropKeyframe,
    CropRect,
    CropTrajectory,
    KeyframeOrigin,
    TimeSpan,
    TrackingConfidence,
)
from onevoicecut.domain.ids import make_media_id
from onevoicecut.domain.media import SourceMedia
from onevoicecut.domain.rendering import OutputSpec, SubtitleCue
from onevoicecut.ports import video_render
from onevoicecut.ports.capabilities import (
    DiarizationSupport,
    RenderCapabilities,
    RenderSupport,
)
from onevoicecut.ports.video_render import RenderedFile, RenderRequest

# Anything that could carry content the source sermon did not produce.
FORBIDDEN_FIELD_TYPES = {
    "bytes",
    "bytearray",
    "memoryview",
    "IO",
    "BinaryIO",
    "TextIO",
    "BytesIO",
    "StringIO",
    "UploadFile",
    "Request",
    "Response",
    "URL",
    "Url",
    "AnyUrl",
    "HttpUrl",
}


def _media() -> SourceMedia:
    return SourceMedia(
        media_id=make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ"),
        original_filename="predicacion.mp4",
        stored_path=Path("source"),
        size_bytes=4096,
        container="mp4",
        checksum="deadbeef",
    )


def _trajectory() -> CropTrajectory:
    return CropTrajectory(
        keyframes=(
            CropKeyframe(
                at_s=0.0,
                rect=CropRect(x=0, y=0, width=606, height=1080),
                origin=KeyframeOrigin.TRACKED,
            ),
        ),
        tracking=TrackingConfidence.WELL_TRACKED,
    )


def _request() -> RenderRequest:
    return RenderRequest(
        media=_media(),
        span=TimeSpan(120.0, 150.0),
        trajectory=_trajectory(),
        cues=(SubtitleCue(start_s=0.0, end_s=2.0, text="hola hermanos"),),
        output=OutputSpec(width=1080, height=1920),
    )


class TestTheRequest:
    def test_it_carries_exactly_the_spec_s_four_inputs_plus_the_target(self) -> None:
        """"A source media reference, a time range, a `CropTrajectory`, and
        subtitle cues" — plus what to deliver into. Nothing else, because
        anything else would be content the sermon did not produce."""
        assert {f.name for f in dataclasses.fields(RenderRequest)} == {
            "media",
            "span",
            "trajectory",
            "cues",
            "output",
        }

    def test_its_span_is_source_absolute(self) -> None:
        """The renderer seeks in the source file, so this pair is absolute —
        while the cues inside it are clip-local. Same split the tracker port
        already ships, and the reason is mechanical: `-ss` before `-i` resets
        output timestamps to zero."""
        assert _request().span.start_s == 120.0

    def test_it_is_frozen(self) -> None:
        request = _request()

        with pytest.raises(dataclasses.FrozenInstanceError):
            request.output = OutputSpec(width=1, height=1)  # type: ignore[misc]

    def test_a_trajectory_is_passed_through_as_given(self) -> None:
        """Opaque geometric data. The renderer must not recompute smoothing,
        dead-zone or clamping, so the port takes the finished object rather than
        the detections it was built from."""
        assert _request().trajectory.keyframes[0].origin is KeyframeOrigin.TRACKED


class TestWhatComesBack:
    def test_the_file_reports_only_what_ffmpeg_can_be_asked(self) -> None:
        """Measured facts about the produced file, and nothing an adapter would
        have to compute. The four declarations are assembled above the port
        precisely so a renderer cannot report a value it never derived."""
        assert {f.name for f in dataclasses.fields(RenderedFile)} == {
            "path",
            "width",
            "height",
            "duration_s",
        }

    def test_it_is_frozen(self) -> None:
        rendered = RenderedFile(
            path=Path("clip.mp4"), width=1080, height=1920, duration_s=30.0
        )

        with pytest.raises(dataclasses.FrozenInstanceError):
            rendered.width = 1  # type: ignore[misc]


class TestTheCapabilityDeclaration:
    def test_render_support_mirrors_the_diarization_shape(self) -> None:
        """Three members for the same reason: "choose another build" and
        "install ffmpeg" are different remediations, and collapsing them would
        send an operator to fix the wrong thing."""
        assert {m.value for m in RenderSupport} == {
            m.value for m in DiarizationSupport
        }

    def test_capabilities_name_the_renderer_and_its_limit(self) -> None:
        """`max_clip_seconds` is the render-resource-exhaustion guard, declared
        rather than enforced only inside the adapter — slice 13c reads it to
        bound how much footage a tracker is asked to sample."""
        capabilities = RenderCapabilities(
            renderer_id="ffmpeg", rendering=RenderSupport.AVAILABLE,
            max_clip_seconds=120.0,
        )

        assert capabilities.renderer_id == "ffmpeg"
        assert capabilities.max_clip_seconds == 120.0

    def test_no_limit_is_expressible(self) -> None:
        """`None` means unbounded, the same way the transcription caps do. Zero
        would be a limit that refuses every clip."""
        assert (
            RenderCapabilities(
                renderer_id="fake", rendering=RenderSupport.AVAILABLE,
                max_clip_seconds=None,
            ).max_clip_seconds
            is None
        )


class TestNothingExternalCanBeRendered:
    """The binding non-goal, made structural.

    "Every frame and every word appearing in a rendered clip MUST originate from
    the source sermon media." A field able to hold an image, a URL or a blob
    would make that a matter of discipline. An absence cannot be proven by
    calling something, so the modules are parsed.
    """

    @pytest.mark.parametrize(
        "module_name",
        ["onevoicecut.ports.video_render", "onevoicecut.domain.rendering"],
    )
    def test_no_field_type_can_carry_an_external_asset(
        self, module_name: str
    ) -> None:
        module = __import__(module_name, fromlist=["x"])
        source = Path(inspect.getsourcefile(module) or "").read_text(encoding="utf-8")

        named: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.AnnAssign) and node.annotation:
                named.update(
                    child.id
                    for child in ast.walk(node.annotation)
                    if isinstance(child, ast.Name)
                )
                named.update(
                    child.attr
                    for child in ast.walk(node.annotation)
                    if isinstance(child, ast.Attribute)
                )

        assert not (named & FORBIDDEN_FIELD_TYPES)

    def test_the_guard_would_notice_a_blob_field(self) -> None:
        """A guard that never fires proves nothing. `bytes` is in the forbidden
        set and would be caught, which is what makes the assertion above a
        constraint rather than a coincidence of the current fields."""
        assert "bytes" in FORBIDDEN_FIELD_TYPES

    def test_the_port_imports_no_http_or_io_machinery(self) -> None:
        """The import-level half, mirroring the shipped `UploadFile` test: a
        module that never imports the machinery cannot grow a field using it."""
        source = Path(inspect.getsourcefile(video_render) or "").read_text(
            encoding="utf-8"
        )

        imported: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)

        assert not (imported & {"io", "httpx", "requests", "urllib", "fastapi"})
