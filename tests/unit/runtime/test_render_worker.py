"""Orchestrating one clip's render, and the refusals recorded around it.

The worker is where every declaration an operator reads is assembled, and the
reason they are assembled *here* rather than reported by the adapter is that all
four are known before ffmpeg is spawned. A renderer that reported them could lie
about arithmetic it never ran.

**The tracking-capability refusal is about spending nothing.** A build with no
vision weights is discoverable before the expensive step, and the expensive step
is detection -- the one place model weights dominate. `_require_detection` runs
above `tracker.detect()` in `_render_profiles`'s whole-clip guard, so a build
that declares no support never pays for an answer it would throw away -- which is
why that test asserts on the tracker never being touched rather than on the
message.

**The frame-geometry refusal holds to the same discipline, and 13b-iii-c is
where that stopped being true by accident.** The degenerate-crop half depends
on the policy, so it reads as per-profile work belonging inside the loop --
which is below detection. It is not: every aspect the clip renders at is
resolved before the tracker is reached, so `_croppable_frames` proves them all
in the guard above. The tests below assert `tracker.spans == []`, which is what
task `13b.20` requires in those words, and the fixture ordered square-first is
the only one that distinguishes checking every aspect from checking the first.
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
from onevoicecut.runtime.render_worker import render_pending_exports
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

# [rev 5] Two distinct render profiles -- the shape the spec's own scenario
# uses: several networks resolving to a handful of profiles. `group_variants_
# by_profile`, which used to turn a candidate's networks into this shape, now
# lives in and is tested directly by `tests/unit/usecases/test_render_
# profiles.py`; these registries stay here because the render *loop* -- what
# `render_pending_exports` does once profiles are already resolved -- is still
# this module's own concern.
RENDER_PROFILES_TWO = {"vertical": PROFILE, "square": SQUARE_PROFILE}

# [rev 5] Two render profiles that share one aspect.
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
    """One profile, from a single `PENDING` export to a persisted result.

    Drives `render_pending_exports` -- the one production entrypoint -- rather
    than a single-profile path of its own: `render_clip_for_profile` and
    `_render` are gone, so the base-case proof this helper exists for now has
    to go through the same fan-out loop every render does, with a fan-out of
    one. `a_pending_export`'s defaults already carry `PROFILE`'s name and this
    candidate's own hook/quote/variant, so a one-target `pending` tuple is the
    whole difference from the old call.
    """
    store = storage if storage is not None else FakeTranscriptStoragePort(tmp_path)
    store.save_transcript(transcript if transcript is not None else a_transcript())
    resolved_candidate = candidate if candidate is not None else a_candidate()
    exports = render_pending_exports(
        JOB_ID,
        CLIP_ID,
        (
            a_pending_export(
                start_s=resolved_candidate.start_s, end_s=resolved_candidate.end_s
            ),
        ),
        media=a_media(tmp_path),
        probe=probe if probe is not None else a_probe(),
        tracker=tracker if tracker is not None else FakeSubjectTrackerPort(),
        renderer=renderer if renderer is not None else RecordingRenderer(),
        storage=store,
        job_dir=tmp_path,
        render_profiles={"vertical": PROFILE},
    )
    return exports[0]


def rendered(export: ClipExport) -> RenderedClip:
    """The clip off a finished export.

    `ClipExport.clip` is optional so a refused render can be recorded at all, and
    `__post_init__` already refuses a `DONE` export without one -- so this
    narrows for the type checker rather than testing anything.
    """
    assert export.clip is not None
    return export.clip


class TestTheRenderClaim:
    """`13b.30`: whatever `ClipExport` needs to make render liveness derivable
    rather than counted -- the render side of `worker.run_job`'s pid-and-
    heartbeat claim, written before any real work starts."""

    def test_the_clip_is_claimed_before_anything_else_runs(
        self, tmp_path: Path
    ) -> None:
        storage = FakeTranscriptStoragePort(tmp_path)

        run(tmp_path, storage=storage)

        assert "write_render_claim" in storage.calls
        assert storage.render_claim_at(JOB_ID, CLIP_ID) is not None

    def test_the_claim_precedes_even_a_refused_range(self, tmp_path: Path) -> None:
        """The claim step reads no further than each export's own state, so a
        clip whose exports disagree on their range is still claimed before
        that disagreement is ever detected."""
        storage = FakeTranscriptStoragePort(tmp_path)

        run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical", start_s=120.0, end_s=150.0),
                a_pending_export(profile="square", start_s=300.0, end_s=330.0),
            ),
            storage=storage,
        )

        assert storage.render_claim_at(JOB_ID, CLIP_ID) is not None

    def test_an_export_is_written_rendering_before_it_is_written_done(
        self, tmp_path: Path
    ) -> None:
        """Mirrors `worker.run_job`'s pid-then-finish ordering: the same
        record is saved twice, once mid-claim and once with the outcome."""
        storage = FakeTranscriptStoragePort(tmp_path)

        run(tmp_path, storage=storage)

        assert storage.calls.count("save_clip_export:vertical") == 2

    def test_an_abandoned_rendering_export_is_re_claimed_on_pickup(
        self, tmp_path: Path
    ) -> None:
        """A `RENDERING` export the render drain judged abandoned is still
        claimable -- re-picking it up without refreshing the claim would read
        as abandoned again on the very next sweep."""
        storage = FakeTranscriptStoragePort(tmp_path)
        stale_export = replace(a_pending_export(), state=ClipState.RENDERING)
        storage.save_transcript(a_transcript())

        exports = render_pending_exports(
            JOB_ID,
            CLIP_ID,
            (stale_export,),
            media=a_media(tmp_path),
            probe=a_probe(),
            tracker=FakeSubjectTrackerPort(),
            renderer=RecordingRenderer(),
            storage=storage,
            job_dir=tmp_path,
            render_profiles={"vertical": PROFILE},
        )

        assert exports[0].state is ClipState.DONE
        assert storage.render_claim_at(JOB_ID, CLIP_ID) is not None


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
        """Task `13b.20` states the requirement as "never calling the tracker",
        and the design's sequence diagram puts this `alt` branch above
        `capabilities()` and `detect()`. A source with no picture is
        profile-independent, so nothing about the fan-out makes this cost
        unavoidable."""
        tracker = FakeSubjectTrackerPort()

        export = run(tmp_path, tracker=tracker, probe=a_probe(frame=None))

        assert export.state is ClipState.FAILED
        assert "FrameGeometryUnavailable" in (export.failure or "")
        assert tracker.spans == []

    def test_a_degenerate_frame_is_refused_before_detection(
        self, tmp_path: Path
    ) -> None:
        """`crop_size_for` is total and answers a non-positive size for a frame
        under two pixels -- an honest answer that has no quality, refused so
        `quality_of` never divides by a zero crop width.

        This one *is* per aspect, which is why it is easy to get wrong: the
        answer depends on the policy, and the policy comes from the profile. It
        is still knowable before detection, because every aspect the clip will
        be rendered at is resolved before the tracker is ever reached."""
        tracker = FakeSubjectTrackerPort()

        export = run(tmp_path, tracker=tracker, probe=a_probe(frame=FrameSize(1920, 1)))

        assert export.state is ClipState.FAILED
        assert tracker.spans == []

    def test_an_aspect_that_is_not_the_first_is_still_checked_before_detection(
        self, tmp_path: Path
    ) -> None:
        """`FrameSize(1920, 2)` crops to (2, 2) at 1:1 and to (0, 2) at 9:16, so
        the square target passes and the vertical one cannot. Ordered square
        first, a guard that inspected only the leading target would find nothing
        wrong and pay for detection before the loop refused -- which is exactly
        how this check ended up below detection in the first place."""
        tracker = FakeSubjectTrackerPort()

        exports = run_pending(
            tmp_path,
            (
                a_pending_export(profile="square"),
                a_pending_export(profile="vertical"),
            ),
            tracker=tracker,
            probe=a_probe(frame=FrameSize(1920, 2)),
        )

        assert {e.state for e in exports} == {ClipState.FAILED}
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


class TestDetectionIsInvariantAcrossProfiles:
    """[rev 5] `detect(media, span, sample_hz)` takes no aspect and no policy --
    it answers where a person was found in the source frame, which is the same
    answer whatever shape gets cropped around it. Aspect enters only at
    `build_trajectory`. Re-detecting per profile would multiply the one cost
    this pipeline lets model weights dominate, to obtain an identical answer --
    this is the unit's sharpest cost assertion.

    Driven through `run_pending` -- `pending` here is exactly what a caller
    grouping a candidate's variants with
    `usecases.render_profiles.group_variants_by_profile` would have written,
    but that grouping is that function's own concern and its own tests; this
    class is about what `_render_profiles` does once profiles are already
    resolved.
    """

    def test_detect_runs_at_most_once_across_two_distinct_profiles(
        self, tmp_path: Path
    ) -> None:
        tracker = FakeSubjectTrackerPort()

        run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical", variants=(a_variant("tiktok"),)),
                a_pending_export(profile="square", variants=(a_variant("facebook"),)),
            ),
            tracker=tracker,
        )

        assert len(tracker.spans) == 1

    def test_detect_runs_at_most_once_across_three_profiles_two_sharing_an_aspect(
        self, tmp_path: Path
    ) -> None:
        tracker = FakeSubjectTrackerPort()

        run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical", variants=(a_variant("tiktok"),)),
                a_pending_export(
                    profile="narrow_vertical", variants=(a_variant("facebook"),)
                ),
            ),
            tracker=tracker,
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

        run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical", variants=(a_variant("tiktok"),)),
                a_pending_export(
                    profile="narrow_vertical", variants=(a_variant("facebook"),)
                ),
            ),
            render_profiles=RENDER_PROFILES_SHARED_ASPECT,
        )

        assert len(policies_seen) == 1

    def test_two_profiles_with_different_aspects_each_plan_their_own(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`vertical` is 9:16 and `square` is 1:1 -- genuinely different
        aspects, so neither trajectory may stand in for the other."""
        policies_seen = _counting_build_trajectory(monkeypatch)

        run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical", variants=(a_variant("tiktok"),)),
                a_pending_export(profile="square", variants=(a_variant("facebook"),)),
            ),
        )

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
        exports = run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical", variants=(a_variant("tiktok"),)),
                a_pending_export(
                    profile="narrow_vertical", variants=(a_variant("facebook"),)
                ),
            ),
            render_profiles=RENDER_PROFILES_SHARED_ASPECT,
        )
        narrow = rendered(next(e for e in exports if e.profile == "narrow_vertical"))
        wide = rendered(next(e for e in exports if e.profile == "vertical"))

        assert narrow.quality.kind is OutputQualityKind.NATIVE
        assert wide.quality.kind is OutputQualityKind.UPSCALED

    def test_each_exports_quality_is_computed_against_its_own_target_width(
        self, tmp_path: Path
    ) -> None:
        exports = run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical", variants=(a_variant("tiktok"),)),
                a_pending_export(
                    profile="narrow_vertical", variants=(a_variant("facebook"),)
                ),
            ),
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


