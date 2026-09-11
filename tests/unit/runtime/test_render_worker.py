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

from onevoicecut.domain.framing import (
    CropTrajectory,
    TimeSpan,
    TrackingConfidence,
    TrajectoryPolicy,
)
from onevoicecut.domain.generation import ClipCandidate, ScriptVariant
from onevoicecut.domain.ids import make_clip_id, make_job_id, make_media_id
from onevoicecut.domain.media import FrameSize, MediaProbe, SourceMedia
from onevoicecut.domain.rendering import (
    CaptionCoverage,
    ClipExport,
    RenderedClip,
    ClipState,
    DurationComplianceKind,
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
from onevoicecut.runtime.render_worker import (
    render_clip_for_candidate,
    render_clip_for_profile,
)
from onevoicecut.usecases.generate_artifacts import ScriptTarget
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

# [rev 5] A second vertical profile, narrower than PROFILE but at the same 9:16
# aspect. Exists to separate two axes that a single extra profile would
# conflate: it proves trajectory *sharing* (same aspect) independently of
# quality *divergence* (different target width) -- a profile that also changed
# aspect would leave open whether a shared trajectory was ever exercised.
NARROW_VERTICAL_PROFILE = RenderProfile(
    name="narrow_vertical",
    output=OutputSpec(width=540, height=960),
    safe_area=SafeArea(top=0.06, bottom=0.18, left=0.05, right=0.14),
    max_duration_s=90.0,
)

# [rev 5] A 1:1 profile -- its aspect genuinely differs from PROFILE's 9:16,
# which is what a differing-aspect scenario needs.
SQUARE_PROFILE = RenderProfile(
    name="square",
    output=OutputSpec(width=1080, height=1080),
    safe_area=SafeArea(top=0.04, bottom=0.10, left=0.04, right=0.04),
    max_duration_s=90.0,
)

# [rev 5] Three networks naming `vertical`, one naming `square` -- the shape
# the spec's own scenario uses: four networks, two distinct profiles.
SCRIPT_TARGETS_TWO_PROFILES = {
    "tiktok": ScriptTarget(
        name="tiktok", format="plain", duration_target_s=45.0, profile="vertical"
    ),
    "instagram": ScriptTarget(
        name="instagram", format="plain", duration_target_s=45.0, profile="vertical"
    ),
    "youtube": ScriptTarget(
        name="youtube", format="plain", duration_target_s=45.0, profile="vertical"
    ),
    "facebook": ScriptTarget(
        name="facebook", format="plain", duration_target_s=45.0, profile="square"
    ),
}
RENDER_PROFILES_TWO = {"vertical": PROFILE, "square": SQUARE_PROFILE}

# [rev 5] Two networks naming two profiles that share one aspect.
SCRIPT_TARGETS_SHARED_ASPECT = {
    "tiktok": ScriptTarget(
        name="tiktok", format="plain", duration_target_s=45.0, profile="vertical"
    ),
    "facebook": ScriptTarget(
        name="facebook",
        format="plain",
        duration_target_s=45.0,
        profile="narrow_vertical",
    ),
}
RENDER_PROFILES_SHARED_ASPECT = {
    "vertical": PROFILE,
    "narrow_vertical": NARROW_VERTICAL_PROFILE,
}


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


def a_candidate_for(*targets: str, end_s: float = 150.0) -> ClipCandidate:
    """A candidate whose variants name exactly the given networks -- the shape
    the fan-out tests need to control which profiles a candidate resolves to."""
    return ClipCandidate(
        start_s=120.0,
        end_s=end_s,
        hook="Hermanos, escuchen",
        quote="Un momento del sermon",
        rationale="El punto central",
        score=0.9,
        variants=tuple(
            ScriptVariant(
                target=target, format="plain", body=f"Hola {target}", duration_target_s=45.0
            )
            for target in targets
        ),
    )


def run_candidate(
    tmp_path: Path,
    *,
    tracker: FakeSubjectTrackerPort | UnavailableSubjectTrackerPort | None = None,
    probe: MediaProbe | None = None,
    transcript: Transcript | None = None,
    candidate: ClipCandidate | None = None,
    renderer: RecordingRenderer | None = None,
    storage: FakeTranscriptStoragePort | None = None,
    script_targets: dict[str, ScriptTarget] = SCRIPT_TARGETS_TWO_PROFILES,
    render_profiles: dict[str, RenderProfile] = RENDER_PROFILES_TWO,
) -> tuple[ClipExport, ...]:
    store = storage if storage is not None else FakeTranscriptStoragePort(tmp_path)
    store.save_transcript(transcript if transcript is not None else a_transcript())
    return render_clip_for_candidate(
        JOB_ID,
        CLIP_ID,
        candidate
        if candidate is not None
        else a_candidate_for("tiktok", "instagram", "youtube", "facebook"),
        media=a_media(tmp_path),
        probe=probe if probe is not None else a_probe(),
        tracker=tracker if tracker is not None else FakeSubjectTrackerPort(),
        renderer=renderer if renderer is not None else RecordingRenderer(),
        storage=store,
        job_dir=tmp_path,
        script_targets=script_targets,
        render_profiles=render_profiles,
    )


def rendered(export: ClipExport) -> RenderedClip:
    """The clip off a finished export.

    `ClipExport.clip` is optional so a refused render can be recorded at all, and
    `__post_init__` already refuses a `DONE` export without one -- so this
    narrows for the type checker rather than testing anything.
    """
    assert export.clip is not None
    return export.clip


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

        assert (tmp_path / "render" / "vertical" / (CLIP_ID + ".ass")).is_file()

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

        assert rendered(export).tracking is TrackingConfidence.LOW_CONFIDENCE

    def test_a_tracked_clip_stays_well_tracked(self, tmp_path: Path) -> None:
        export = run(tmp_path)

        assert rendered(export).tracking is TrackingConfidence.WELL_TRACKED

    def test_caption_coverage_comes_from_the_segments(self, tmp_path: Path) -> None:
        export = run(tmp_path, transcript=a_transcript(SegmentKind.UNCERTAIN))

        assert rendered(export).captions is CaptionCoverage.INCLUDES_UNVERIFIED

    def test_segment_level_timing_is_declared_when_no_words_arrived(
        self, tmp_path: Path
    ) -> None:
        export = run(tmp_path)

        assert rendered(export).subtitle_timing is SubtitleTimingSource.SEGMENT_LEVEL

    def test_quality_is_computed_against_this_profiles_target_width(
        self, tmp_path: Path
    ) -> None:
        """A 1920x1080 frame yields a 606-wide 9:16 crop, so a 1080-wide target
        upscales it -- and the operator reads that rather than watching for it."""
        export = run(tmp_path)

        assert rendered(export).quality.kind is OutputQualityKind.UPSCALED
        assert rendered(export).quality.factor > 1.0

    def test_the_source_range_survives_onto_the_clip(self, tmp_path: Path) -> None:
        """The only place the original coordinate survives -- everything inside
        the render is clip-local."""
        export = run(tmp_path)

        assert (rendered(export).source_start_s, rendered(export).source_end_s) == (120.0, 150.0)


class TestTheRefusalsThatComeFirst:
    """A refusal is recorded, not merely raised.

    The worker's own message reaches the server log and nowhere an operator
    looks -- a gap this project already has for the transcription worker and did
    not want to repeat. So every refusal becomes a `FAILED` export under the same
    clip-and-profile key a success would have used, naming what went wrong.

    It carries no clip, and that is what the reshaping was for. `RenderedClip`
    declares quality, subtitle timing, caption coverage and tracking with no
    defaults, and a render refused before ffmpeg was spawned has no honest value
    for any of them.
    """

    def test_a_source_with_no_picture_is_refused_before_detection(
        self, tmp_path: Path
    ) -> None:
        tracker = FakeSubjectTrackerPort()

        export = run(tmp_path, tracker=tracker, probe=a_probe(frame=None))

        assert export.state is ClipState.FAILED
        assert "FrameGeometryUnavailable" in (export.failure or "")
        assert tracker.spans == []

    def test_a_degenerate_frame_is_refused_before_detection(
        self, tmp_path: Path
    ) -> None:
        """crop_size_for is total and answers (0, 0) for a frame under two
        pixels -- an honest answer that has no quality. Refused here so
        quality_of never divides by a zero crop width."""
        tracker = FakeSubjectTrackerPort()

        export = run(tmp_path, tracker=tracker, probe=a_probe(frame=FrameSize(1920, 1)))

        assert export.state is ClipState.FAILED
        assert tracker.spans == []

    def test_a_build_that_cannot_track_is_refused_before_detection(
        self, tmp_path: Path
    ) -> None:
        """Asserting the error type alone proves nothing here, and a mutation
        showed it. `UnavailableSubjectTrackerPort.detect` raises
        `TrackingUnavailable` itself, so a worker that ignored the declaration
        and called through would record the identical failure -- having paid for
        it, which on a vision adapter is most of the cost of the clip. The spy is
        what makes the ordering observable.
        """
        tracker = _SpyingUnavailableTracker()

        export = run(tmp_path, tracker=tracker)

        assert tracker.detect_calls == 0
        assert export.state is ClipState.FAILED
        assert "TrackingUnavailable" in (export.failure or "")
        assert "install" in (export.failure or "")

    def test_a_failure_is_persisted_under_the_same_key_a_success_would_use(
        self, tmp_path: Path
    ) -> None:
        """So a caller polling one clip finds the refusal where it would have
        found the file, rather than finding nothing and having to guess."""
        storage = FakeTranscriptStoragePort(tmp_path)

        run(tmp_path, storage=storage, probe=a_probe(frame=None))
        stored = storage.load_clip_exports(JOB_ID, CLIP_ID)

        assert [export.state for export in stored] == [ClipState.FAILED]

    def test_a_failed_export_still_carries_what_the_operator_would_publish(
        self, tmp_path: Path
    ) -> None:
        """The variants are why the render was requested, and they do not stop
        being true because it was refused -- reconstructing them means re-running
        generation against a transcript that may have been re-stitched since."""
        export = run(tmp_path, probe=a_probe(frame=None))

        assert [variant.target for variant in export.variants] == ["tiktok"]

    def test_nothing_is_rendered_when_a_refusal_fires(self, tmp_path: Path) -> None:
        renderer = RecordingRenderer()

        run(tmp_path, renderer=renderer, probe=a_probe(frame=None))

        assert renderer.requests == []

    def test_an_impossible_range_is_refused_by_the_guarded_use_case(
        self, tmp_path: Path
    ) -> None:
        """The worker does not re-implement the range guards. `check_clip_range`
        is one definition with two call sites, so the port-side guarantee and
        this early refusal cannot drift."""
        export = run(tmp_path, candidate=a_candidate(end_s=99_999.0))

        assert export.state is ClipState.FAILED
        assert "ClipRangeInvalid" in (export.failure or "")


class TestProfileFanOut:
    """[rev 5] A candidate is rendered once per *distinct* render profile among
    its variants, never once per network. Dedup is on the profile, never the
    network -- three networks naming one profile share its single export."""

    def test_four_networks_resolving_to_two_profiles_produce_two_exports(
        self, tmp_path: Path
    ) -> None:
        exports = run_candidate(tmp_path)

        assert {export.profile for export in exports} == {"vertical", "square"}

    def test_three_networks_sharing_one_profile_land_on_its_single_export(
        self, tmp_path: Path
    ) -> None:
        exports = run_candidate(tmp_path)
        vertical = next(e for e in exports if e.profile == "vertical")

        assert {v.target for v in vertical.variants} == {
            "tiktok",
            "instagram",
            "youtube",
        }

    def test_the_other_profiles_export_carries_only_its_own_network(
        self, tmp_path: Path
    ) -> None:
        exports = run_candidate(tmp_path)
        square = next(e for e in exports if e.profile == "square")

        assert {v.target for v in square.variants} == {"facebook"}

    def test_dedup_is_on_the_profile_not_the_network(self, tmp_path: Path) -> None:
        """Three networks all naming `vertical` -- one export, not three."""
        exports = run_candidate(
            tmp_path, candidate=a_candidate_for("tiktok", "instagram", "youtube")
        )

        assert len(exports) == 1
        assert exports[0].profile == "vertical"


class TestDetectionIsInvariantAcrossProfiles:
    """[rev 5] `detect(media, span, sample_hz)` takes no aspect and no policy --
    it answers where a person was found in the source frame, which is the same
    answer whatever shape gets cropped around it. Aspect enters only at
    `build_trajectory`. Re-detecting per profile would multiply the one cost
    this pipeline lets model weights dominate, to obtain an identical answer --
    this is the unit's sharpest cost assertion."""

    def test_detect_runs_at_most_once_across_two_distinct_profiles(
        self, tmp_path: Path
    ) -> None:
        tracker = FakeSubjectTrackerPort()

        run_candidate(tmp_path, tracker=tracker)

        assert len(tracker.spans) == 1

    def test_detect_runs_at_most_once_across_three_profiles_two_sharing_an_aspect(
        self, tmp_path: Path
    ) -> None:
        tracker = FakeSubjectTrackerPort()

        run_candidate(
            tmp_path,
            tracker=tracker,
            candidate=a_candidate_for("tiktok", "facebook"),
            script_targets=SCRIPT_TARGETS_SHARED_ASPECT,
            render_profiles=RENDER_PROFILES_SHARED_ASPECT,
        )

        assert len(tracker.spans) == 1


def _counting_build_trajectory(
    monkeypatch: pytest.MonkeyPatch,
) -> list[TrajectoryPolicy]:
    """Wraps the real `build_trajectory` so a test can observe how many times
    -- and for which aspect -- it actually ran, without changing its answer.
    `render_worker` binds the name at import time, so patching by dotted
    string is what a caller going through the module's own name actually
    sees; the module is not asked to re-export the name for this."""
    from onevoicecut.usecases.plan_trajectory import (
        build_trajectory as real_build_trajectory,
    )

    policies_seen: list[TrajectoryPolicy] = []

    def spy(
        detections: tuple[SubjectDetection, ...],
        frame: FrameSize,
        span: TimeSpan,
        policy: TrajectoryPolicy,
    ) -> CropTrajectory:
        policies_seen.append(policy)
        return real_build_trajectory(detections, frame, span, policy)

    monkeypatch.setattr("onevoicecut.runtime.render_worker.build_trajectory", spy)
    return policies_seen


class TestTrajectoryIsKeyedByAspect:
    """[rev 5] Detection is invariant across profiles; trajectory planning is
    not. Two profiles agreeing on aspect must share the one trajectory built
    from the shared detection set, and a profile with a different aspect must
    get its own -- neither reused for the other."""

    def test_two_profiles_sharing_an_aspect_plan_one_trajectory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`vertical` and `narrow_vertical` both reduce to 9:16 -- only their
        target width differs -- so a caller keying by aspect must plan once."""
        policies_seen = _counting_build_trajectory(monkeypatch)

        run_candidate(
            tmp_path,
            candidate=a_candidate_for("tiktok", "facebook"),
            script_targets=SCRIPT_TARGETS_SHARED_ASPECT,
            render_profiles=RENDER_PROFILES_SHARED_ASPECT,
        )

        assert len(policies_seen) == 1

    def test_two_profiles_with_different_aspects_each_plan_their_own(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`vertical` is 9:16 and `square` is 1:1 -- genuinely different
        aspects, so neither trajectory may stand in for the other."""
        policies_seen = _counting_build_trajectory(monkeypatch)

        run_candidate(tmp_path, candidate=a_candidate_for("tiktok", "facebook"))

        assert len(policies_seen) == 2
        assert {(p.aspect_w, p.aspect_h) for p in policies_seen} == {(9, 16), (1, 1)}


class TestQualityIsDeclaredPerProfile:
    """[rev 5] Quality is `target_width / crop_width`, and the target is the
    profile's -- one clip cut from one crop can be native under a profile
    asking for fewer pixels and upscaled under one asking for more, and both
    statements are true at once. A single quality value per clip could only
    report one of them."""

    def test_one_clip_declares_native_for_one_profile_and_upscaled_for_another(
        self, tmp_path: Path
    ) -> None:
        """A 1920x1080 frame yields a 606-wide 9:16 crop: `narrow_vertical`'s
        540px target is native, `vertical`'s 1080px target upscales it."""
        exports = run_candidate(
            tmp_path,
            candidate=a_candidate_for("tiktok", "facebook"),
            script_targets=SCRIPT_TARGETS_SHARED_ASPECT,
            render_profiles=RENDER_PROFILES_SHARED_ASPECT,
        )
        narrow = rendered(next(e for e in exports if e.profile == "narrow_vertical"))
        wide = rendered(next(e for e in exports if e.profile == "vertical"))

        assert narrow.quality.kind is OutputQualityKind.NATIVE
        assert wide.quality.kind is OutputQualityKind.UPSCALED

    def test_each_exports_quality_is_computed_against_its_own_target_width(
        self, tmp_path: Path
    ) -> None:
        exports = run_candidate(
            tmp_path,
            candidate=a_candidate_for("tiktok", "facebook"),
            script_targets=SCRIPT_TARGETS_SHARED_ASPECT,
            render_profiles=RENDER_PROFILES_SHARED_ASPECT,
        )
        narrow = rendered(next(e for e in exports if e.profile == "narrow_vertical"))
        wide = rendered(next(e for e in exports if e.profile == "vertical"))

        assert round(narrow.quality.factor, 2) == 0.89
        assert round(wide.quality.factor, 2) == 1.78


class TestDurationCeilingIsDeclaredNotTrimmed:
    """[rev 5] A candidate past its profile's declared ceiling still renders at
    the candidate's own full range -- the overrun is declared, never cut, since
    which end to trim is an editorial judgement no rule here is positioned to
    make."""

    def test_an_over_length_candidate_renders_its_full_range(
        self, tmp_path: Path
    ) -> None:
        """PROFILE's ceiling is 90.0s; this candidate is 110s long."""
        renderer = RecordingRenderer()

        export = run(
            tmp_path, renderer=renderer, candidate=a_candidate(end_s=230.0)
        )

        assert export.state is ClipState.DONE
        assert (rendered(export).source_start_s, rendered(export).source_end_s) == (
            120.0,
            230.0,
        )
        assert renderer.requests[0].span.duration_s == 110.0

    def test_the_overrun_is_declared_with_its_magnitude(self, tmp_path: Path) -> None:
        export = run(tmp_path, candidate=a_candidate(end_s=230.0))

        assert rendered(export).duration.kind is DurationComplianceKind.OVER_CEILING
        assert rendered(export).duration.overrun_s == 20.0

    def test_a_candidate_within_the_ceiling_declares_no_overrun(
        self, tmp_path: Path
    ) -> None:
        export = run(tmp_path)

        assert rendered(export).duration.kind is DurationComplianceKind.WITHIN_CEILING
        assert rendered(export).duration.overrun_s == 0.0
