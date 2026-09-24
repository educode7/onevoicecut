"""The contract every `SubjectTrackerPort` must satisfy, whatever is behind it.

One body, run against every adapter — the two fakes in the default suite, the
real torchvision detector under `localmodel`. Adapters are structural
(`typing.Protocol`), so nothing forces them to agree; this is what does.

The shape is `transcription.py`'s, including the part that carries the weight
there: the body does not assume one behaviour, it reads `capabilities()` and
holds each adapter to its own declaration. An adapter declaring anything but
`AVAILABLE` MUST refuse with `TrackingUnavailable` — answering an empty series
instead is the dangerous failure, because zero detections reads exactly like a
subject who never moved and renders as a motionless centred crop reported as
success.

The body is also deliberately **hit-agnostic**. The `localmodel` fixture is a
genuinely person-free `testsrc2` clip, so every real sample is a miss, while
the fake's scripted drift is every-sample-a-hit; an assertion demanding a hit
somewhere in the series would bind one adapter and pass the other vacuously —
the same reason the transcription body never asserts what the engine heard.
What binds both is the shape of each answer: inside the span, clip-local,
strictly advancing, and a hit or an explicit miss with nothing in between.
"""

import pytest

from onevoicecut.domain.errors import TrackingUnavailable
from onevoicecut.domain.framing import TimeSpan
from onevoicecut.domain.media import SourceMedia
from onevoicecut.ports.capabilities import DetectionSupport
from onevoicecut.ports.subject_tracker import SubjectDetection, SubjectTrackerPort

# Slow enough that the real adapter's every-Nth-frame stride is visible in the
# sample count, fast enough that a two-second span still yields several.
SAMPLE_HZ = 2.0


class SubjectTrackerPortContract:
    """Subclass it and supply the three fixtures. Every test comes with it.

    A base class rather than a parametrized fixture, following
    `TranscriptionPortContract`: the real adapter's subclass carries a
    `localmodel` mark the fakes must not, and per-class marks keep that split
    per subclass — inside one file, because unlike the local ASR module this
    one imports nothing heavy at module scope and collects on a bare checkout.
    """

    @pytest.fixture
    def tracker(self) -> SubjectTrackerPort:
        raise NotImplementedError("supply the adapter under test")

    @pytest.fixture
    def media(self) -> SourceMedia:
        raise NotImplementedError("supply a source the adapter can read")

    @pytest.fixture
    def span(self) -> TimeSpan:
        raise NotImplementedError("supply a non-empty span inside that source")

    def _series_or_refusal(
        self, tracker: SubjectTrackerPort, media: SourceMedia, span: TimeSpan
    ) -> tuple[SubjectDetection, ...] | None:
        """The detection series, or `None` after proving the refusal.

        Held to the adapter's own declaration, the way the transcription body
        holds each engine to its diarization claim: a non-`AVAILABLE` adapter
        that answered anyway — even emptily — is the silent degradation the
        capability axis exists to prevent.
        """
        if tracker.capabilities().detection is not DetectionSupport.AVAILABLE:
            with pytest.raises(TrackingUnavailable):
                tracker.detect(media, span, sample_hz=SAMPLE_HZ)
            return None
        return tracker.detect(media, span, sample_hz=SAMPLE_HZ)

    def test_it_declares_a_tracker_identity(self, tracker: SubjectTrackerPort) -> None:
        """Provenance, the `engine_id` rule on this axis: a trajectory nobody
        can attribute to a detector is one nobody can reproduce."""
        capabilities = tracker.capabilities()

        assert capabilities.tracker_id
        assert capabilities.detection in set(DetectionSupport)

    def test_it_honours_its_own_capability_declaration(
        self, tracker: SubjectTrackerPort, media: SourceMedia, span: TimeSpan
    ) -> None:
        """`AVAILABLE` answers; anything else refuses by name.

        Both halves are load-bearing. The declaration lets a caller skip the
        clip before spending anything on it; the exception stops a caller who
        ignored the declaration from receiving an empty series, which reads
        exactly like a subject who never moved and would render as a
        motionless centred crop reported as success.
        """
        series = self._series_or_refusal(tracker, media, span)

        if series is not None:
            assert isinstance(series, tuple)

    def test_samples_are_clip_local_and_never_escape_the_span(
        self, tracker: SubjectTrackerPort, media: SourceMedia, span: TimeSpan
    ) -> None:
        """The port's central promise, and the one whose violation is silent.

        Source-absolute times would build a trajectory aimed two seconds past
        the clip's own start — every crop window landing on a moment the clip
        does not contain — and mirroring `TranscriptionPort`'s chunk-local
        invariant is what keeps the one translation point in the use case.
        A non-empty span always gets at least one sample: zero detections is
        not "no subject found", it is nothing to build a trajectory from.
        """
        series = self._series_or_refusal(tracker, media, span)
        if series is None:
            return

        assert series, "a non-empty span always gets at least one sample"
        times = [detection.at_s for detection in series]
        assert all(0.0 <= at_s <= span.duration_s for at_s in times)
        assert times == sorted(set(times)), "samples advance, and never repeat"

    def test_every_sample_is_a_hit_with_a_box_or_an_explicit_miss(
        self, tracker: SubjectTrackerPort, media: SourceMedia, span: TimeSpan
    ) -> None:
        """The miss axis: `box` is the decider, and the two answers are
        structurally distinguishable.

        A miss carries no box at all — never a centred or last-known position
        disguised as a detection, because the trajectory would stamp it
        `TRACKED`. A hit carries its box whatever the score: there is no
        threshold at which a weak hit becomes a miss, because whether a weak
        hit is good enough to frame on is `plan_trajectory`'s policy and never
        the detector's. A hit without a usable score, or one that located a
        person of zero extent, would be a hit in name only.
        """
        series = self._series_or_refusal(tracker, media, span)
        if series is None:
            return

        for detection in series:
            if detection.box is None:
                continue
            assert detection.confidence is not None
            assert 0.0 < detection.confidence <= 1.0
            assert detection.box.width > 0
            assert detection.box.height > 0

    def test_an_empty_span_gets_no_samples(
        self, tracker: SubjectTrackerPort, media: SourceMedia, span: TimeSpan
    ) -> None:
        """There is no frame to look at. A sample here would put a keyframe
        where no picture exists — and both shipped implementations already
        agree on `()`, which is exactly what a contract body is for: pinning
        the agreement so it cannot drift on one side only."""
        empty = TimeSpan(start_s=span.start_s, end_s=span.start_s)

        series = self._series_or_refusal(tracker, media, empty)
        if series is None:
            return

        assert series == ()