def a_variant(target: str) -> ScriptVariant:
    """One network's own variant -- the shape a `PENDING` export's `variants`
    tuple is built from when a test wants to name more than one network."""
    return ScriptVariant(
        target=target, format="plain", body=f"Hola {target}", duration_target_s=45.0
    )


def a_pending_export(
    *,
    profile: str = "vertical",
    variants: tuple[ScriptVariant, ...] | None = None,
    title: str = "Hermanos, escuchen",
    description: str = "Un momento del sermon",
    start_s: float = 120.0,
    end_s: float = 150.0,
) -> ClipExport:
    """What the web process is specified to write before spawning this worker
    -- one `PENDING` export per distinct profile, already carrying its own
    range. The entrypoint's whole job is to read records shaped like this."""
    return ClipExport(
        job_id=JOB_ID,
        clip_id=CLIP_ID,
        profile=profile,
        source_start_s=start_s,
        source_end_s=end_s,
        title=title,
        description=description,
        variants=(
            variants
            if variants is not None
            else (
                ScriptVariant(
                    target="tiktok", format="plain", body="Hola", duration_target_s=45.0
                ),
            )
        ),
        state=ClipState.PENDING,
        clip=None,
        failure=None,
    )


def run_pending(
    tmp_path: Path,
    pending: tuple[ClipExport, ...],
    *,
    tracker: FakeSubjectTrackerPort | UnavailableSubjectTrackerPort | None = None,
    probe: MediaProbe | None = None,
    transcript: Transcript | None = None,
    renderer: RecordingRenderer | None = None,
    storage: FakeTranscriptStoragePort | None = None,
    render_profiles: dict[str, RenderProfile] = RENDER_PROFILES_TWO,
) -> tuple[ClipExport, ...]:
    store = storage if storage is not None else FakeTranscriptStoragePort(tmp_path)
    store.save_transcript(transcript if transcript is not None else a_transcript())
    return render_pending_exports(
        JOB_ID,
        CLIP_ID,
        pending,
        media=a_media(tmp_path),
        probe=probe if probe is not None else a_probe(),
        tracker=tracker if tracker is not None else FakeSubjectTrackerPort(),
        renderer=renderer if renderer is not None else RecordingRenderer(),
        storage=store,
        job_dir=tmp_path,
        render_profiles=render_profiles,
    )


