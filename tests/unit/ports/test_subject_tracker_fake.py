"""The fake every trajectory test will be written against, held to its port.

The whole trajectory subsystem — smoothing, dead-zone, clamping, interpolation,
the fallback threshold — is specified as provable "with no model weights loaded".
That is only true if this fake is faithful, and structural typing means nothing
forces it to be. A fake that drifted from the port would not be a fake; it would
be a second implementation with its own contract, and the arithmetic written
against it would break against the real detector.

Two invariants carry the weight, both from the port's own docstring:

**Every sampled point is answered.** A hit or an explicit miss, never a silent
gap. A detector that simply omitted the points it could not resolve would hand
the trajectory a sparse series indistinguishable from one sampled more coarsely,
and the gap-interpolation logic reads exactly that difference.

**No sample lies outside the requested span.** Detection is scoped to the clip,
not the source: the spec's own scenario is a multi-hour recording where a few
minutes are wanted, and a detector free to answer beyond the span would make the
cost of a clip depend on the length of the sermon it came from.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.errors import DetectionFailed, TrackingUnavailable
from onevoicecut.domain.framing import TimeSpan
from onevoicecut.domain.ids import make_media_id
from onevoicecut.domain.media import SourceMedia
from onevoicecut.ports.capabilities import DetectionSupport
from onevoicecut.ports.subject_tracker import BoundingBox, SubjectTrackerPort
from tests.fakes.subject_tracker import (
    FakeSubjectTrackerPort,
    UnavailableSubjectTrackerPort,
)

MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")
SPAN = TimeSpan(start_s=600.0, end_s=602.0)
SAMPLE_HZ = 4.0


def _media() -> SourceMedia:
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicacion.mp4",
        stored_path=Path("source"),
        size_bytes=4096,
        container="mp4",
        checksum="deadbeef",
    )


def _port(**kwargs: object) -> SubjectTrackerPort:
    """Typed as the port on purpose: this call is the structural check."""
    return FakeSubjectTrackerPort(**kwargs)  # type: ignore[arg-type]


class TestItSatisfiesThePort:
    def test_it_names_the_tracker_that_produced_the_detections(self) -> None:
        """Provenance, same rule as `engine_id` on a chunk result: a trajectory
        nobody can attribute to a tracker is one nobody can reproduce."""
        assert _port().capabilities().tracker_id

    def test_it_declares_whether_it_can_detect_at_all(self) -> None:
        assert _port().capabilities().detection in set(DetectionSupport)


class TestEverySampledPointIsAnswered:
    def test_the_count_follows_the_sample_rate_and_the_span(self) -> None:
        """Two seconds at 4 Hz is eight samples. Naming it here means a change
        to the sampling contract shows up as a change in this number rather than
        as a trajectory that quietly got coarser."""
        detections = _port().detect(_media(), SPAN, sample_hz=SAMPLE_HZ)

        assert len(detections) == 8

    def test_a_point_it_cannot_resolve_is_still_answered(self) -> None:
        """With an explicit miss. Omitting it instead would hand the trajectory a
        sparse series indistinguishable from coarser sampling, and gap
        interpolation reads exactly that difference."""
        detections = _port(misses=(2, 3)).detect(_media(), SPAN, sample_hz=SAMPLE_HZ)

        assert len(detections) == 8
        assert [d.box is None for d in detections] == [
            False, False, True, True, False, False, False, False
        ]

    def test_a_miss_is_a_missing_box_not_a_centred_guess(self) -> None:
        """The rule the whole port exists to hold. A synthesised position would
        be marked `TRACKED` downstream — a fabricated frame wearing a real
        detection's provenance."""
        detections = _port(misses=(0,)).detect(_media(), SPAN, sample_hz=SAMPLE_HZ)

        assert detections[0].box is None


