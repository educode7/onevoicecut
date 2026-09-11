"""Assembling one clip's render, and refusing before the expensive step.

    python -m onevoicecut.runtime.render_worker --job-id <id> --clip-id <id>

A second composition root, like `worker.py`: a render is a separate short-lived
process, and render state lives per clip rather than on the job record.

**The entrypoint reads a decision already made, rather than making a second
one.** design.md's sequence diagram is binding: the web process mints the clip
id and writes one `PENDING` `ClipExport` per distinct profile before this
process is ever spawned. `main` resolves `--clip-id` to exactly those `PENDING`
records and renders the profiles they name -- never re-deriving the profile set
from a candidate's variants, because the render-profile registry can be edited
between the request and the render, and re-deriving here would silently change
what gets delivered from what an operator already saw acknowledged.

**Every declaration an operator reads is assembled here, above the port.** All
four are known before ffmpeg is spawned -- quality from the crop and the target,
subtitle timing and coverage from the segments, tracking from the trajectory --
so a renderer able to report them could contradict arithmetic it never ran.

**Both refusals come before detection, and the ordering is the point.** Detection
is the one step model weights dominate, and a source with no picture or a build
with no vision extras is discoverable without spending any of it. A worker that
detected first and refused afterwards would be equally correct and would have
paid for an answer it threw away.

**This worker writes the `.ass`, and 13b-i left that to it deliberately.** The
render adapter writes the `.cmds` file, which its request determines completely,
and then refuses to spawn without a subtitle document. It cannot write one
itself: `render_ass` needs a `RenderProfile` and a `RenderRequest` carries only
an `OutputSpec`. This is the caller that has the profile.
"""

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from onevoicecut.adapters.ffmpeg.extractor import FfmpegAudioExtractor
from onevoicecut.adapters.ffmpeg.subtitles import render_ass
from onevoicecut.adapters.ffmpeg.video_render import FfmpegVideoRenderer
from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    RENDER_DIRNAME,
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.errors import (
    CorruptedRecord,
    DomainError,
    FrameGeometryUnavailable,
    TrackingUnavailable,
)
from onevoicecut.domain.framing import (
    CropTrajectory,
    TimeSpan,
    TrajectoryPolicy,
    crop_size_for,
)
from onevoicecut.domain.generation import ScriptVariant
from onevoicecut.domain.ids import (
    ClipId,
    InvalidIdError,
    JobId,
    make_clip_id,
    make_job_id,
)
from onevoicecut.domain.media import FrameSize, MediaProbe, SourceMedia
from onevoicecut.domain.rendering import (
    CaptionCoverage,
    ClipExport,
    ClipState,
    RenderedClip,
    RenderProfile,
    SubtitleCue,
    SubtitleTimingSource,
    aspect_of,
    duration_compliance_of,
    quality_of,
)
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.capabilities import DetectionSupport, TrackerCapabilities
from onevoicecut.ports.subject_tracker import SubjectDetection, SubjectTrackerPort
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.ports.video_render import RenderRequest, VideoRenderPort
from onevoicecut.usecases.build_subtitle_cues import build_subtitle_cues
from onevoicecut.usecases.plan_trajectory import build_trajectory
from onevoicecut.usecases.render_clip import (
    DEFAULT_MAX_CLIP_SECONDS,
    check_clip_range,
    render_clip,
)
from onevoicecut.usecases.render_profiles import RENDER_PROFILES, resolve_render_profiles

ExtractorFactory = Callable[[Path, JobId], AudioExtractorPort]

# design.md's figure: four samples a second, and the trajectory keeps one
# keyframe per sample. Named here rather than spelled at the call site, where a
# silent change would alter both the cost of every clip and the confidence ratio.
DEFAULT_SAMPLE_HZ = 4.0

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNUSABLE = 2