class TestRenderPendingExportsReadsWhatWasAlreadyDecided:
    """[13b.19] The entrypoint's own orchestration: render exactly the
    profiles a `PENDING` export already names, never re-derive them."""

    def test_it_renders_the_profile_a_pending_export_names(
        self, tmp_path: Path
    ) -> None:
        exports = run_pending(tmp_path, (a_pending_export(profile="vertical"),))

        assert [e.profile for e in exports] == ["vertical"]
        assert exports[0].state is ClipState.DONE

    def test_two_pending_exports_render_two_profiles(self, tmp_path: Path) -> None:
        exports = run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical"),
                a_pending_export(profile="square"),
            ),
        )

        assert {e.profile for e in exports} == {"vertical", "square"}
        assert all(e.state is ClipState.DONE for e in exports)

    def test_the_range_rendered_is_the_one_the_export_already_carried(
        self, tmp_path: Path
    ) -> None:
        exports = run_pending(
            tmp_path,
            (a_pending_export(profile="vertical", start_s=200.0, end_s=230.0),),
        )

        assert rendered(exports[0]).source_start_s == 200.0
        assert rendered(exports[0]).source_end_s == 230.0

    def test_title_and_description_travel_from_the_pending_export(
        self, tmp_path: Path
    ) -> None:
        exports = run_pending(
            tmp_path,
            (
                a_pending_export(
                    profile="vertical", title="Otro titulo", description="Otra cita"
                ),
            ),
        )

        assert exports[0].title == "Otro titulo"
        assert exports[0].description == "Otra cita"

    def test_it_does_not_re_derive_the_profile_from_the_variants(
        self, tmp_path: Path
    ) -> None:
        """A mutation that went back to resolving `SCRIPT_TARGETS[variant.
        target]` instead of reading `export.profile` would refuse this clip,
        because no script target maps this network to anything. Reading the
        profile straight off the export renders it regardless."""
        exports = run_pending(
            tmp_path,
            (
                a_pending_export(
                    profile="vertical",
                    variants=(
                        ScriptVariant(
                            target="a-network-no-script-target-maps",
                            format="plain",
                            body="Hola",
                            duration_target_s=45.0,
                        ),
                    ),
                ),
            ),
        )

        assert exports[0].state is ClipState.DONE

    def test_detection_runs_once_across_two_pending_profiles(
        self, tmp_path: Path
    ) -> None:
        tracker = FakeSubjectTrackerPort()

        run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical"),
                a_pending_export(profile="square"),
            ),
            tracker=tracker,
        )

        assert len(tracker.spans) == 1

    def test_a_profile_no_longer_in_the_registry_fails_only_that_export(
        self, tmp_path: Path
    ) -> None:
        """The registry can be edited between the request and the render.
        One export naming a profile that vanished from it must not block a
        sibling export naming one that is still there."""
        exports = run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical"),
                a_pending_export(profile="a-profile-nobody-configured"),
            ),
        )

        by_profile = {e.profile: e for e in exports}
        assert by_profile["vertical"].state is ClipState.DONE
        assert by_profile["a-profile-nobody-configured"].state is ClipState.FAILED
        assert "RenderProfileInvalid" in (
            by_profile["a-profile-nobody-configured"].failure or ""
        )

    def test_a_failed_resolution_still_carries_the_requested_range(
        self, tmp_path: Path
    ) -> None:
        """Even a render that never got past resolving its profile name has a
        legitimate range to record -- read straight off the PENDING export,
        never invented."""
        exports = run_pending(
            tmp_path,
            (a_pending_export(profile="not-configured", start_s=5.0, end_s=15.0),),
        )

        assert exports[0].source_start_s == 5.0
        assert exports[0].source_end_s == 15.0

    def test_an_empty_pending_tuple_yields_no_exports(self, tmp_path: Path) -> None:
        assert run_pending(tmp_path, ()) == ()


