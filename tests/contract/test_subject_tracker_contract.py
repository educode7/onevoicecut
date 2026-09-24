"""The subject-tracker contract: fakes in the default run, the real detector under `localmodel`.

Every trajectory test in the suite is written against `FakeSubjectTrackerPort`,
and the port is structural — nothing forces the fake, or the real adapter, to
agree with it. This file runs one shared body (`subject_tracking.py`, the
`transcription.py` pattern) against all three implementations, so a drift on
either side fails here rather than in a rendered clip.

One file with a per-class mark, rather than the two-file split the
transcription contract uses, and the reason is the fake half: it must run in
the default suite, because a fake that drifted from the port would turn every
trajectory test into a proof about a detector that does not exist. The
transcription split exists because its real-engine module needs a module-level
`importorskip`; this file needs none — the adapter imports nothing heavy at
module scope, pinned by an `ast` test in the default suite — so it collects on
a bare checkout, and the `localmodel` class guards inside its fixtures instead,
honouring the capability probe's two facts rather than triggering a 167 MB
weights download.
"""

import subprocess
from pathlib import Path

import pytest

from onevoicecut.adapters.vision.declarations import weights_cached
from onevoicecut.adapters.vision.torchvision_tracker_adapter import (
    TorchvisionSubjectTracker,
)
from onevoicecut.domain.framing import TimeSpan
from onevoicecut.domain.ids import make_media_id
from onevoicecut.domain.media import SourceMedia
from onevoicecut.ports.subject_tracker import SubjectTrackerPort
from tests.contract.subject_tracking import SAMPLE_HZ, SubjectTrackerPortContract
from tests.fakes.subject_tracker import (
    FakeSubjectTrackerPort,
    UnavailableSubjectTrackerPort,
)

MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFZ")

# A non-zero start, like the transcription contract's CHUNK_START_S: an adapter
# returning source-absolute times still looks correct against a span starting
# at 0. The real fixture is only FIXTURE_SECONDS long, so the offset stays
# small — two seconds of pre-roll still separates absolute from clip-local.
SPAN = TimeSpan(start_s=2.0, end_s=4.0)

FIXTURE_SECONDS = 6.0
MAX_CLIP_SECONDS = 180.0


def _a_media(stored: Path) -> SourceMedia:
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicacion.mp4",
        stored_path=stored,
        size_bytes=stored.stat().st_size,
        container="mp4",
        checksum="0" * 64,
    )


class TestTheFakeSatisfiesTheContract(SubjectTrackerPortContract):
    """The double every trajectory test is written against, held to the port.

    Its scripted drift is every-sample-a-hit, one of the two extremes the
    shared body binds without assuming either.
    """

    @pytest.fixture
    def tracker(self) -> SubjectTrackerPort:
        return FakeSubjectTrackerPort()

    @pytest.fixture
    def media(self, tmp_path: Path) -> SourceMedia:
        stored = tmp_path / "source"
        stored.write_bytes(b"not really a video")
        return _a_media(stored)

    @pytest.fixture
    def span(self) -> TimeSpan:
        return SPAN


class TestTheUnavailableFakeSatisfiesTheContract(SubjectTrackerPortContract):
    """The refusal half of the contract, in the default run.

    Without this subclass the body's declaration branch — a non-`AVAILABLE`
    adapter MUST refuse rather than answer emptily — would only ever execute
    under `localmodel`, on a machine that happens to lack the vision extras.
    """

    @pytest.fixture
    def tracker(self) -> SubjectTrackerPort:
        return UnavailableSubjectTrackerPort()

    @pytest.fixture
    def media(self, tmp_path: Path) -> SourceMedia:
        stored = tmp_path / "source"
        stored.write_bytes(b"not really a video")
        return _a_media(stored)

    @pytest.fixture
    def span(self) -> TimeSpan:
        return SPAN


@pytest.mark.localmodel
class TestTheRealAdapterSatisfiesTheContract(SubjectTrackerPortContract):
    """The real torchvision adapter, under exactly the same body as the fakes.

    The fixture is ffmpeg's `testsrc2`, which contains no person — the honesty
    split 13c-i recorded: no deterministic person fixture exists on this
    machine, so every real sample here is an explicit miss and the body binds
    the miss shape, the span scoping and the clip-local times against the
    weights, while the hit arithmetic stays proven against injected
    predictions in the default suite.
    """

    @pytest.fixture
    def tracker(self) -> SubjectTrackerPort:
        # In the fixture, not at module level: a module-wide `importorskip`
        # would take the fake half — which needs no extras — down with it.
        pytest.importorskip(
            "torchvision",
            reason="vision extras not installed (requirements-vision.txt)",
        )
        pytest.importorskip(
            "av", reason="vision extras not installed (requirements-vision.txt)"
        )
        if not weights_cached():
            pytest.skip("cached vision weights absent (requirements-vision.txt)")
        return TorchvisionSubjectTracker(max_clip_seconds=MAX_CLIP_SECONDS)

    @pytest.fixture
    def media(self, tmp_path: Path, ffmpeg_available: None) -> SourceMedia:
        """A small, deterministic, genuinely person-free video."""
        video = tmp_path / "source.mp4"
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-y",
                "-f", "lavfi",
                "-i", f"testsrc2=size=320x240:rate=25:duration={FIXTURE_SECONDS}",
                "-pix_fmt", "yuv420p",
                str(video),
            ],
            check=True,
            capture_output=True,
            timeout=60,
        )
        return _a_media(video)

    @pytest.fixture
    def span(self) -> TimeSpan:
        return SPAN

    def test_a_person_free_span_comes_back_as_explicit_misses(
        self, tracker: SubjectTrackerPort, media: SourceMedia, span: TimeSpan
    ) -> None:
        """13c.10: a miss is reported, never guessed — against the real weights.

        `testsrc2` carries no person, and CPU eval-mode inference is
        deterministic, so the all-miss expectation is stable rather than lucky.
        A detector that papered over a miss with a plausible centred box would
        return `box` here — at `(W - w) / 2` on both axes, the guess the port
        exists to forbid — and the trajectory would stamp it `TRACKED`, a
        fabricated frame wearing a real detection's provenance. The miss shape
        leaves nothing to mistake: no box, and no score that could be read as a
        weak hit. What to do about an absent subject is `plan_trajectory`'s
        decision, and it stamps that decision `FALLBACK_CENTER`.
        """
        detections = tracker.detect(media, span, sample_hz=SAMPLE_HZ)

        assert detections, "the fixture is person-free, not frame-free"
        for detection in detections:
            assert detection.box is None
            assert detection.confidence is None
