"""The vision tracker's decidable half: no weights, no decode, no torch.

Everything here runs on a bare checkout, mirroring the split slice 9a-i
established for the diarization probe: pure decision functions plus injected
seams — the spec finder, the hub directory, the model loader, the container
opener — prove the sampling arithmetic, the person filter, the coordinate
rescale, the span guard, the capability declaration and the model lifecycle in
the default suite. `test_vision_tracker_real.py` proves the one claim only the
installed extras can make, and the structural tests here prove the absences no
request can: no subprocess anywhere under `adapters/vision`, and no heavy
extra imported at module scope.
"""

import ast
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from onevoicecut.adapters.vision import torchvision_tracker_adapter
from onevoicecut.adapters.vision.declarations import (
    VISION_PACKAGES,
    WEIGHTS_FILENAME,
    default_hub_dir,
    detection_support,
    is_installed,
    weights_cached,
)
from onevoicecut.adapters.vision.torchvision_tracker_adapter import (
    TRACKER_ID,
    TorchvisionSubjectTracker,
    best_person,
    sample_step,
    scaled_size,
)
from onevoicecut.domain.errors import DetectionFailed, TrackingUnavailable
from onevoicecut.domain.framing import TimeSpan
from onevoicecut.domain.ids import make_media_id
from onevoicecut.domain.media import SourceMedia
from onevoicecut.ports.capabilities import DetectionSupport
from onevoicecut.ports.subject_tracker import BoundingBox

REPO_ROOT = Path(__file__).resolve().parents[4]
VISION_DIR = REPO_ROOT / "src" / "onevoicecut" / "adapters" / "vision"
HEAVY_EXTRAS = {"torch", "torchvision", "av", "numpy"}

MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
SPAN = TimeSpan(start_s=2.0, end_s=4.0)


def a_media(tmp_path: Path) -> SourceMedia:
    stored = tmp_path / "source"
    stored.write_bytes(b"not really a video")
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="sermon.mp4",
        stored_path=stored,
        size_bytes=stored.stat().st_size,
        container="mp4",
        checksum="0" * 64,
    )


def imported_names(source: Path, *, top_level_only: bool) -> set[str]:
    """Imported root packages, parsed rather than imported.

    The same argument the architecture test makes: this states a fact about the
    file itself, and cannot be satisfied by an import that merely happened to
    succeed on this machine.
    """
    tree = ast.parse(source.read_text(encoding="utf-8"))
    nodes: list[ast.AST] = list(tree.body) if top_level_only else list(ast.walk(tree))
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


@pytest.fixture
def ready(tmp_path: Path) -> dict[str, Any]:
    """Constructor kwargs simulating a fully set-up machine, with no weights.

    A finder that answers for both packages and a hub directory carrying the
    pinned weights filename: the two facts the probe reads, faked at the seam
    the probe already has.
    """
    hub = tmp_path / "hub"
    (hub / "checkpoints").mkdir(parents=True)
    (hub / "checkpoints" / WEIGHTS_FILENAME).write_bytes(b"weights")
    return {"finder": lambda name: object(), "hub_dir": hub}


def a_tracker(**kwargs: Any) -> TorchvisionSubjectTracker:
    kwargs.setdefault("max_clip_seconds", 180.0)
    return TorchvisionSubjectTracker(**kwargs)