class TestNoSampleEscapesTheSpan:
    def test_every_time_lies_inside_the_requested_range(self) -> None:
        span_s = SPAN.duration_s

        for detection in _port().detect(_media(), SPAN, sample_hz=SAMPLE_HZ):
            assert 0.0 <= detection.at_s < span_s

    def test_times_are_clip_local_not_source_absolute(self) -> None:
        """The span starts at 600 s in the source and the first sample is at 0.

        Mirrors `TranscriptionPort`'s chunk-local invariant, and it is the
        coordinate the render pass needs: `-ss` before `-i` resets output
        timestamps to zero, so every commanded value is clip-local by
        construction. One translation point, in the use case, and nothing
        downstream re-offsets.
        """
        detections = _port().detect(_media(), SPAN, sample_hz=SAMPLE_HZ)

        assert detections[0].at_s == 0.0
        assert max(d.at_s for d in detections) < SPAN.duration_s

    def test_times_are_ordered(self) -> None:
        """The trajectory folds these in order; samples that ran backwards would
        corrupt smoothing without ever raising."""
        detections = _port().detect(_media(), SPAN, sample_hz=SAMPLE_HZ)

        assert [d.at_s for d in detections] == sorted(d.at_s for d in detections)

    def test_a_span_shorter_than_one_sample_still_gets_one(self) -> None:
        """Zero detections is not "no subject found" — it is nothing to build a
        trajectory from, and the clip would render entirely `FALLBACK_CENTER`
        with the preacher visible in every frame of it.

        Found by mutation-checking: swapping `floor` for `ceil` changed nothing
        any test could see, and chasing why showed the real difference was not
        samples escaping the span (neither operator does that) but a short clip
        getting none at all.
        """
        detections = _port().detect(
            _media(), TimeSpan(start_s=0.0, end_s=0.1), sample_hz=SAMPLE_HZ
        )

        assert len(detections) == 1
        assert detections[0].at_s == 0.0

    def test_an_empty_span_gets_no_samples(self) -> None:
        """There is no frame to look at. A sample here would put a keyframe
        where no picture exists."""
        assert (
            _port().detect(
                _media(), TimeSpan(start_s=5.0, end_s=5.0), sample_hz=SAMPLE_HZ
            )
            == ()
        )

    def test_a_non_integer_sample_count_never_escapes_the_span(self) -> None:
        """2.1 s at 4 Hz is 8.4 samples. Whatever rounding is chosen, the last
        one must still be a frame the renderer will actually cut."""
        span = TimeSpan(start_s=0.0, end_s=2.1)

        for detection in _port().detect(_media(), span, sample_hz=SAMPLE_HZ):
            assert detection.at_s < span.duration_s


class TestWhatItCanRefuse:
    def test_a_tracker_that_cannot_run_says_so_before_detecting(self) -> None:
        """Declared, then raised. The declaration is what lets a caller skip the
        clip; the exception is what stops a caller who ignored it from receiving
        an empty trajectory that looks like a subject who never moved."""
        tracker = UnavailableSubjectTrackerPort()

        assert tracker.capabilities().detection is DetectionSupport.REQUIRES_SETUP
        with pytest.raises(TrackingUnavailable):
            tracker.detect(_media(), SPAN, sample_hz=SAMPLE_HZ)

    def test_a_detection_failure_is_a_domain_error(self) -> None:
        """An adapter must never leak a provider exception upward; a caller
        catching domain errors would not survive a vision library's own."""
        with pytest.raises(DetectionFailed):
            _port(fail_with=DetectionFailed("weights unreadable")).detect(
                _media(), SPAN, sample_hz=SAMPLE_HZ
            )


class TestTheScriptedPositions:
    def test_a_moving_subject_produces_moving_boxes(self) -> None:
        """What every trajectory test needs: a series whose motion is known, so
        smoothing and the dead-zone can be asserted against an input nobody has
        to guess about."""
        detections = _port(drift_px=10).detect(_media(), SPAN, sample_hz=SAMPLE_HZ)

        centres = [d.box.x for d in detections if d.box is not None]
        assert centres == sorted(centres)
        assert centres[-1] > centres[0]

    def test_a_still_subject_produces_identical_boxes(self) -> None:
        detections = _port(drift_px=0).detect(_media(), SPAN, sample_hz=SAMPLE_HZ)

        boxes = {d.box for d in detections if d.box is not None}
        assert len(boxes) == 1

    def test_the_boxes_are_bounding_boxes(self) -> None:
        detections = _port().detect(_media(), SPAN, sample_hz=SAMPLE_HZ)

        assert all(isinstance(d.box, BoundingBox) for d in detections)
