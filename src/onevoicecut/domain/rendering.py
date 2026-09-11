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

import math
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from onevoicecut.domain.framing import CropRect, TimeSpan, TrackingConfidence
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


class DurationComplianceKind(StrEnum):
    """Whether a candidate's range fits its profile's declared ceiling.

    **[rev 5]** Two states for the reason every other axis here has two:
    a range either fits or it does not, and there is no silently-corrected
    third state. `OVER_CEILING` never trims -- `duration_compliance_of` is
    read alongside the candidate's own untouched range, never in place of it.
    """

    WITHIN_CEILING = "within_ceiling"
    OVER_CEILING = "over_ceiling"


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
class SafeArea:
    """Where a caption may not go, as fractions of the output frame.

    The fifth no-silent-degradation axis, and the least visible of the five. The
    other four are discoverable by inspecting the file; a caption sitting under a
    destination's interface overlay is correct in the file, correct in a local
    player, and wrong only in the app it was made for — which is to say, wrong
    only after it is published.

    **Fractions, never pixels.** A profile retargeted to another resolution keeps
    its meaning instead of silently moving the caption, which is the same reason
    `aspect_of` derives its pair rather than reading a declared one.

    Zero is a measured value, not a missing one: a destination with no overlay on
    an edge really does have no margin there. "Nobody has measured this yet" is
    said by `RenderProfile.safe_area` being `None`, and refused at resolution.
    """

    top: float
    bottom: float
    left: float
    right: float

    def __post_init__(self) -> None:
        """Arithmetic refusing a nonsensical value, so `ValueError` rather than a
        domain error — the same boundary `quality_of` draws for a degenerate
        crop. A job-level refusal belongs at resolution, where a profile has a
        name to put in the message."""
        for edge, value in (
            ("top", self.top),
            ("bottom", self.bottom),
            ("left", self.left),
            ("right", self.right),
        ):
            if not 0.0 <= value < 1.0:
                raise ValueError(
                    f"safe area {edge} is {value}; margins are fractions of the "
                    f"output frame and must sit in [0, 1) — 1.0 consumes the "
                    f"whole frame and a negative one places the caption outside it"
                )

        for axis, first, second in (
            ("top", self.top, self.bottom),
            ("left", self.left, self.right),
        ):
            if first + second >= 1.0:
                raise ValueError(
                    f"safe area {axis} and its opposite sum to {first + second}; "
                    f"each is individually legal and the pair leaves no frame to "
                    f"caption, which no later arithmetic can report honestly"
                )


@dataclass(frozen=True, slots=True)
class RenderProfile:
    """What a destination implies about the *file*, and nothing about the script.

    Named, so a script target can reference one without knowing what geometry is,
    and so two networks wanting the same file share it by naming the same
    profile — which is what keeps four destinations from meaning four ffmpeg
    passes and four copies on disk.

    **`safe_area` has no default**, for the reason `OutputSpec.width` has none: a
    default here is precisely the failure this axis exists to prevent, wearing
    the shape of a convenience. `None` is statable because "this destination
    exists and nobody has measured it yet" is a real state the registry must be
    able to hold — but it can never be reached by omission, and
    `resolve_render_profiles` refuses it by name.

    **No aspect field.** The pair the trajectory needs is already implied by the
    output spec; two fields that can disagree about one fact eventually will, and
    the derived one is the one nobody can contradict. See `aspect_of`.
    """

    name: str
    output: OutputSpec
    safe_area: SafeArea | None
    max_duration_s: float


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
class DurationCompliance:
    """Whether a clip's range fits its profile's ceiling, and by how much it
    does not.

    **[rev 5]** `overrun_s` is 0.0 exactly when `kind` is `WITHIN_CEILING` --
    the two never disagree, the same invariant `OutputQuality` holds between
    `kind` and `factor`. A trimmed range would have removed either the setup
    or the payoff of the clip, a judgement about the material this system
    refuses to make on the operator's behalf; this declaration exists so the
    overrun is visible instead of silently absorbed.
    """

    kind: DurationComplianceKind
    overrun_s: float


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

    None of the five declarations has a default. The rule
    `non_speech_classification` set and `word_timing` repeated: a clip that never
    stated one of these is a gap no reader can reason about, and the safe reading
    of silence is not obvious enough to encode as a default. **[rev 5]** added
    the fifth -- `duration` -- on the same argument: a render that never stated
    whether it exceeded its profile's ceiling is indistinguishable from one that
    was measured and found to fit.
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
    duration: DurationCompliance