class TestNoSubprocessAndNoEagerWeights:
    def test_no_vision_module_imports_subprocess(self) -> None:
        """The "no subprocess pipe of raw frames" half of 13c.1.

        Structural, like the shipped "no `UploadFile` in `adapters/web`" test,
        for the reason that test records: an absence cannot be proven by a
        request. A raw-frame pipe needs `subprocess`; no module here names it.
        """
        modules = tuple(VISION_DIR.glob("*.py"))
        assert modules, "the vision adapter package exists"
        for module in modules:
            assert "subprocess" not in imported_names(module, top_level_only=False), (
                f"{module.name} imports subprocess; the binding constraint "
                "forbids piping raw frames through a child process"
            )

    def test_the_adapter_imports_no_heavy_extra_at_module_scope(self) -> None:
        """The composition root imports this module; the weights it must not.

        Mirrors `test_engine_registration.py`'s laziness proof one level down:
        torch, torchvision, av and numpy are imported inside functions only, so
        importing the adapter costs what importing `ports.capabilities` costs.
        """
        source = Path(torchvision_tracker_adapter.__file__)
        assert not (imported_names(source, top_level_only=True) & HEAVY_EXTRAS)

    def test_declarations_imports_nothing_heavier_than_the_ports(self) -> None:
        """The 9a-i invariant, restated for the vision probe: this module is a
        question *about* which extras a machine carries, so it must be readable
        on a machine carrying none."""
        declarations = VISION_DIR / "declarations.py"
        allowed = {"importlib", "os", "pathlib", "collections", "typing", "onevoicecut"}
        assert imported_names(declarations, top_level_only=False) <= allowed


class TestSampleStep:
    @pytest.mark.parametrize(
        ("fps", "sample_hz", "expected"),
        [
            (25.0, 4.0, 6),  # design.md's figures
            (25.0, 2.0, 12),
            (30.0, 4.0, 8),  # round(7.5) — banker's rounding lands on 8
            (1.0, 4.0, 1),  # sampling faster than frames exist: every frame
            (25.0, 25.0, 1),
        ],
    )
    def test_step(self, fps: float, sample_hz: float, expected: int) -> None:
        assert sample_step(fps, sample_hz) == expected

    @pytest.mark.parametrize(("fps", "sample_hz"), [(0.0, 4.0), (25.0, 0.0), (-1.0, 4.0)])
    def test_nonsense_rates_are_refused(self, fps: float, sample_hz: float) -> None:
        with pytest.raises(ValueError):
            sample_step(fps, sample_hz)


class TestScaledSize:
    def test_a_wide_frame_is_downscaled_by_its_long_edge(self) -> None:
        assert scaled_size(1920, 1080) == (640, 360)

    def test_a_tall_frame_is_downscaled_by_its_long_edge(self) -> None:
        assert scaled_size(1080, 1920) == (360, 640)

    def test_a_frame_already_small_enough_is_untouched(self) -> None:
        assert scaled_size(320, 240) == (320, 240)

    def test_the_ceiling_itself_is_untouched(self) -> None:
        assert scaled_size(640, 480) == (640, 480)


class TestBestPerson:
    """The detection filter, in pure data: no torch tensor crosses this seam."""

    SOURCE = (1920, 1080)
    SCALED = (640, 360)

    def pick(
        self,
        boxes: list[list[float]],
        labels: list[int],
        scores: list[float],
    ) -> tuple[BoundingBox, float] | None:
        return best_person(
            boxes, labels, scores, source_size=self.SOURCE, scaled=self.SCALED
        )

    def test_a_person_free_frame_is_no_answer(self) -> None:
        assert (
            self.pick(
                [[0.0, 0.0, 100.0, 100.0], [0.0, 0.0, 50.0, 50.0]],
                [7, 3],
                [0.99, 0.9],
            )
            is None
        )

    def test_a_person_below_the_floor_is_no_answer(self) -> None:
        assert self.pick([[0.0, 0.0, 10.0, 20.0]], [1], [0.49]) is None

    def test_the_floor_itself_is_a_hit(self) -> None:
        answer = self.pick([[0.0, 0.0, 10.0, 20.0]], [1], [0.5])
        assert answer is not None

    def test_the_highest_scoring_person_wins(self) -> None:
        answer = self.pick(
            [[0.0, 0.0, 10.0, 10.0], [100.0, 100.0, 200.0, 300.0]],
            [1, 1],
            [0.6, 0.95],
        )
        assert answer is not None
        box, confidence = answer
        assert confidence == pytest.approx(0.95)
        # 100,100 -> 200,300 in a frame downscaled 3x from 1920x1080.
        assert (box.x, box.y, box.width, box.height) == (300, 300, 300, 600)

    def test_boxes_are_rescaled_to_source_pixels(self) -> None:
        """The port promises source-frame pixels, and inference ran on a frame
        downscaled 3x — the box that comes back is 3x too small until it is
        rescaled, and the render pass crops in source geometry."""
        answer = self.pick([[10.0, 20.0, 110.0, 320.0]], [1], [0.8])
        assert answer is not None
        box, _ = answer
        assert (box.x, box.y, box.width, box.height) == (30, 60, 300, 900)

    def test_a_non_person_above_the_floor_never_wins(self) -> None:
        assert self.pick([[0.0, 0.0, 50.0, 50.0]], [2], [1.0]) is None