def _range_disagreement(pending: tuple[ClipExport, ...]) -> CorruptedRecord | None:
    """One clip id names one range, and the set has to be read to see it.

    `ClipExport.__post_init__` already refuses an export whose carried clip
    disagrees with its own range, but it inspects one record at a time and so
    cannot see this: two rows, each internally consistent, contradicting each
    other about which seconds of the sermon the operator asked for. Taking the
    span off whichever happened to be first would render both profiles against
    one of the two ranges and record the other as though it had been honoured.
    """
    ranges = {(export.source_start_s, export.source_end_s) for export in pending}
    if len(ranges) <= 1:
        return None
    return CorruptedRecord(
        f"the pending exports for this clip disagree on its range "
        f"{sorted(ranges)}; a clip id names one span, so there is no way to "
        f"choose between them that is not an invention"
    )


def render_pending_exports(
    job_id: JobId,
    clip_id: ClipId,
    pending: tuple[ClipExport, ...],
    *,
    media: SourceMedia,
    probe: MediaProbe,
    tracker: SubjectTrackerPort,
    renderer: VideoRenderPort,
    storage: TranscriptStoragePort,
    job_dir: Path,
    render_profiles: Mapping[str, RenderProfile] = RENDER_PROFILES,
    sample_hz: float = DEFAULT_SAMPLE_HZ,
    max_clip_seconds: float = DEFAULT_MAX_CLIP_SECONDS,
) -> tuple[ClipExport, ...]:
    """Renders exactly the profiles `pending` already names -- never re-derived
    from a candidate's variants.

    `pending` is one `PENDING` `ClipExport` per distinct profile, written by
    the web process before this entrypoint was ever spawned. The render-profile
    registry can be edited between that request and this render, so re-deriving
    the profile set here from the variants would let an edit silently change
    the fan-out from what an operator already saw acknowledged at `202`.
    Reading `export.profile` off each record is what keeps the two moments in
    agreement -- and it is also why this function needs no `script_targets`
    parameter at all: a network was already resolved to a profile once, by
    whoever wrote `pending`.
    `usecases.render_profiles.group_variants_by_profile` is where that
    resolution happens, for the caller that writes `pending` in the first
    place; this function never repeats it.

    Runs `_render_profiles`, the one render-loop implementation this module
    carries.
    """
    disagreement = _range_disagreement(pending)
    if disagreement is not None:
        return tuple(
            _record(
                _failed(
                    job_id,
                    clip_id,
                    title=export.title,
                    description=export.description,
                    profile_name=export.profile,
                    variants=export.variants,
                    source_start_s=export.source_start_s,
                    source_end_s=export.source_end_s,
                    error=disagreement,
                ),
                storage=storage,
            )
            for export in pending
        )

    targets: list[_ProfileTarget] = []
    exports: list[ClipExport] = []
    for export in pending:
        try:
            profile = resolve_render_profiles(export.profile, registry=render_profiles)[0]
        except DomainError as error:
            exports.append(
                _record(
                    _failed(
                        job_id,
                        clip_id,
                        title=export.title,
                        description=export.description,
                        profile_name=export.profile,
                        variants=export.variants,
                        source_start_s=export.source_start_s,
                        source_end_s=export.source_end_s,
                        error=error,
                    ),
                    storage=storage,
                )
            )
            continue
        targets.append(
            _ProfileTarget(
                profile=profile,
                variants=export.variants,
                title=export.title,
                description=export.description,
            )
        )

    if not targets:
        # Every named profile failed to resolve -- a registry edited out from
        # under an already-written PENDING set. Nothing left to share a
        # whole-clip guard over, so there is no detection to hoist above.
        return tuple(exports)

    span = TimeSpan(pending[0].source_start_s, pending[0].source_end_s)
    exports.extend(
        _render_profiles(
            job_id,
            clip_id,
            tuple(targets),
            span=span,
            media=media,
            probe=probe,
            tracker=tracker,
            renderer=renderer,
            storage=storage,
            job_dir=job_dir,
            sample_hz=sample_hz,
            max_clip_seconds=max_clip_seconds,
        )
    )
    return tuple(exports)