@dataclass(frozen=True, slots=True)
class ClipExport:
    """The clip plus what an operator needs to publish it, keyed by clip *and*
    profile.

    The spec names title, description and the scripts alongside the file. An
    export without them is a video nobody can post, and reconstructing them
    later would mean re-running generation against a transcript that may have
    been re-stitched since.

    **`profile` is a name, not a `RenderProfile`.** The resolved object carries a
    measured safe area and a duration ceiling, and those are measurements against
    a destination's current interface — they go stale. An export is read back
    long after it was written, so embedding them would resurrect last month's
    margin as though the registry still agreed with it. The registry stays the
    one place that geometry lives; the name is the stable identity, and it is
    what a storage adapter can use as a path component.

    **`variants` is plural, and that is the whole point of the re-keying.**
    Networks sharing a profile share one file, so a file serving three of them
    with a single variant recorded loses the other two — the same reasoning that
    put title and description here rather than leaving them derivable.
    """

    # The identity lives here rather than on the clip, because a render that was
    # refused has no clip and still has to be recorded and addressed. Storage
    # needs both halves to place the file, and reading them off a value that may
    # be `None` would make a failure unstorable.
    job_id: JobId
    clip_id: ClipId
    profile: str
    title: str
    description: str
    variants: tuple[ScriptVariant, ...]
    state: ClipState
    # `None` until ffmpeg has actually produced something. A refusal that had to
    # invent a quality and a tracking confidence to be written down would be
    # fabricating exactly the four declarations `RenderedClip` exists to make
    # honest.
    clip: RenderedClip | None
    failure: str | None

    def __post_init__(self) -> None:
        """An export delivering nothing is the failure this type exists to
        prevent, so it is refused where it is first expressible — a `ValueError`,
        the boundary `SafeArea` and `quality_of` already draw, because there is
        no job here to name in a domain error.

        Unconditional, rather than scoped to the confirmed-coverage case task
        13a.56 named. Coverage says what the captions were built from; a variant
        says what an operator posts. A span carrying no eligible segment is still
        postable material, and inferring one axis from the other is what this
        system refuses everywhere else. Nor does it wait for `DONE`: the profiles
        a render is dispatched under are derived from the variants' targets, so
        an empty export was requested by nobody even at `PENDING`.
        """
        key = export_key(self.clip_id, self.profile)
        if not self.variants:
            raise ValueError(
                f"export {key} delivers no script variant; a rendered file "
                f"nobody can post is what this record exists to prevent, and the "
                f"variants cannot be reconstructed from a transcript that may "
                f"have been re-stitched"
            )
        if self.state is ClipState.DONE and self.clip is None:
            raise ValueError(
                f"export {key} is done and names no file; a finished render an "
                f"operator cannot open is worse than a refused one, because "
                f"nothing about it says to look again"
            )
        if self.state is ClipState.FAILED and not self.failure:
            raise ValueError(
                f"export {key} failed and says nothing about why; the worker's "
                f"own message already reaches only the server log, and a record "
                f"repeating that silence sends an operator nowhere"
            )
        if self.clip is not None and self.clip.clip_id != self.clip_id:
            raise ValueError(
                f"export {key} carries a clip named {self.clip.clip_id}; two "
                f"places holding one identity eventually disagree, and the day "
                f"they did neither would name the operator's file"
            )


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


def duration_compliance_of(span: TimeSpan, profile: RenderProfile) -> DurationCompliance:
    """Whether this span's length fits this profile's declared ceiling.

    **[rev 5]** Never trims. The candidate's range is the source of truth
    everywhere else in this system, and this function only measures it against
    the ceiling -- it does not return a shortened span, because there is none
    to return. `check_clip_range`, one layer up, refuses a range past the
    deployment-wide bound this is not; a profile's editorial ceiling is a
    softer, per-destination fact worth declaring rather than enforcing.

    The boundary sits on the legal side, matching `check_clip_range`'s own
    ceiling: a range exactly at `max_duration_s` is within it, not over it,
    because refusing the figure a profile advertises would make the number
    wrong by a second.
    """
    overrun = span.duration_s - profile.max_duration_s
    if overrun > 0.0:
        return DurationCompliance(
            kind=DurationComplianceKind.OVER_CEILING, overrun_s=overrun
        )
    return DurationCompliance(kind=DurationComplianceKind.WITHIN_CEILING, overrun_s=0.0)


def aspect_of(profile: RenderProfile) -> tuple[int, int]:
    """The output spec reduced to its lowest terms — `1080x1920` is `(9, 16)`.

    The pair feeds `TrajectoryPolicy` directly, which is what makes a second
    aspect a *value* rather than a code path: the trajectory arithmetic was
    already parameterised, so per-network delivery costs nothing here.

    Derived rather than declared, on the same argument as `quality_of` reading
    width only. A configuration restating the ratio would create a second thing
    to keep true, and the day it disagreed with the dimensions there would be no
    way to tell which one the operator meant.
    """
    divisor = math.gcd(profile.output.width, profile.output.height)
    return profile.output.width // divisor, profile.output.height // divisor


def export_key(clip_id: ClipId, profile: str) -> str:
    """What identifies a rendered file now that a clip id no longer does.

    One candidate yields one export per distinct profile, so the pair is the
    identity and either half alone names a set.

    **The key is the relative path the export is stored at, minus its suffix.**
    13b-ii persists at `render/{clip_id}/{profile}.json`, which is this key with
    `render/` in front and `.json` behind — one derivation of the identity rather
    than two spellings of it, on the argument `aspect_of` already makes. Two
    things that can disagree about one fact eventually will, and the day a key
    and a path disagreed there would be no way to tell which one named the
    operator's file.

    Path-shaped is not path-safe. A storage adapter still resolves what it builds
    from this inside the job directory, the way every other name in this system
    is checked: the profile originates in configuration, and configuration is not
    a trust boundary.
    """
    return f"{clip_id}/{profile}"
