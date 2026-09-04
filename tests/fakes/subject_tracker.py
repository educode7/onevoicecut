"""Fakes conforming to SubjectTrackerPort — no vision weights, no ffmpeg.

The spec requires the whole trajectory subsystem to be provable "with no model
weights loaded", and this is what makes that true. Smoothing, the dead-zone,
clamping, gap interpolation and the fallback threshold are all arithmetic over a
detection series; a scripted series is a better input for proving them than a
real detector, because the motion is known exactly rather than observed.

Two fakes, because the port has a capability axis and a single fake cannot stand
on both sides of it. `FakeSubjectTrackerPort` declares `AVAILABLE` and answers;
`UnavailableSubjectTrackerPort` declares `REQUIRES_SETUP` and refuses — the shape
`NonClassifyingFakeTranscriptionPort` already established for the classification
axis.

The scripted motion is a straight horizontal drift, which is the least
interesting thing a subject can do and exactly right here: a trajectory test
asserting that jitter is smoothed away needs an input whose real movement is
unambiguous, so that whatever survives smoothing is attributable.
"""

import math

from onevoicecut.domain.errors import TrackingUnavailable
from onevoicecut.domain.framing import TimeSpan
from onevoicecut.domain.media import SourceMedia
from onevoicecut.ports.capabilities import DetectionSupport, TrackerCapabilities
from onevoicecut.ports.subject_tracker import BoundingBox, SubjectDetection

TRACKER_ID = "fake-tracker"

# A person-shaped box in a 1920x1080 frame, drifting from left of centre.
_START_X = 800
_START_Y = 300
_BOX_W = 200
_BOX_H = 500


def _sample_times(span: TimeSpan, sample_hz: float) -> tuple[float, ...]:
    """Clip-local sample times, every one strictly inside the span.

    **A non-empty span always gets at least one sample.** `floor` alone returns
    zero for any clip shorter than one sample interval — a 0.2 s clip at 4 Hz —
    and zero detections is not "no subject found", it is *nothing to build a
    trajectory from*. The clip would render entirely `FALLBACK_CENTER` with the
    preacher perfectly visible in every frame of it, which is precisely the
    mostly-guessed reframe the confidence axis exists to flag.

    An empty or reversed span gets none, because there is no frame to look at.
    """
    if span.duration_s <= 0:
        return ()
    count = max(1, math.floor(span.duration_s * sample_hz))
    return tuple(index / sample_hz for index in range(count))


class FakeSubjectTrackerPort:
    """Declares AVAILABLE detection; answers every sampled point.

    `misses` names sample *indices* rather than times, so a test can say "the
    third and fourth samples are occluded" without recomputing the sampling grid
    every time the rate changes.
    """

    def __init__(
        self,
        *,
        misses: tuple[int, ...] = (),
        drift_px: int = 4,
        confidence: float = 0.9,
        fail_with: Exception | None = None,
    ) -> None:
        self._misses = set(misses)
        self._drift_px = drift_px
        self._confidence = confidence
        self._fail_with = fail_with
        self.spans: list[TimeSpan] = []

    def capabilities(self) -> TrackerCapabilities:
        return TrackerCapabilities(
            tracker_id=TRACKER_ID, detection=DetectionSupport.AVAILABLE
        )

    def detect(
        self, media: SourceMedia, span: TimeSpan, *, sample_hz: float
    ) -> tuple[SubjectDetection, ...]:
        # Recorded so a test can assert detection was scoped to the clip rather
        # than to the whole source, which is a spec scenario of its own.
        self.spans.append(span)
        if self._fail_with is not None:
            raise self._fail_with

        return tuple(
            SubjectDetection(
                at_s=at_s,
                box=None if index in self._misses else self._box_at(index),
                confidence=None if index in self._misses else self._confidence,
            )
            for index, at_s in enumerate(_sample_times(span, sample_hz))
        )

    def _box_at(self, index: int) -> BoundingBox:
        return BoundingBox(
            x=_START_X + index * self._drift_px,
            y=_START_Y,
            width=_BOX_W,
            height=_BOX_H,
        )


class UnavailableSubjectTrackerPort:
    """Declares REQUIRES_SETUP and refuses, rather than answering emptily.

    Both halves matter. The declaration lets a caller skip the clip before
    spending anything; the exception stops a caller who ignored it from
    receiving an empty series, which reads exactly like a subject who never
    moved and would render as a motionless centred crop reported as success.
    """

    def capabilities(self) -> TrackerCapabilities:
        return TrackerCapabilities(
            tracker_id="unavailable-fake-tracker",
            detection=DetectionSupport.REQUIRES_SETUP,
        )

    def detect(
        self, media: SourceMedia, span: TimeSpan, *, sample_hz: float
    ) -> tuple[SubjectDetection, ...]:
        raise TrackingUnavailable(
            f"{self.capabilities().tracker_id} declares "
            f"{self.capabilities().detection.value}; install the vision extras "
            f"or render this clip without a reframe"
        )