@dataclass(frozen=True, slots=True)
class _ProfileTarget:
    """One profile's worth of what `_render_profiles` needs, already resolved.

    Not exported: `render_pending_exports` is `_render_profiles`'s one caller,
    reading a name straight off a `PENDING` record after resolving it against
    the current registry -- there is only one shape building this now.
    """

    profile: RenderProfile
    variants: tuple[ScriptVariant, ...]
    title: str
    description: str


def _render_profiles(
    job_id: JobId,
    clip_id: ClipId,
    targets: tuple[_ProfileTarget, ...],
    *,
    span: TimeSpan,
    media: SourceMedia,
    probe: MediaProbe,
    tracker: SubjectTrackerPort,
    renderer: VideoRenderPort,
    storage: TranscriptStoragePort,
    job_dir: Path,
    sample_hz: float,
    max_clip_seconds: float,
) -> tuple[ClipExport, ...]:
    """One clip, already resolved to its per-profile targets, rendered once.

    The whole-clip guards -- the range, the tracking capability, detection,
    the transcript's cues -- run once regardless of how many targets follow; a
    refusal here fails every target identically, recorded once each under its
    own key. Trajectory planning is the one step that is not invariant: it is
    cached per distinct aspect, never per profile, so targets sharing an
    aspect share the one trajectory built from the shared detection set.

    `render_pending_exports` is this function's one caller -- one render-loop
    implementation, not two orchestration bodies free to drift apart.
    """
    try:
        check_clip_range(span, probe, max_clip_seconds=max_clip_seconds)
        frames = _croppable_frames(probe, targets, clip_id)
        _require_detection(tracker)
        detections = tracker.detect(media, span, sample_hz=sample_hz)
        transcript = storage.load_transcript(job_id)
        segments = () if transcript is None else transcript.segments
        cues, timing, coverage = build_subtitle_cues(segments, span)
    except DomainError as error:
        return tuple(
            _record(
                _failed(
                    job_id,
                    clip_id,
                    title=target.title,
                    description=target.description,
                    profile_name=target.profile.name,
                    variants=target.variants,
                    source_start_s=span.start_s,
                    source_end_s=span.end_s,
                    error=error,
                ),
                storage=storage,
            )
            for target in targets
        )

    render_dir = job_dir / RENDER_DIRNAME
    trajectories: dict[tuple[int, int], CropTrajectory] = {}
    exports: list[ClipExport] = []

    for target in targets:
        try:
            aspect = aspect_of(target.profile)
            if aspect not in trajectories:
                policy = TrajectoryPolicy(aspect_w=aspect[0], aspect_h=aspect[1])
                trajectories[aspect] = build_trajectory(
                    detections, frames[aspect], span, policy
                )
            export = _export_from_trajectory(
                job_id,
                clip_id,
                title=target.title,
                description=target.description,
                profile=target.profile,
                span=span,
                probe=probe,
                trajectory=trajectories[aspect],
                cues=cues,
                timing=timing,
                coverage=coverage,
                variants=target.variants,
                media=media,
                renderer=renderer,
                storage=storage,
                render_dir=render_dir,
                max_clip_seconds=max_clip_seconds,
            )
        except DomainError as error:
            export = _record(
                _failed(
                    job_id,
                    clip_id,
                    title=target.title,
                    description=target.description,
                    profile_name=target.profile.name,
                    variants=target.variants,
                    source_start_s=span.start_s,
                    source_end_s=span.end_s,
                    error=error,
                ),
                storage=storage,
            )
        exports.append(export)

    return tuple(exports)


