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

from pathlib import Path

from onevoicecut.adapters.ffmpeg.subtitles import render_ass
from onevoicecut.adapters.storage.filesystem_transcript_storage import RENDER_DIRNAME
from onevoicecut.domain.errors import FrameGeometryUnavailable, TrackingUnavailable
from onevoicecut.domain.framing import TimeSpan, TrajectoryPolicy, crop_size_for
from onevoicecut.domain.generation import ClipCandidate
from onevoicecut.domain.ids import ClipId, JobId
from onevoicecut.domain.media import FrameSize, MediaProbe, SourceMedia
from onevoicecut.domain.rendering import (
    ClipExport,
    ClipState,
    RenderedClip,
    RenderProfile,
    aspect_of,
    quality_of,
)
from onevoicecut.ports.capabilities import DetectionSupport
from onevoicecut.ports.subject_tracker import SubjectTrackerPort
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.ports.video_render import RenderRequest, VideoRenderPort
from onevoicecut.usecases.build_subtitle_cues import build_subtitle_cues
from onevoicecut.usecases.plan_trajectory import build_trajectory
from onevoicecut.usecases.render_clip import (
    DEFAULT_MAX_CLIP_SECONDS,
    check_clip_range,
    render_clip,
)

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
    """
    span = TimeSpan(candidate.start_s, candidate.end_s)
    aspect_w, aspect_h = aspect_of(profile)
    policy = TrajectoryPolicy(aspect_w=aspect_w, aspect_h=aspect_h)

    # Before detection, not merely before the spawn. `render_clip` checks again
    # at the port, which is where the guarantee belongs -- but a vision pass runs
    # in between, and a ruinous range refused only at the spawn would already
    # have paid for detection over the whole absurd span.
    check_clip_range(span, probe, max_clip_seconds=max_clip_seconds)
    frame = _croppable_frame(probe, policy, clip_id)
    _require_detection(tracker)

    detections = tracker.detect(media, span, sample_hz=sample_hz)
    trajectory = build_trajectory(detections, frame, span, policy)

    transcript = storage.load_transcript(job_id)
    segments = () if transcript is None else transcript.segments
    cues, timing, coverage = build_subtitle_cues(segments, span)

    render_dir = job_dir / RENDER_DIRNAME
    render_dir.mkdir(parents=True, exist_ok=True)
    # Written before the render is dispatched, because the adapter refuses to
    # spawn without it -- a precondition rather than a courtesy.
    (render_dir / f"{clip_id}.ass").write_text(
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
        dest=render_dir / f"{clip_id}.mp4",
        max_clip_seconds=max_clip_seconds,
    )

    export = ClipExport(
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
        ),
        profile=profile.name,
        title=candidate.hook,
        description=candidate.quote,
        variants=candidate.variants,
        state=ClipState.DONE,
    )
    storage.save_clip_export(export)
    return export


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
