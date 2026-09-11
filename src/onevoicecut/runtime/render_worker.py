"""Assembling one clip's render, and refusing before the expensive step.

A second composition root, like `worker.py`: a render is a separate short-lived
process, and render state lives per clip rather than on the job record.

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

from collections.abc import Mapping
from pathlib import Path

from onevoicecut.adapters.ffmpeg.subtitles import render_ass
from onevoicecut.adapters.storage.filesystem_transcript_storage import RENDER_DIRNAME
from onevoicecut.domain.errors import (
    DomainError,
    FrameGeometryUnavailable,
    RenderProfileInvalid,
    TrackingUnavailable,
)
from onevoicecut.domain.framing import (
    CropTrajectory,
    TimeSpan,
    TrajectoryPolicy,
    crop_size_for,
)
from onevoicecut.domain.generation import ClipCandidate, ScriptVariant
from onevoicecut.domain.ids import ClipId, JobId
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
from onevoicecut.ports.capabilities import DetectionSupport
from onevoicecut.ports.subject_tracker import SubjectTrackerPort
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.ports.video_render import RenderRequest, VideoRenderPort
from onevoicecut.usecases.build_subtitle_cues import build_subtitle_cues
from onevoicecut.usecases.generate_artifacts import SCRIPT_TARGETS, ScriptTarget
from onevoicecut.usecases.plan_trajectory import build_trajectory
from onevoicecut.usecases.render_clip import (
    DEFAULT_MAX_CLIP_SECONDS,
    check_clip_range,
    render_clip,
)
from onevoicecut.usecases.render_profiles import RENDER_PROFILES, resolve_render_profiles

# design.md's figure: four samples a second, and the trajectory keeps one
# keyframe per sample. Named here rather than spelled at the call site, where a
# silent change would alter both the cost of every clip and the confidence ratio.
DEFAULT_SAMPLE_HZ = 4.0


def render_clip_for_profile(
    job_id: JobId,
    clip_id: ClipId,
    candidate: ClipCandidate,
    *,
    profile: RenderProfile,
    media: SourceMedia,
    probe: MediaProbe,
    tracker: SubjectTrackerPort,
    renderer: VideoRenderPort,
    storage: TranscriptStoragePort,
    job_dir: Path,
    sample_hz: float = DEFAULT_SAMPLE_HZ,
    max_clip_seconds: float = DEFAULT_MAX_CLIP_SECONDS,
) -> ClipExport:
    """One clip, one profile, from probe to persisted export.

    The candidate's range is the source of truth and is never adjusted here.
    `render_clip` is the only way into the port, so its refusals are this
    worker's refusals too rather than a second set of guards free to drift from
    them.

    **Always returns an export, never raises a domain error.** A refusal that
    only propagated would reach the server log and nowhere an operator looks --
    the gap this project already carries for the transcription worker, and did
    not want to repeat one subsystem later. The failure is recorded under the
    same clip-and-profile key a success would have used, so a caller polling one
    clip finds the refusal where it expected the file.
    """
    span = TimeSpan(candidate.start_s, candidate.end_s)

    try:
        return _render(
            job_id,
            clip_id,
            candidate,
            span=span,
            profile=profile,
            media=media,
            probe=probe,
            tracker=tracker,
            renderer=renderer,
            storage=storage,
            job_dir=job_dir,
            sample_hz=sample_hz,
            max_clip_seconds=max_clip_seconds,
        )
    except DomainError as error:
        # Every failure crossing a port is already a domain error, so the worker
        # records it rather than letting a traceback reach an operator. A
        # non-domain exception is a defect here and is left to propagate.
        return _record(
            _failed(job_id, clip_id, candidate, profile, candidate.variants, error),
            storage=storage,
        )


def render_clip_for_candidate(
    job_id: JobId,
    clip_id: ClipId,
    candidate: ClipCandidate,
    *,
    media: SourceMedia,
    probe: MediaProbe,
    tracker: SubjectTrackerPort,
    renderer: VideoRenderPort,
    storage: TranscriptStoragePort,
    job_dir: Path,
    script_targets: Mapping[str, ScriptTarget] = SCRIPT_TARGETS,
    render_profiles: Mapping[str, RenderProfile] = RENDER_PROFILES,
    sample_hz: float = DEFAULT_SAMPLE_HZ,
    max_clip_seconds: float = DEFAULT_MAX_CLIP_SECONDS,
) -> tuple[ClipExport, ...]:
    """One clip, fanned out across every *distinct* profile its variants name.

    **[rev 5]** A candidate's variants name networks, not profiles, and several
    networks routinely name the same one -- every destination this change
    ships is `vertical`. Rendering per network would put byte-identical files
    in the job directory with nothing to tell them apart, so the fan-out is
    keyed on `ScriptTarget.profile` after dedup, never on the network. Each
    resolved profile keeps only the variants that named it, so a shared file
    still carries every variant it serves and a distinct one carries only its
    own -- `ClipExport.variants` being plural is exactly what makes this
    representable.

    `script_targets` and `render_profiles` are the two registries this join
    walks -- `ScriptTarget.profile` names a profile, `render_profiles` resolves
    it. Both are parameters, not the module constants, for the same reason
    `resolve_render_profiles`'s own registry is: a test proving two distinct
    profiles would otherwise have to widen the shipped one-entry registry to
    reach it.

    **Detection runs at most once, trajectory planning at most once per
    distinct aspect.** `SubjectTrackerPort.detect` takes no aspect and no
    policy -- it answers where a person was found in the source frame, the
    same answer whatever shape gets cropped around it -- so it is hoisted
    above the per-profile loop entirely. Aspect enters only at
    `build_trajectory`, so two profiles that agree on aspect share the one
    trajectory built from that shared detection set, and a profile with a
    different aspect gets its own. Re-detecting per profile would multiply
    the one cost this pipeline lets model weights dominate, to obtain an
    identical answer.

    A whole-clip refusal (an impossible range, a tracker declaring no
    detection support) fails every profile identically, recorded once each
    under its own key. A refusal scoped to one profile's aspect -- a
    degenerate crop for that aspect alone -- fails only the profiles sharing
    it; the rest still render from the detection already paid for.
    """
    grouped = _grouped_profiles(
        candidate.variants, script_targets=script_targets, render_profiles=render_profiles
    )
    span = TimeSpan(candidate.start_s, candidate.end_s)

    try:
        check_clip_range(span, probe, max_clip_seconds=max_clip_seconds)
        _require_detection(tracker)
        detections = tracker.detect(media, span, sample_hz=sample_hz)
        transcript = storage.load_transcript(job_id)
        segments = () if transcript is None else transcript.segments
        cues, timing, coverage = build_subtitle_cues(segments, span)
    except DomainError as error:
        return tuple(
            _record(
                _failed(job_id, clip_id, candidate, profile, variants, error),
                storage=storage,
            )
            for profile, variants in grouped
        )

    render_dir = job_dir / RENDER_DIRNAME
    trajectories: dict[tuple[int, int], CropTrajectory] = {}
    exports: list[ClipExport] = []

    for profile, variants in grouped:
        try:
            aspect = aspect_of(profile)
            if aspect not in trajectories:
                policy = TrajectoryPolicy(aspect_w=aspect[0], aspect_h=aspect[1])
                frame = _croppable_frame(probe, policy, clip_id)
                trajectories[aspect] = build_trajectory(detections, frame, span, policy)
            export = _export_from_trajectory(
                job_id,
                clip_id,
                candidate,
                profile=profile,
                span=span,
                probe=probe,
                trajectory=trajectories[aspect],
                cues=cues,
                timing=timing,
                coverage=coverage,
                variants=variants,
                media=media,
                renderer=renderer,
                storage=storage,
                render_dir=render_dir,
                max_clip_seconds=max_clip_seconds,
            )
        except DomainError as error:
            export = _record(
                _failed(job_id, clip_id, candidate, profile, variants, error),
                storage=storage,
            )
        exports.append(export)

    return tuple(exports)


def _grouped_profiles(
    variants: tuple[ScriptVariant, ...],
    *,
    script_targets: Mapping[str, ScriptTarget],
    render_profiles: Mapping[str, RenderProfile],
) -> tuple[tuple[RenderProfile, tuple[ScriptVariant, ...]], ...]:
    """A candidate's variants, grouped by the distinct profile they resolve to.

    Order is first-seen among the variants, the same rule
    `resolve_render_profiles` already applies to an operator's comma list --
    reused here rather than re-implemented, so one unmeasured profile still
    refuses the whole candidate rather than rendering the rest and silently
    dropping it.
    """
    order: list[str] = []
    by_profile_name: dict[str, list[ScriptVariant]] = {}
    for variant in variants:
        target = script_targets.get(variant.target)
        if target is None:
            raise RenderProfileInvalid(
                f"script variant names network {variant.target!r}, which no "
                f"configured script target maps to a render profile"
            )
        if target.profile not in by_profile_name:
            by_profile_name[target.profile] = []
            order.append(target.profile)
        by_profile_name[target.profile].append(variant)

    profiles = resolve_render_profiles(",".join(order), registry=render_profiles)
    return tuple(
        (profile, tuple(by_profile_name[profile.name])) for profile in profiles
    )


def _render(
    job_id: JobId,
    clip_id: ClipId,
    candidate: ClipCandidate,
    *,
    span: TimeSpan,
    profile: RenderProfile,
    media: SourceMedia,
    probe: MediaProbe,
    tracker: SubjectTrackerPort,
    renderer: VideoRenderPort,
    storage: TranscriptStoragePort,
    job_dir: Path,
    sample_hz: float,
    max_clip_seconds: float,
) -> ClipExport:
    """The single-profile path. Every refusal on it is a domain error.

    `render_clip_for_candidate` does not call this: fanning out to several
    profiles means detection and cues are shared, and this function always
    pays for its own. It stays the direct route for a caller that already
    knows the one profile it wants.
    """
    # Before detection, not merely before the spawn. `render_clip` checks again
    # at the port, which is where the guarantee belongs -- but a vision pass runs
    # in between, and a ruinous range refused only at the spawn would already
    # have paid for detection over the whole absurd span.
    check_clip_range(span, probe, max_clip_seconds=max_clip_seconds)
    aspect_w, aspect_h = aspect_of(profile)
    policy = TrajectoryPolicy(aspect_w=aspect_w, aspect_h=aspect_h)
    frame = _croppable_frame(probe, policy, clip_id)
    _require_detection(tracker)

    detections = tracker.detect(media, span, sample_hz=sample_hz)
    trajectory = build_trajectory(detections, frame, span, policy)

    transcript = storage.load_transcript(job_id)
    segments = () if transcript is None else transcript.segments
    cues, timing, coverage = build_subtitle_cues(segments, span)

    render_dir = job_dir / RENDER_DIRNAME
    return _export_from_trajectory(
        job_id,
        clip_id,
        candidate,
        profile=profile,
        span=span,
        probe=probe,
        trajectory=trajectory,
        cues=cues,
        timing=timing,
        coverage=coverage,
        variants=candidate.variants,
        media=media,
        renderer=renderer,
        storage=storage,
        render_dir=render_dir,
        max_clip_seconds=max_clip_seconds,
    )


def _export_from_trajectory(
    job_id: JobId,
    clip_id: ClipId,
    candidate: ClipCandidate,
    *,
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

    The step every render path converges on -- whether it arrived alone
    through `_render` or from `render_clip_for_candidate`'s loop, where several
    profiles sharing an aspect call this with the identical `trajectory`
    object. `variants` is the caller's to narrow: `_render` passes the whole
    candidate's, the fan-out passes only the subset that named this profile.
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
        title=candidate.hook,
        description=candidate.quote,
        variants=variants,
        state=ClipState.DONE,
    )
    return _record(export, storage=storage)


def _croppable_frame(
    probe: MediaProbe, policy: TrajectoryPolicy, clip_id: ClipId
) -> FrameSize:
    """A picture, and one large enough to cut a window out of.

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
    candidate: ClipCandidate,
    profile: RenderProfile,
    variants: tuple[ScriptVariant, ...],
    error: DomainError,
) -> ClipExport:
    """A refusal, recorded with no clip and with the type that caused it.

    The type name leads the message because the operator's next move depends on
    it and not on the prose: `ClipRangeInvalid` and `FrameGeometryUnavailable`
    fail identically on every retry, while `RenderFailed` may not.

    `variants` travel onto it explicitly rather than being read off the
    candidate: a whole-clip refusal from `render_clip_for_candidate` names one
    profile's own subset, not every network the candidate carries. They are
    why the render was requested and do not stop being true because it was
    refused -- reconstructing them later means re-running generation against a
    transcript that may have been re-stitched.
    """
    return ClipExport(
        job_id=job_id,
        clip_id=clip_id,
        profile=profile.name,
        title=candidate.hook,
        description=candidate.quote,
        variants=variants,
        state=ClipState.FAILED,
        clip=None,
        failure=f"{type(error).__name__}: {error}",
    )


def _record(export: ClipExport, *, storage: TranscriptStoragePort) -> ClipExport:
    storage.save_clip_export(export)
    return export