class TestOneClipIdIsOneRange:
    """A clip id names a range, and every export written under it must agree
    on which one. Reading the span off whichever record happened to be first
    would let two disagreeing rows render as though they had never disagreed
    -- the silent degradation `ClipExport.__post_init__` already refuses
    between an export and the clip it carries, on the one axis that check
    cannot see because it only ever inspects one record at a time.
    """

    def test_pending_exports_that_disagree_on_the_range_are_all_refused(
        self, tmp_path: Path
    ) -> None:
        exports = run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical", start_s=120.0, end_s=150.0),
                a_pending_export(profile="square", start_s=300.0, end_s=330.0),
            ),
        )

        assert {e.state for e in exports} == {ClipState.FAILED}
        assert {e.profile for e in exports} == {"vertical", "square"}
        for export in exports:
            assert "CorruptedRecord" in (export.failure or "")

    def test_the_disagreement_is_refused_before_anything_is_detected(
        self, tmp_path: Path
    ) -> None:
        """The whole reason this check sits above the loop: detection is the
        one step model weights dominate, and a contradictory request is
        discoverable without spending any of it."""
        tracker = FakeSubjectTrackerPort()

        run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical", start_s=120.0, end_s=150.0),
                a_pending_export(profile="square", start_s=300.0, end_s=330.0),
            ),
            tracker=tracker,
        )

        assert tracker.spans == []

    def test_a_disagreement_on_the_end_alone_is_still_a_disagreement(
        self, tmp_path: Path
    ) -> None:
        """Two exports sharing a start prove more than two sharing nothing: a
        check that compared only the start would pass the sibling fixture
        above and still cut one profile's clip short by thirty seconds."""
        exports = run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical", start_s=120.0, end_s=150.0),
                a_pending_export(profile="square", start_s=120.0, end_s=180.0),
            ),
        )

        assert {e.state for e in exports} == {ClipState.FAILED}

    def test_exports_agreeing_on_the_range_are_rendered(self, tmp_path: Path) -> None:
        exports = run_pending(
            tmp_path,
            (
                a_pending_export(profile="vertical", start_s=300.0, end_s=330.0),
                a_pending_export(profile="square", start_s=300.0, end_s=330.0),
            ),
        )

        assert all(e.state is ClipState.DONE for e in exports)