class TestTheProbeIsCheapAndHonest:
    def test_the_extras_absent_declares_requires_setup(self) -> None:
        tracker = a_tracker(finder=lambda name: None, hub_dir=None)
        capabilities = tracker.capabilities()
        assert capabilities.tracker_id == TRACKER_ID
        assert capabilities.detection is DetectionSupport.REQUIRES_SETUP

    def test_the_weights_absent_declares_requires_setup(self, tmp_path: Path) -> None:
        tracker = a_tracker(finder=lambda name: object(), hub_dir=tmp_path / "empty")
        assert tracker.capabilities().detection is DetectionSupport.REQUIRES_SETUP

    def test_both_facts_present_declares_available(self, ready: dict[str, Any]) -> None:
        assert a_tracker(**ready).capabilities().detection is DetectionSupport.AVAILABLE

    def test_reading_capabilities_never_builds_the_model(
        self, ready: dict[str, Any]
    ) -> None:
        """Probe, not proof — the 7c/9a-i lesson. A caller that only wanted the
        declaration must not pay the fifteen seconds and 167 MB."""

        def loader(device: str) -> Any:
            pytest.fail("capabilities() built the model")

        tracker = a_tracker(**ready, model_loader=loader)
        assert tracker.capabilities().detection is DetectionSupport.AVAILABLE


class TestTheDecisionFunction:
    """The pure half, on facts rather than on a machine — both ways."""

    @pytest.mark.parametrize(
        ("installed", "weights"),
        [(False, False), (False, True), (True, False)],
    )
    def test_any_missing_fact_is_requires_setup(
        self, installed: bool, weights: bool
    ) -> None:
        assert (
            detection_support(installed=installed, weights=weights)
            is DetectionSupport.REQUIRES_SETUP
        )

    def test_both_facts_is_available(self) -> None:
        assert (
            detection_support(installed=True, weights=True) is DetectionSupport.AVAILABLE
        )

    def test_is_installed_answers_for_every_package(self) -> None:
        seen: list[str] = []

        def finder(name: str) -> Any:
            seen.append(name)
            return object()

        assert is_installed(finder=finder)
        assert set(seen) == set(VISION_PACKAGES)

    def test_a_finder_that_raises_means_not_installed(self) -> None:
        def finder(name: str) -> Any:
            raise ModuleNotFoundError(name)

        assert not is_installed(finder=finder)

    def test_weights_cached_reads_the_pinned_filename(self, tmp_path: Path) -> None:
        hub = tmp_path / "hub"
        (hub / "checkpoints").mkdir(parents=True)
        assert not weights_cached(hub_dir=hub)
        (hub / "checkpoints" / WEIGHTS_FILENAME).write_bytes(b"weights")
        assert weights_cached(hub_dir=hub)

    def test_the_default_hub_dir_honours_torch_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("TORCH_HOME", str(tmp_path / "custom"))
        assert default_hub_dir() == tmp_path / "custom" / "hub"


