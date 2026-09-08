"""Orchestrating one clip's render, and the two refusals that must come first.

The worker is where every declaration an operator reads is assembled, and the
reason they are assembled *here* rather than reported by the adapter is that all
four are known before ffmpeg is spawned. A renderer that reported them could lie
about arithmetic it never ran.

**Both refusals are about spending nothing.** A source with no picture and a
build with no vision weights are each discoverable before the expensive step, and
the expensive step is detection -- the one place model weights dominate. A worker
that detected first and refused afterwards would be correct and would still have
paid for the answer it threw away, which is why the tests below assert on the
tracker never being touched rather than on the message.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from onevoicecut.domain.errors import (
    ClipRangeInvalid,
    FrameGeometryUnavailable,
    TrackingUnavailable,
)
from onevoicecut.domain.framing import TimeSpan, TrackingConfidence
from onevoicecut.domain.generation import ClipCandidate, ScriptVariant
from onevoicecut.domain.ids import make_clip_id, make_job_id, make_media_id
from onevoicecut.domain.media import FrameSize, MediaProbe, SourceMedia
from onevoicecut.domain.rendering import (
    CaptionCoverage,
    ClipExport,
    ClipState,
    OutputQualityKind,
    OutputSpec,
    RenderProfile,
    SafeArea,
    SubtitleTimingSource,
)
from onevoicecut.domain.transcript import SegmentKind, Transcript, TranscriptSegment
from onevoicecut.ports.capabilities import RenderCapabilities, RenderSupport
from onevoicecut.ports.subject_tracker import SubjectDetection
from onevoicecut.ports.video_render import RenderedFile, RenderRequest
from onevoicecut.runtime.render_worker import render_clip_for_profile
from tests.fakes.subject_tracker import (
    FakeSubjectTrackerPort,
    UnavailableSubjectTrackerPort,
)
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
CLIP_ID = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFG")

# Measured here rather than taken from RENDER_PROFILES, whose every entry
# declares no safe area on purpose. A render legitimately refuses until an
# operator measures a destination, so a test that wants one must bring it.
PROFILE = RenderProfile(
    name="vertical",
    output=OutputSpec(width=1080, height=1920),
    safe_area=SafeArea(top=0.06, bottom=0.18, left=0.05, right=0.14),
    max_duration_s=90.0,
)


class RecordingRenderer:
    """Records the request rather than spawning.

    The four declarations are computed above this port, so a renderer able to
    report them could contradict arithmetic it never ran.
    """

    def __init__(self) -> None:
        self.requests: list[RenderRequest] = []

    def capabilities(self) -> RenderCapabilities:
        """Declared, though nothing here reads it: the worker goes through
        `render_clip`, which owns the range ceiling this would only restate."""
        return RenderCapabilities(
            renderer_id="recording-fake",
            rendering=RenderSupport.AVAILABLE,
            max_clip_seconds=None,
        )

    def render(self, request: RenderRequest, dest: Path) -> RenderedFile:
        self.requests.append(request)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"rendered")
        return RenderedFile(
            path=dest,
            width=request.output.width,
            height=request.output.height,
            duration_s=request.span.duration_s,
        )


class _SpyingUnavailableTracker(UnavailableSubjectTrackerPort):
    """The shipped unavailable fake, plus a count of what should never happen."""

    def __init__(self) -> None:
        self.detect_calls = 0

    def detect(
        self, media: SourceMedia, span: TimeSpan, *, sample_hz: float
    ) -> tuple[SubjectDetection, ...]:
        self.detect_calls += 1
        return super().detect(media, span, sample_hz=sample_hz)


def a_candidate(end_s: float = 150.0) -> ClipCandidate:
    return ClipCandidate(
        start_s=120.0,
        end_s=end_s,
        hook="Hermanos, escuchen",
        quote="Un momento del sermon",
        rationale="El punto central",
        score=0.9,
        variants=(
            ScriptVariant(
                target="tiktok", format="plain", body="Hola", duration_target_s=45.0
            ),
        ),
    )


def a_transcript(kind: SegmentKind = SegmentKind.SPEECH) -> Transcript:
    return Transcript(
        job_id=JOB_ID,
        segments=(
            TranscriptSegment(
                start_s=120.0,
                end_s=130.0,
                text="hermanos queridos",
                speaker=None,
                confidence=0.9,
                kind=kind,
                words=(),
            ),
        ),
        engine_id="fake-engine",
        diarized=False,
    )


def a_media(tmp_path: Path) -> SourceMedia:
    source = tmp_path / "source"
    source.write_bytes(b"not really a video")
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="sermon.mp4",
        stored_path=source,
        size_bytes=source.stat().st_size,
        container="mp4",
        checksum="0" * 64,
    )


def a_probe(frame: FrameSize | None = FrameSize(1920, 1080)) -> MediaProbe:
    return MediaProbe(duration_s=7200.0, container="mp4", has_audio=True, frame=frame)


def run(
    tmp_path: Path,
    *,
    tracker: FakeSubjectTrackerPort | UnavailableSubjectTrackerPort | None = None,
    probe: MediaProbe | None = None,
    transcript: Transcript | None = None,
    candidate: ClipCandidate | None = None,
    renderer: RecordingRenderer | None = None,
    storage: FakeTranscriptStoragePort | None = None,
) -> ClipExport:
    store = storage if storage is not None else FakeTranscriptStoragePort(tmp_path)
    store.save_transcript(transcript if transcript is not None else a_transcript())
    return render_clip_for_profile(
        JOB_ID,
        CLIP_ID,
        candidate if candidate is not None else a_candidate(),
        profile=PROFILE,
        media=a_media(tmp_path),
        probe=probe if probe is not None else a_probe(),
        tracker=tracker if tracker is not None else FakeSubjectTrackerPort(),
        renderer=renderer if renderer is not None else RecordingRenderer(),
        storage=store,
        job_dir=tmp_path,
    )


class TestTheHappyPath:
    def test_it_writes_a_finished_export(self, tmp_path: Path) -> None:
        export = run(tmp_path)

        assert export.state is ClipState.DONE
        assert export.profile == "vertical"

    def test_the_export_is_persisted_under_the_clip_and_profile(
        self, tmp_path: Path
    ) -> None:
        storage = FakeTranscriptStoragePort(tmp_path)

        run(tmp_path, storage=storage)

        assert storage.load_clip_exports(JOB_ID, CLIP_ID) != ()

    def test_the_variants_travel_onto_the_export(self, tmp_path: Path) -> None:
        """A file serving a network with no variant recorded is a video nobody
        can post, which is what ClipExport exists to prevent."""
        export = run(tmp_path)

        assert [variant.target for variant in export.variants] == ["tiktok"]

    def test_the_subtitle_document_is_written_for_the_renderer(
        self, tmp_path: Path
    ) -> None:
        """13b-i's adapter refuses to spawn without it, on purpose: render_ass
        needs a profile and the adapter has only an OutputSpec. This worker is
        the caller that has one."""
        run(tmp_path)

        assert (tmp_path / "render" / (CLIP_ID + ".ass")).is_file()

    def test_the_render_is_asked_for_the_candidates_own_range(
        self, tmp_path: Path
    ) -> None:
        renderer = RecordingRenderer()

        run(tmp_path, renderer=renderer)
        span = renderer.requests[0].span

        assert (span.start_s, span.end_s) == (120.0, 150.0)

    def test_detection_is_scoped_to_the_clip_not_the_source(
        self, tmp_path: Path
    ) -> None:
        """A detector free to answer beyond the span would make the cost of a
        clip depend on the length of the sermon it came from."""
        tracker = FakeSubjectTrackerPort()

        run(tmp_path, tracker=tracker)

        assert tracker.spans == [TimeSpan(120.0, 150.0)]


class TestTheDeclarations:
    """All four computed above the port, never reported by the adapter."""

    def test_low_confidence_tracking_is_not_reported_as_success(
        self, tmp_path: Path
    ) -> None:
        """A mostly-guessed reframe delivered as an ordinary success is the
        failure this axis exists to prevent -- the file plays fine."""
        every_sample_missed = FakeSubjectTrackerPort(misses=tuple(range(1000)))

        export = run(tmp_path, tracker=every_sample_missed)

        assert export.clip.tracking is TrackingConfidence.LOW_CONFIDENCE

    def test_a_tracked_clip_stays_well_tracked(self, tmp_path: Path) -> None:
        export = run(tmp_path)

        assert export.clip.tracking is TrackingConfidence.WELL_TRACKED

    def test_caption_coverage_comes_from_the_segments(self, tmp_path: Path) -> None:
        export = run(tmp_path, transcript=a_transcript(SegmentKind.UNCERTAIN))

        assert export.clip.captions is CaptionCoverage.INCLUDES_UNVERIFIED

    def test_segment_level_timing_is_declared_when_no_words_arrived(
        self, tmp_path: Path
    ) -> None:
        export = run(tmp_path)

        assert export.clip.subtitle_timing is SubtitleTimingSource.SEGMENT_LEVEL

    def test_quality_is_computed_against_this_profiles_target_width(
        self, tmp_path: Path
    ) -> None:
        """A 1920x1080 frame yields a 606-wide 9:16 crop, so a 1080-wide target
        upscales it -- and the operator reads that rather than watching for it."""
        export = run(tmp_path)

        assert export.clip.quality.kind is OutputQualityKind.UPSCALED
        assert export.clip.quality.factor > 1.0

    def test_the_source_range_survives_onto_the_clip(self, tmp_path: Path) -> None:
        """The only place the original coordinate survives -- everything inside
        the render is clip-local."""
        export = run(tmp_path)

        assert (export.clip.source_start_s, export.clip.source_end_s) == (120.0, 150.0)


class TestTheRefusalsThatComeFirst:
    def test_a_source_with_no_picture_is_refused_before_detection(
        self, tmp_path: Path
    ) -> None:
        tracker = FakeSubjectTrackerPort()

        with pytest.raises(FrameGeometryUnavailable):
            run(tmp_path, tracker=tracker, probe=a_probe(frame=None))

        assert tracker.spans == []

    def test_a_degenerate_frame_is_refused_before_detection(
        self, tmp_path: Path
    ) -> None:
        """crop_size_for is total and answers (0, 0) for a frame under two
        pixels -- an honest answer that has no quality. Refused here so
        quality_of never divides by a zero crop width."""
        tracker = FakeSubjectTrackerPort()

        with pytest.raises(FrameGeometryUnavailable):
            run(tmp_path, tracker=tracker, probe=a_probe(frame=FrameSize(1920, 1)))

        assert tracker.spans == []

    def test_a_build_that_cannot_track_is_refused_before_detection(
        self, tmp_path: Path
    ) -> None:
        """Asserting the type alone proves nothing here, and a mutation showed it.

        `UnavailableSubjectTrackerPort.detect` raises `TrackingUnavailable`
        itself, so a worker that ignored the declaration and called through would
        still surface the right exception -- having paid for the refusal, which on
        a vision adapter is most of the cost of the clip. The spy is what makes
        the ordering observable.
        """
        tracker = _SpyingUnavailableTracker()

        with pytest.raises(TrackingUnavailable) as caught:
            run(tmp_path, tracker=tracker)

        assert tracker.detect_calls == 0
        assert "install" in str(caught.value)

    def test_nothing_is_rendered_when_a_refusal_fires(self, tmp_path: Path) -> None:
        renderer = RecordingRenderer()

        with pytest.raises(FrameGeometryUnavailable):
            run(tmp_path, renderer=renderer, probe=a_probe(frame=None))

        assert renderer.requests == []

    def test_an_impossible_range_goes_through_the_guarded_use_case(
        self, tmp_path: Path
    ) -> None:
        """The worker does not re-implement the range guards. render_clip is the
        only way into the port, so its refusals are the worker's too."""
        with pytest.raises(ClipRangeInvalid):
            run(tmp_path, candidate=a_candidate(end_s=99_999.0))
