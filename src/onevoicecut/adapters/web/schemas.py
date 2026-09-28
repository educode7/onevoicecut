"""Request and response shapes for the HTTP boundary.

Separate from the domain entities on purpose. These describe what a browser may
send and what it gets back; `JobRecord` describes what the system knows. Letting
one be the other would make every domain field a public API and every API change
a domain change.

The jobs shapes moved out with their routes, into the module that owns them
(`systems/pipeline/jobs/presentation/`); what remains here is the clip half,
which moves when its routes do.
"""

from pydantic import BaseModel, ConfigDict

from onevoicecut.systems.pipeline.clips.domain.framing import TrackingConfidence
from onevoicecut.systems.pipeline.clips.domain.generation import ScriptVariant
from onevoicecut.systems.pipeline.clips.domain.rendering import (
    CaptionCoverage,
    ClipExport,
    ClipState,
    OutputQuality,
    OutputQualityKind,
    SubtitleTimingSource,
)


class ClipExportRequest(BaseModel):
    """`targets` names *networks*, never profiles: an operator reasons about
    destinations, and the profile they share is this system's business, not
    theirs to state."""

    model_config = ConfigDict(extra="forbid")

    candidate_index: int
    targets: tuple[str, ...]


class ClipExportResponse(BaseModel):
    """`profiles`, not the requested networks (rev 5's own correction): an
    operator who asked for four destinations and is getting two files must
    learn it here, at request time, not by counting files afterwards."""

    clip_id: str
    profiles: tuple[str, ...]


class ScriptVariantResponse(BaseModel):
    target: str
    format: str
    body: str
    duration_target_s: float

    @classmethod
    def of(cls, variant: ScriptVariant) -> "ScriptVariantResponse":
        return cls(
            target=variant.target,
            format=variant.format,
            body=variant.body,
            duration_target_s=variant.duration_target_s,
        )


class OutputQualityResponse(BaseModel):
    kind: OutputQualityKind
    factor: float

    @classmethod
    def of(cls, quality: OutputQuality) -> "OutputQualityResponse":
        return cls(kind=quality.kind, factor=quality.factor)


class ClipExportItem(BaseModel):
    """One profile's export. `quality`, `subtitle_timing`, `captions` and
    `tracking` are `RenderedClip`'s four declarations, projected honestly:
    `None` for a `PENDING` or `FAILED` export, because inventing a value for
    a clip that was never produced is exactly the fabrication those four
    declarations exist to prevent."""

    profile: str
    state: ClipState
    quality: OutputQualityResponse | None
    subtitle_timing: SubtitleTimingSource | None
    captions: CaptionCoverage | None
    tracking: TrackingConfidence | None
    variants: tuple[ScriptVariantResponse, ...]

    @classmethod
    def of(cls, export: ClipExport) -> "ClipExportItem":
        clip = export.clip
        return cls(
            profile=export.profile,
            state=export.state,
            quality=None if clip is None else OutputQualityResponse.of(clip.quality),
            subtitle_timing=None if clip is None else clip.subtitle_timing,
            captions=None if clip is None else clip.captions,
            tracking=None if clip is None else clip.tracking,
            variants=tuple(ScriptVariantResponse.of(v) for v in export.variants),
        )


class ClipExportListResponse(BaseModel):
    """A wrapper object, not a bare array (D10), matching `JobListResponse`:
    one export per profile, because a single-object response would have to
    pick one profile to report and be wrong about the rest."""

    exports: list[ClipExportItem]
