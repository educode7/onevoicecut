"""What a delivered clip is, and the four things it has to declare about itself.

Rendering is where every no-silent-degradation axis in this system finally
arrives at a file somebody watches. A clip can be soft, it can be captioned from
audio nobody verified, it can be captioned from timings nobody measured, and it
can be framed on an empty pulpit. **All four look identical in a directory
listing**, and three of them look fine in the first two seconds of playback. So
`RenderedClip` carries one declaration per axis, and none of them is a value you
have to watch the video to discover.

**They are computed above the port, never reported by the adapter.** All four are
known before ffmpeg is spawned — quality from the frame and the target, subtitle
timing from whether the segments carried words, coverage from their
`SegmentKind`, tracking from the trajectory. Letting the adapter report them
would put pure arithmetic behind an `integration` marker, which is the trade the
hexagon exists to refuse, and it would make the adapter capable of lying about a
value it never computed.

**`quality_of` lives here rather than in `framing.py`, and that is a deviation.**
design.md places it beside `crop_size_for`, "mirroring how `render_message_text`
lives beside its entities". Taken literally it does not compose: `RenderedClip`
needs `TrackingConfidence` from `framing`, so `rendering` already imports
`framing`, and putting `quality_of` there would need `OutputQuality` imported
back — a circular import at module load. The stated rationale actually points
here: `render_message_text` sits beside the type it *returns into*, and this
function returns an `OutputQuality`. One module over, in the direction that has
no cycle.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from onevoicecut.domain.framing import CropRect, TrackingConfidence
from onevoicecut.domain.generation import ScriptVariant
from onevoicecut.domain.ids import ClipId, JobId


class OutputQualityKind(StrEnum):
    """Whether the delivered pixels came from the source or were stretched."""

    NATIVE = "native"
    UPSCALED = "upscaled"


class SubtitleTimingSource(StrEnum):
    """Where the cue boundaries came from.

    Two states for the same reason `WordTimingSupport` has two: an engine either
    produced word timings or it did not. A clip built from the segment-level
    fallback must say so rather than present evenly-guessed captions as ordinary
    ones — they drift further from the audio with every syllable the speaker
    lingers on, and look completely plausible while doing it.
    """

    WORD_LEVEL = "word_level"
    SEGMENT_LEVEL = "segment_level"


class CaptionCoverage(StrEnum):
    """What the burned-in captions were built from.

    **One basis for all three members: the eligible segments overlapping the
    clip's span, never the cues.** Cue construction is total over that set, so
    "no eligible segment" and "zero cues" are the same condition rather than two
    — which is what lets `NONE` mean something an operator can act on instead of
    being indistinguishable from a cue builder that quietly produced nothing.
    """

    CONFIRMED_SPEECH = "confirmed_speech"  # every eligible segment was SPEECH
    INCLUDES_UNVERIFIED = "includes_unverified"  # at least one was UNCERTAIN
    NONE = "none"  # the span carried no eligible segment at all


class ClipState(StrEnum):
    """The same four answers a chunk gives.

    Deliberately the shape of `ChunkState` rather than a second vocabulary: a
    clip is dispatched, worked and finished the same way, and a reader who knows
    one lifecycle should not have to learn another for the other.
    """

    PENDING = "pending"
    RENDERING = "rendering"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class OutputSpec:
    """The delivery target: what the rendered file is supposed to be.

    `width` and `height` have no defaults. They decide whether every clip this
    build produces is native or soft, and a default would make that choice
    invisible at the one place it is actually made — the same reasoning that
    keeps `local_model_size` undefaulted.
    """

    width: int
    height: int
    # Defaulted, unlike the dimensions: frame rate does not decide sharpness,
    # and 30 is what every short-form destination accepts.
    fps: float = 30.0


@dataclass(frozen=True, slots=True)
class OutputQuality:
    """Native or upscaled, and by how much.

    `factor` is `target_width / crop_width`, and the direction is the readable
    one: above 1.0 the clip is being stretched. Inverting it would make `1.78`
    read as a better clip than `0.89`, which is backwards from how the words
    "upscale factor" are spoken.
    """

    kind: OutputQualityKind
    factor: float


@dataclass(frozen=True, slots=True)
class SubtitleCue:
    """One on-screen caption. Times are **clip-local**, like everything past the
    trajectory — the render pass places `-ss` before `-i`, which resets output
    timestamps to zero, so a source-absolute cue would land hours away."""

    start_s: float
    end_s: float
    text: str


@dataclass(frozen=True, slots=True)
class RenderedClip:
    """A finished clip, and everything about it that is not visible in a listing.

    None of the four declarations has a default. The rule
    `non_speech_classification` set and `word_timing` repeated: a clip that never
    stated one of these is a gap no reader can reason about, and the safe reading
    of silence is not obvious enough to encode as a default.
    """

    clip_id: ClipId
    job_id: JobId
    path: Path
    # Source-absolute, and the only place the original coordinate survives —
    # everything inside the render is clip-local. Without this pair a clip
    # cannot be traced back into the three-hour recording it came from.
    source_start_s: float
    source_end_s: float
    quality: OutputQuality
    subtitle_timing: SubtitleTimingSource
    captions: CaptionCoverage
    tracking: TrackingConfidence


@dataclass(frozen=True, slots=True)
class ClipExport:
    """The clip plus what an operator needs to publish it.

    The spec names title, description and the script variant alongside the file.
    An export without them is a video nobody can post, and reconstructing them
    later would mean re-running generation against a transcript that may have
    been re-stitched since.
    """

    clip: RenderedClip
    title: str
    description: str
    variant: ScriptVariant
    state: ClipState


def quality_of(crop: CropRect, target: OutputSpec) -> OutputQuality:
    """Whether delivering this crop at this target stretches anything.

    **Width only.** Height cannot disagree: `crop_size_for` derives one from the
    other at a fixed aspect and `CropTrajectory` holds the pair constant for the
    whole clip, so a second axis could only restate the first — or contradict
    it, which is worse than being silent.

    A factor of exactly 1.0 is native. The boundary belongs on that side because
    stretching by one is stretching by nothing, and putting it on the other would
    flag every perfectly-matched render as degraded.

    A degenerate crop is refused rather than divided by. `crop_size_for` is total
    and answers `(0, 0)` for a frame under two pixels — an honest answer that has
    no quality — and slice 13b's worker turns this refusal into
    `FrameGeometryUnavailable` before a render is ever dispatched. Returning a
    fabricated factor here would put a number on a clip that cannot exist.
    """
    if crop.width <= 0:
        raise ValueError(
            f"a crop {crop.width}px wide has no output quality; a frame under "
            f"two pixels yields a degenerate crop, which is refused before a "
            f"render is dispatched"
        )

    factor = target.width / crop.width
    kind = (
        OutputQualityKind.UPSCALED if factor > 1.0 else OutputQualityKind.NATIVE
    )
    return OutputQuality(kind=kind, factor=factor)