class TestTheSpanGuard:
    def test_an_over_long_span_is_refused_before_the_declaration(
        self, tmp_path: Path
    ) -> None:
        """Guard first, on purpose: an over-long span is nonsense on any
        install, and the ceiling refusal is the more precise answer."""
        tracker = a_tracker(max_clip_seconds=30.0, finder=lambda name: None)
        with pytest.raises(DetectionFailed) as refusal:
            tracker.detect(a_media(tmp_path), TimeSpan(0.0, 60.0), sample_hz=2.0)
        assert "30.0" in str(refusal.value)

    def test_an_over_long_span_is_refused_before_the_model_is_built(
        self, ready: dict[str, Any], tmp_path: Path
    ) -> None:
        def loader(device: str) -> Any:
            pytest.fail("the span guard let a build happen")

        tracker = a_tracker(**ready, max_clip_seconds=30.0, model_loader=loader)
        with pytest.raises(DetectionFailed):
            tracker.detect(a_media(tmp_path), TimeSpan(0.0, 31.0), sample_hz=2.0)

    def test_a_zero_length_span_is_answerable_with_nothing(
        self, ready: dict[str, Any], tmp_path: Path
    ) -> None:
        """`TimeSpan` keeps zero length legal — a request to look at nothing —
        and the fake detector answers it with no samples. So does the real one,
        without building anything."""
        tracker = a_tracker(**ready)
        assert tracker.detect(a_media(tmp_path), TimeSpan(5.0, 5.0), sample_hz=4.0) == ()


class TestDetectRefusals:
    def test_a_declaration_that_is_not_available_refuses(
        self, tmp_path: Path
    ) -> None:
        """The backstop for a caller who ignored `capabilities()`: an empty
        detection series would read exactly like a subject who never moved.
        The failing loader also proves the refusal precedes the build — the
        declaration is read before the expensive step, not after."""

        def loader(device: str) -> Any:
            pytest.fail("an unavailable declaration built the model")

        tracker = a_tracker(
            finder=lambda name: None, hub_dir=None, model_loader=loader
        )
        with pytest.raises(TrackingUnavailable) as refusal:
            tracker.detect(a_media(tmp_path), SPAN, sample_hz=2.0)
        assert "requirements-vision.txt" in str(refusal.value)

    def test_a_model_that_cannot_be_built_refuses_by_name(
        self, ready: dict[str, Any], tmp_path: Path
    ) -> None:
        def loader(device: str) -> Any:
            raise RuntimeError("cuBLAS not found")

        tracker = a_tracker(**ready, model_loader=loader)
        with pytest.raises(TrackingUnavailable) as refusal:
            tracker.detect(a_media(tmp_path), SPAN, sample_hz=2.0)
        assert "cuBLAS" in str(refusal.value)

    def test_a_failed_build_is_never_cached(
        self, ready: dict[str, Any], tmp_path: Path
    ) -> None:
        calls = 0

        def loader(device: str) -> Any:
            nonlocal calls
            calls += 1
            raise RuntimeError("no")

        tracker = a_tracker(**ready, model_loader=loader)
        for _ in range(2):
            with pytest.raises(TrackingUnavailable):
                tracker.detect(a_media(tmp_path), SPAN, sample_hz=2.0)
        assert calls == 2, "a failure is not a model; rebuilding must stay possible"

    def test_a_loader_that_returns_nothing_refuses(
        self, ready: dict[str, Any], tmp_path: Path
    ) -> None:
        tracker = a_tracker(**ready, model_loader=lambda device: None)
        with pytest.raises(TrackingUnavailable):
            tracker.detect(a_media(tmp_path), SPAN, sample_hz=2.0)

    def test_an_unopenable_container_is_a_detection_failure(
        self, ready: dict[str, Any], tmp_path: Path
    ) -> None:
        """The provider's exception is translated at the boundary, never leaked
        — and it is `DetectionFailed` (this clip) rather than
        `TrackingUnavailable` (this build)."""

        def opener(path: Path) -> Any:
            raise OSError(f"could not open {path}")

        tracker = a_tracker(**ready, model_loader=lambda device: object(), opener=opener)
        with pytest.raises(DetectionFailed) as refusal:
            tracker.detect(a_media(tmp_path), SPAN, sample_hz=2.0)
        assert "OSError" in str(refusal.value)


