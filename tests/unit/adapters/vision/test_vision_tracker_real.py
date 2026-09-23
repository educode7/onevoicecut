"""The real vision tracker, against the installed weights and a real decode.

The claims only the real extras can make, mirroring the shape
`test_faster_whisper_diarization.py` established: the decode is scoped to the
requested span, the span guard refuses before any decode, and the probe
declares `AVAILABLE` on a real install. Everything decidable from data — the
sampling arithmetic, the person filter, the box rescale, the probe's decision
function, the model lifecycle — is proven in the default suite beside this file
through injected seams, with no weights.

The fixture is ffmpeg's `testsrc2`, which contains no person, and that is a
forced move rather than an oversight: no deterministic person fixture exists on
this machine, and a crude synthetic drawing would make the hit path flaky
instead of proven. What the fixture proves is the contract — samples land only
inside the span, times are clip-local, and a genuinely person-free frame comes
back as an explicit miss (`box=None`), never a centred guess. The hit path's
arithmetic is proven against injected predictions in the default suite, and the
person-label mapping is pinned below against the installed weights' own
metadata — the same honesty split the SAPI diarization fixture drew.
"""

import subprocess
from pathlib import Path, PurePosixPath

import pytest

# Before the adapter import, and load-bearing: pytest imports every test module
# during collection, before it filters on markers.
pytest.importorskip(
    "torchvision",
    reason="vision extras not installed (requirements-vision.txt)",
)
pytest.importorskip("av", reason="vision extras not installed (requirements-vision.txt)")

from onevoicecut.adapters.vision.declarations import (  # noqa: E402
    WEIGHTS_FILENAME,
    is_installed,
    weights_cached,
)
from onevoicecut.adapters.vision.torchvision_tracker_adapter import (  # noqa: E402
    PERSON_LABEL,
    TRACKER_ID,
    TorchvisionSubjectTracker,
)
from onevoicecut.domain.errors import DetectionFailed  # noqa: E402
from onevoicecut.domain.framing import TimeSpan  # noqa: E402
from onevoicecut.domain.ids import make_media_id  # noqa: E402
from onevoicecut.domain.media import SourceMedia  # noqa: E402
from onevoicecut.ports.capabilities import DetectionSupport  # noqa: E402

# The install probe is `find_spec`-based, so this costs no torch import on a
# checkout without the extras — it skips them instead, at collection. The
# weights are not gated, but an uncached 167 MB download is not a test-suite
# cost either; the probe says so and this honours it.
if not (is_installed() and weights_cached()):
    pytest.skip(
        "vision extras or cached weights absent (requirements-vision.txt)",
        allow_module_level=True,
    )

pytestmark = pytest.mark.localmodel

MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFH")

FIXTURE_SECONDS = 6.0
SPAN = TimeSpan(start_s=2.0, end_s=4.0)


def _synthesize(dest: Path) -> None:
    """A small, deterministic, genuinely person-free video."""
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y",
            "-f", "lavfi",
            "-i", f"testsrc2=size=320x240:rate=25:duration={FIXTURE_SECONDS}",
            "-pix_fmt", "yuv420p",
            str(dest),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )


def _media_at(path: Path) -> SourceMedia:
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="sermon.mp4",
        stored_path=path,
        size_bytes=path.stat().st_size if path.exists() else 0,
        container="mp4",
        checksum="0" * 64,
    )


@pytest.fixture
def source(tmp_path: Path, ffmpeg_available: None) -> SourceMedia:
    video = tmp_path / "source.mp4"
    _synthesize(video)
    return _media_at(video)


def test_detect_covers_only_the_requested_span(source: SourceMedia) -> None:
    """13c.1: the decode is scoped to the clip, and the times are clip-local.

    A detector that decoded the whole source and returned everything would put
    samples four seconds past the span's start; one that returned
    source-absolute times would put them two seconds past it. Both fail here.
    """
    tracker = TorchvisionSubjectTracker(max_clip_seconds=180.0)

    detections = tracker.detect(source, SPAN, sample_hz=2.0)

    assert detections, "a non-empty span always gets at least one sample"
    assert all(0.0 <= detection.at_s <= SPAN.duration_s for detection in detections)
    times = [detection.at_s for detection in detections]
    assert times == sorted(set(times)), "samples advance, and never repeat"

    # testsrc2 carries no person, so every sample is an explicit miss — the
    # spec's "reported, never guessed", on a genuinely person-free fixture.
    assert all(detection.box is None for detection in detections)


def test_an_over_long_span_is_refused_before_any_decode(
    source: SourceMedia, tmp_path: Path
) -> None:
    """13c.3: the guard fires before the file is even opened.

    Proven twice: against the real fixture, where a decode would have been
    possible, and against a path that does not exist, where the *same* refusal
    comes back — which it could only do if the span was judged first.
    """
    tracker = TorchvisionSubjectTracker(max_clip_seconds=5.0)
    over_long = TimeSpan(start_s=0.0, end_s=30.0)

    with pytest.raises(DetectionFailed) as refusal:
        tracker.detect(source, over_long, sample_hz=2.0)
    assert "5.0" in str(refusal.value), "the refusal names the ceiling it enforces"

    absent = tmp_path / "absent"
    with pytest.raises(DetectionFailed) as before_open:
        tracker.detect(_media_at(absent), over_long, sample_hz=2.0)
    assert str(before_open.value) == str(refusal.value)


def test_capabilities_declares_available_on_a_real_install() -> None:
    """13c.5, the branch a bare checkout can only prove as arithmetic."""
    capabilities = TorchvisionSubjectTracker(max_clip_seconds=180.0).capabilities()

    assert capabilities.tracker_id == TRACKER_ID
    assert capabilities.detection is DetectionSupport.AVAILABLE


def test_capabilities_declares_requires_setup_when_the_extras_are_absent() -> None:
    """13c.5's other branch, simulated through the probe's injected finder.

    This machine has the extras, so absence is handed to the probe rather than
    uninstalled; the decision function itself is proven both ways, on facts
    instead of a machine, in the default suite.
    """
    tracker = TorchvisionSubjectTracker(
        max_clip_seconds=180.0, finder=lambda name: None
    )

    assert tracker.capabilities().detection is DetectionSupport.REQUIRES_SETUP


def test_the_pinned_vision_facts_match_the_installed_package() -> None:
    """The two constants introspection could not settle are pinned to it here.

    `PERSON_LABEL` is read out of the weights' own category list rather than
    trusted from a probe's arithmetic, and `WEIGHTS_FILENAME` is the basename of
    the pinned weights URL — the file the capability probe looks for in the
    torch hub cache. A torchvision upgrade that moved either fails this test
    instead of mislabelling people or mis-declaring the install.
    """
    from torchvision.models.detection import FasterRCNN_ResNet50_FPN_V2_Weights

    weights = FasterRCNN_ResNet50_FPN_V2_Weights.COCO_V1

    assert weights.meta["categories"][PERSON_LABEL] == "person"
    assert PurePosixPath(weights.url).name == WEIGHTS_FILENAME