def _export_from_trajectory(
    job_id: JobId,
    clip_id: ClipId,
    *,
    title: str,
    description: str,
    profile: RenderProfile,
    span: TimeSpan,
    probe: MediaProbe,
    trajectory: CropTrajectory,
    cues: tuple[SubtitleCue, ...],
    timing: SubtitleTimingSource,
    coverage: CaptionCoverage,
    variants: tuple[ScriptVariant, ...],
    media: SourceMedia,
    renderer: VideoRenderPort,
    storage: TranscriptStoragePort,
    render_dir: Path,
    max_clip_seconds: float,
) -> ClipExport:
    """Cues, a trajectory and a profile become a file, then a persisted export.

    Called from `_render_profiles`'s loop, once per target -- several targets
    sharing an aspect call this with the identical `trajectory` object, so a
    shared crop is computed once and reused rather than rebuilt per profile.
    `title`, `description` and `variants` travel from the `PENDING` export
    `render_pending_exports` resolved this target from; `variants` is already
    narrowed to the subset that named this profile before it reaches here.
    """
    # [rev 5] Namespaced by profile, not flat: two distinct profiles for one
    # clip now produce two files, and the adapter derives the clip id -- which
    # `build_render_argv` validates as a ULID -- from `dest.stem`. The profile
    # can only live in the directory, never in the stem, or the id validation
    # this stem sharing exists for would refuse it.
    profile_dir = render_dir / profile.name
    profile_dir.mkdir(parents=True, exist_ok=True)
    # Written before the render is dispatched, because the adapter refuses to
    # spawn without it -- a precondition rather than a courtesy.
    (profile_dir / f"{clip_id}.ass").write_text(
        render_ass(cues, profile=profile), encoding="utf-8", newline="\n"
    )

    rendered = render_clip(
        RenderRequest(
            media=media,
            span=span,
            trajectory=trajectory,
            cues=cues,
            output=profile.output,
        ),
        renderer=renderer,
        probe=probe,
        dest=profile_dir / f"{clip_id}.mp4",
        max_clip_seconds=max_clip_seconds,
    )

    export = ClipExport(
        job_id=job_id,
        clip_id=clip_id,
        failure=None,
        clip=RenderedClip(
            clip_id=clip_id,
            job_id=job_id,
            path=rendered.path,
            source_start_s=span.start_s,
            source_end_s=span.end_s,
            quality=quality_of(trajectory.keyframes[0].rect, profile.output),
            subtitle_timing=timing,
            captions=coverage,
            tracking=trajectory.tracking,
            duration=duration_compliance_of(span, profile),
        ),
        profile=profile.name,
        source_start_s=span.start_s,
        source_end_s=span.end_s,
        title=title,
        description=description,
        variants=variants,
        state=ClipState.DONE,
    )
    return _record(export, storage=storage)


def _croppable_frames(
    probe: MediaProbe, targets: tuple[_ProfileTarget, ...], clip_id: ClipId
) -> dict[tuple[int, int], FrameSize]:
    """Every aspect this clip will be cut at, proved croppable before detection.

    Task `13b.20` states the requirement as "never calling the tracker", and the
    design's sequence diagram puts this refusal above `capabilities()` and
    `detect()`. The fan-out made that easy to lose: the degenerate-crop half
    depends on the policy, the policy comes from the profile, and a check that
    needs a profile reads as one belonging inside the per-profile loop -- which
    is after detection has already been paid for.

    It does not belong there, because the aspects are not discovered by
    iterating. Every profile this clip renders under is resolved before the
    tracker is reached, so the whole set of policies is knowable up front, and
    a source that cannot be cropped at any of them is discoverable without
    spending the one step model weights dominate.

    Refusing on the *first* unusable aspect rather than per aspect is the same
    rule `resolve_render_profiles` applies to an unmeasured profile: an operator
    who asked for four destinations and would get two files learns it now, not
    by counting files afterwards.
    """
    frames: dict[tuple[int, int], FrameSize] = {}
    for target in targets:
        aspect = aspect_of(target.profile)
        if aspect not in frames:
            frames[aspect] = _croppable_frame(
                probe, TrajectoryPolicy(aspect_w=aspect[0], aspect_h=aspect[1]), clip_id
            )
    return frames