class _FakeStreams:
    def __init__(self, video: list[Any]) -> None:
        self.video = video


class _FakeStream:
    def __init__(
        self, *, average_rate: Any = Fraction(25), guessed_rate: Any = None
    ) -> None:
        self.average_rate = average_rate
        self.guessed_rate = guessed_rate
        self.time_base = Fraction(1, 1000)


class _FakeFrame:
    def __init__(self, pts: int | None) -> None:
        self.pts = pts


class _FakeContainer:
    """The PyAV surface `detect` touches, and nothing more."""

    def __init__(self, stream: _FakeStream | None, pts_values: list[int | None]) -> None:
        self.streams = _FakeStreams([stream] if stream is not None else [])
        self._pts_values = pts_values
        self.seeks: list[int] = []
        self.closed = False

    def seek(self, offset: int) -> None:
        self.seeks.append(offset)

    def decode(self, stream: Any) -> Any:
        return iter([_FakeFrame(pts) for pts in self._pts_values])

    def close(self) -> None:
        self.closed = True


def _decode_tracker(container: _FakeContainer, ready: dict[str, Any]) -> Any:
    def opener(path: Path) -> Any:
        return container

    return a_tracker(**ready, model_loader=lambda device: object(), opener=opener)


class TestTheDecodeBoundary:
    """What the loop does around the frames, provable without evaluating any:
    every case here ends before `_evaluate`, so no torch is touched."""

    def test_the_seek_targets_the_span_start_in_microseconds(
        self, ready: dict[str, Any], tmp_path: Path
    ) -> None:
        container = _FakeContainer(_FakeStream(), [0, 1000])  # both before the span
        tracker = _decode_tracker(container, ready)

        with pytest.raises(DetectionFailed):
            tracker.detect(a_media(tmp_path), SPAN, sample_hz=1.0)

        assert container.seeks == [2_000_000]

    def test_a_span_no_frame_falls_inside_refuses(
        self, ready: dict[str, Any], tmp_path: Path
    ) -> None:
        """Empty is not an answer. A zero-sample series reads exactly like a
        subject who never moved, so a span the picture never reaches is a
        failure of this clip, named."""
        container = _FakeContainer(
            _FakeStream(), list(range(0, 2000, 40))  # 0.0s .. 1.96s
        )
        tracker = _decode_tracker(container, ready)

        with pytest.raises(DetectionFailed) as refusal:
            tracker.detect(a_media(tmp_path), SPAN, sample_hz=1.0)
        assert "no frame" in str(refusal.value)
        assert container.closed

    def test_a_container_with_no_video_stream_refuses(
        self, ready: dict[str, Any], tmp_path: Path
    ) -> None:
        tracker = _decode_tracker(_FakeContainer(None, []), ready)
        with pytest.raises(DetectionFailed) as refusal:
            tracker.detect(a_media(tmp_path), SPAN, sample_hz=1.0)
        assert "no video stream" in str(refusal.value)

    def test_a_stream_that_declares_no_frame_rate_refuses(
        self, ready: dict[str, Any], tmp_path: Path
    ) -> None:
        stream = _FakeStream(average_rate=None, guessed_rate=None)
        tracker = _decode_tracker(_FakeContainer(stream, [2500]), ready)
        with pytest.raises(DetectionFailed) as refusal:
            tracker.detect(a_media(tmp_path), SPAN, sample_hz=1.0)
        assert "frame rate" in str(refusal.value)

    def test_the_model_is_built_once_per_adapter(
        self, ready: dict[str, Any], tmp_path: Path
    ) -> None:
        calls = 0

        def loader(device: str) -> Any:
            nonlocal calls
            calls += 1
            return object()

        def opener(path: Path) -> Any:
            raise OSError("closed")

        tracker = a_tracker(**ready, model_loader=loader, opener=opener)
        for _ in range(2):
            with pytest.raises(DetectionFailed):
                tracker.detect(a_media(tmp_path), SPAN, sample_hz=1.0)
        assert calls == 1, "an adapter lives for one render; the weights load once"
