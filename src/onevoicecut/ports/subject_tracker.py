"""Where the preacher is. Nothing about how the frame gets cropped around him.

A sermon is filmed once, wide, and a vertical clip needs a window that follows
the speaker. This port answers only the first half of that: at a given moment,
where in the source frame was a person found. Cropping, smoothing, aspect ratio
and rendering are all downstream, and keeping them out of here is what lets the
whole trajectory arithmetic be proven against a fake with no vision weights
loaded.

**A miss has no box; a weak hit has one.** That is the spec's "the no-detection
result MUST be distinguishable from a low-confidence true detection", made
structural rather than conventional. A detector returning a centred guess at
`confidence=0.05` instead of `box=None` is indistinguishable from one that
genuinely found a barely-visible subject, and the trajectory built from it would
stamp a fabricated position `TRACKED` — the same silent degradation the
diarization and classification axes exist to prevent, now in the rendered file
instead of the transcript. There is deliberately no threshold at which a weak hit
becomes a miss: *whether a weak hit is good enough* is policy, and policy lives in
the use case.

**Times are clip-local, and the reason is mechanical.** The port takes a
source-absolute `span` because it must seek in the source file, and returns times
measured from the start of that span — exactly the pairing already shipped
between `AudioExtractorPort.slice` and `TranscriptionPort.transcribe`. It is also
the coordinate the render pass needs: `-ss` placed before `-i` resets output
timestamps to zero, so every commanded value is clip-local by construction. One
translation point, in the trajectory use case, and nothing downstream re-offsets.

`BoundingBox` is not `CropRect`, despite the same four integers. `CropRect` is a
window the renderer cuts; this is where a person was found. Sharing the type would
put cropping vocabulary inside an answer this port is forbidden to have an opinion
about.
"""

from dataclasses import dataclass
from typing import Protocol

from onevoicecut.domain.framing import TimeSpan
from onevoicecut.domain.media import SourceMedia
from onevoicecut.ports.capabilities import TrackerCapabilities


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """Where a person was found, in source-frame pixels."""

    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class SubjectDetection:
    """One sampled moment's answer: a located subject, or an explicit miss.

    Every field is required. A detection that could omit its box would let a
    caller construct one without deciding hit or miss, which is the single
    decision this type exists to record.

    `confidence` is allowed on a miss — some detectors score a frame even when
    they locate nothing in it — because the box is what decides, not the score.
    """

    at_s: float  # CLIP-LOCAL, mirroring TranscriptionPort's chunk-local invariant
    box: BoundingBox | None  # None is an EXPLICIT MISS, never a centred guess
    confidence: float | None  # a low-confidence HIT still carries its box


class SubjectTrackerPort(Protocol):
    def capabilities(self) -> TrackerCapabilities: ...

    def detect(
        self, media: SourceMedia, span: TimeSpan, *, sample_hz: float
    ) -> tuple[SubjectDetection, ...]:
        """INVARIANT: `at_s` is CLIP-LOCAL and every sample lies within `span`.

        Every sampled point is answered — a hit or an explicit miss, never a
        silent gap. Omitting the points it could not resolve would hand the
        trajectory a sparse series indistinguishable from one sampled more
        coarsely, and gap interpolation reads exactly that difference.

        Sampling is scoped to the clip, never the source. The spec's own scenario
        is a multi-hour recording where a few minutes are wanted, and a detector
        free to answer beyond the span would make the cost of a clip depend on
        the length of the sermon it came from.

        Raises TrackingUnavailable, DetectionFailed.
        """
        ...