def _croppable_frame(
    probe: MediaProbe, policy: TrajectoryPolicy, clip_id: ClipId
) -> FrameSize:
    """A picture, and one large enough to cut a window out of at this aspect.

    Two conditions and one error, because the operator's answer is the same for
    both: this source cannot be reframed. `crop_size_for` is total and answers a
    non-positive size for a frame under two pixels -- an honest answer that has
    no quality -- so the refusal belongs here, before `quality_of` would divide
    by a zero crop width.
    """
    frame = probe.frame
    if frame is None:
        raise FrameGeometryUnavailable(
            f"clip {clip_id} cannot be reframed: the source reports no picture, "
            f"so there is no geometry to crop toward"
        )

    crop_w, crop_h = crop_size_for(frame, policy)
    if crop_w <= 0 or crop_h <= 0:
        raise FrameGeometryUnavailable(
            f"clip {clip_id} cannot be reframed: a {frame.width}x{frame.height} "
            f"frame yields a {crop_w}x{crop_h} crop, which is not a window"
        )
    return frame


def _require_detection(tracker: SubjectTrackerPort) -> None:
    """Read the declaration rather than calling and catching.

    The capability exists so a clip can be skipped before anything is spent.
    Letting `detect` raise would be equally safe and would have paid for the
    refusal, which on a vision adapter is most of the cost of the clip.
    """
    capabilities = tracker.capabilities()
    if capabilities.detection is not DetectionSupport.AVAILABLE:
        raise TrackingUnavailable(
            f"tracker {capabilities.tracker_id} declares "
            f"{capabilities.detection.value}; install the vision extras and let "
            f"the weights download, or choose another tracker"
        )


def _failed(
    job_id: JobId,
    clip_id: ClipId,
    *,
    title: str,
    description: str,
    profile_name: str,
    variants: tuple[ScriptVariant, ...],
    source_start_s: float,
    source_end_s: float,
    error: DomainError,
) -> ClipExport:
    """A refusal, recorded with no clip and with the type that caused it.

    The type name leads the message because the operator's next move depends on
    it and not on the prose: `ClipRangeInvalid` and `FrameGeometryUnavailable`
    fail identically on every retry, while `RenderFailed` may not.

    `profile_name` is a bare string, not a `RenderProfile`: a whole-clip
    refusal from `render_pending_exports` can fire *because* the name failed
    to resolve against the registry, before any `RenderProfile` object exists
    to read `.name` off.

    `variants`, `source_start_s` and `source_end_s` travel onto it explicitly
    rather than being read off a candidate: `render_pending_exports` has no
    candidate at all, only the `PENDING` export's own record, already narrowed
    to one profile's own subset. They are why the render was requested and
    what it would have been cut from, and they do not stop being true because
    it was refused -- reconstructing them later means re-running generation
    against a transcript that may have been re-stitched.
    """
    return ClipExport(
        job_id=job_id,
        clip_id=clip_id,
        profile=profile_name,
        source_start_s=source_start_s,
        source_end_s=source_end_s,
        title=title,
        description=description,
        variants=variants,
        state=ClipState.FAILED,
        clip=None,
        failure=f"{type(error).__name__}: {error}",
    )


def _record(export: ClipExport, *, storage: TranscriptStoragePort) -> ClipExport:
    storage.save_clip_export(export)
    return export


def _ffmpeg_extractor(job_dir: Path, job_id: JobId) -> AudioExtractorPort:
    return FfmpegAudioExtractor(job_dir, job_id=job_id)


class _UnconfiguredSubjectTracker:
    """Declares `UNSUPPORTED` until 13c-i lands a real vision adapter.

    Not a fake standing in for a test: production code constructs this today
    because no vision-backed tracker has been built yet, the same way
    `production_factories` can register no ASR engine on a build with neither
    key configured. Declaring the capability rather than omitting a tracker
    altogether routes every clip through the already-proven
    `TrackingUnavailable` path -- a `FAILED` export an operator can read,
    naming what to do next -- instead of a process that cannot start at all.
    13c-i replaces this with a real tracker-resolver mirroring
    `runtime/engine_resolver.py`'s shape; nothing else about this module
    changes on that day.
    """

    _TRACKER_ID = "no-vision-adapter-configured"

    def capabilities(self) -> TrackerCapabilities:
        return TrackerCapabilities(
            tracker_id=self._TRACKER_ID, detection=DetectionSupport.UNSUPPORTED
        )

    def detect(
        self, media: SourceMedia, span: TimeSpan, *, sample_hz: float
    ) -> tuple[SubjectDetection, ...]:
        raise TrackingUnavailable(
            f"{self._TRACKER_ID} declares {DetectionSupport.UNSUPPORTED.value}; "
            f"no vision-backed tracker is built into this install yet"
        )


def run_render(
    job_id: JobId,
    clip_id: ClipId,
    data_dir: Path,
    *,
    tracker: SubjectTrackerPort | None = None,
    renderer: VideoRenderPort | None = None,
    extractor_factory: ExtractorFactory = _ffmpeg_extractor,
    render_profiles: Mapping[str, RenderProfile] = RENDER_PROFILES,
    sample_hz: float = DEFAULT_SAMPLE_HZ,
    max_clip_seconds: float = DEFAULT_MAX_CLIP_SECONDS,
) -> tuple[ClipExport, ...] | None:
    """Wire the real adapters for one clip and render every profile its
    `PENDING` exports name.

    `None` means the id pair named no clip at all -- an empty
    `load_clip_exports` result. Refused here, before `load_media` or an
    extractor is even built, the same discipline `worker.main` already applies
    to an unconfigured engine: nothing is claimed or spent for a request that
    names nothing.

    A caller-supplied `tracker`/`renderer` wins over the production default,
    mirroring `worker.run_job`'s injected `resolver`: the E2E harness and this
    module's own tests drive real `FilesystemTranscriptStorage` with fake
    heavy adapters, never the reverse.
    """
    storage = FilesystemTranscriptStorage(data_dir)
    pending = storage.load_clip_exports(job_id, clip_id)
    if not pending:
        return None

    job_dir = storage.job_dir(job_id)
    media = storage.load_media(job_id)
    probe = extractor_factory(job_dir, job_id).probe(media)
    active_tracker = tracker if tracker is not None else _UnconfiguredSubjectTracker()
    active_renderer = (
        renderer
        if renderer is not None
        else FfmpegVideoRenderer(job_dir, max_clip_seconds=max_clip_seconds)
    )

    return render_pending_exports(
        job_id,
        clip_id,
        pending,
        media=media,
        probe=probe,
        tracker=active_tracker,
        renderer=active_renderer,
        storage=storage,
        job_dir=job_dir,
        render_profiles=render_profiles,
        sample_hz=sample_hz,
        max_clip_seconds=max_clip_seconds,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    tracker: SubjectTrackerPort | None = None,
    renderer: VideoRenderPort | None = None,
    extractor_factory: ExtractorFactory = _ffmpeg_extractor,
    render_profiles: Mapping[str, RenderProfile] = RENDER_PROFILES,
) -> int:
    """Exit code carries the outcome, the idiom `worker.main` already ships:
    the supervisor that spawned this process reads this, not stdout.
    """
    parser = argparse.ArgumentParser(prog="render-worker")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--clip-id", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        job_id = make_job_id(args.job_id)
        clip_id = make_clip_id(args.clip_id)
    except InvalidIdError as error:
        print(f"render-worker: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        exports = run_render(
            job_id,
            clip_id,
            args.data_dir,
            tracker=tracker,
            renderer=renderer,
            extractor_factory=extractor_factory,
            render_profiles=render_profiles,
        )
    except DomainError as error:
        # Every failure crossing a port outside the per-profile render
        # pipeline is already a domain error -- `load_media` on a job with
        # none recorded, for one -- so this reports it rather than letting a
        # traceback reach an operator.
        print(f"render-worker: {error}", file=sys.stderr)
        return EXIT_FAILED

    if exports is None:
        print(
            f"render-worker: no pending export names clip {clip_id} on job "
            f"{job_id}; nothing was requested, so nothing is rendered",
            file=sys.stderr,
        )
        return EXIT_UNUSABLE

    return (
        EXIT_OK
        if all(export.state is ClipState.DONE for export in exports)
        else EXIT_FAILED
    )


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
