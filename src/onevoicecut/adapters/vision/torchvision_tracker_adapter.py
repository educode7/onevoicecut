"""`SubjectTrackerPort` over real frames: torchvision's Faster R-CNN, decoded in-process through PyAV.

The threat-matrix row this module answers names three ways a vision pass can be
ruinous, and each gets a structural response rather than a comment. Decoding
goes through PyAV **in-process** — never an `ffmpeg | python` pipe of raw
frames, the binding constraint `VideoRenderPort`'s single-process rule states
from the other side; torchvision 0.29 removed its own video APIs, which makes
PyAV the sanctioned decode path rather than a preference. Sampling is scoped to
the **clip span**: a seek to its start and a break at its end, so the cost of a
clip never depends on the length of the sermon it came from. And frames are
evaluated **every Nth**, downscaled to a 640px long edge, one at a time — no
reference outlives the loop iteration, so nothing buffers the span.

**Boxes come back in source-frame pixels.** Inference runs on the downscaled
frame because the model's cost is a function of pixels, but `BoundingBox`
promises source-frame pixels and the render pass crops in source geometry — a
box left in downscaled coordinates would place the crop window at a fraction of
the frame and read as a subject standing in a corner. The rescale happens here,
once, so nothing downstream has to know inference ran small.

**Times are clip-local and come from each frame's own timestamp**, never from a
nominal grid: the port's invariant is the same pairing `AudioExtractorPort` and
`TranscriptionPort` ship, and a frame's presentation time is the only honest
answer to "when is this picture". Frames the seek lands on before the span
starts — keyframe pre-roll — are decoded and dropped; that is what makes the
first sample truthful rather than merely first.

**A miss is `box=None`, and the score floor is not a policy.**
`MIN_PERSON_SCORE` is the floor under which nothing was *located at all*;
whether a weak hit is good enough to frame on is `plan_trajectory`'s decision
and never this module's. `PERSON_LABEL` is pinned to the installed weights' own
category list by a `localmodel` test, because torchvision's COCO labels are
1-based with `__background__` at 0 and a probe's arithmetic once claimed
otherwise.

Everything heavy is imported inside a function; the module itself costs what
`ports.capabilities` costs. The probe/proof split is the diarizer's: reading
`capabilities()` never builds the model — the proof is paid at the first
`detect()`, once per adapter, and an adapter lives for one render.
"""

import importlib.util
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from onevoicecut.adapters.vision.declarations import (
    VISION_REQUIREMENTS,
    WEIGHTS_FILENAME,
    SpecFinder,
    detection_support,
    is_installed,
    weights_cached,
)
from onevoicecut.domain.errors import DetectionFailed, TrackingUnavailable
from onevoicecut.domain.framing import TimeSpan
from onevoicecut.domain.media import SourceMedia
from onevoicecut.ports.capabilities import DetectionSupport, TrackerCapabilities
from onevoicecut.ports.subject_tracker import BoundingBox, SubjectDetection

TRACKER_ID = "torchvision-fasterrcnn"

# COCO's person class, 1-based: torchvision detection models output the COCO
# category id, and the weights' own category list carries `__background__` at
# index 0 and `person` at 1. Pinned by introspection test, never by memory.
PERSON_LABEL = 1

# The floor under which nothing was located at all — not the trajectory's
# confidence policy, which lives in `plan_trajectory` and never here. Inclusive:
# a floor that refused the score it names would be wrong by exactly that score.
MIN_PERSON_SCORE = 0.5

# The long edge every frame is downscaled to before inference. Named, like
# every knob that changes what a viewer eventually sees.
DOWNSCALE_MAX_EDGE = 640

# libav's `AV_TIME_BASE`, the unit container-level seeks are expressed in.
# Pinned here rather than read from `av` so that the decode path stays
# importable — and its boundary provable — on a checkout without the extra.
AV_TIME_BASE = 1_000_000

# Injected in tests so the lifecycle below is provable with no weights, no
# decoder and no torch anywhere near the default suite — the diarizer's
# `PipelineLoader` seam, on this axis.
ModelLoader = Callable[[str], Any]
ContainerOpener = Callable[[Path], Any]


def _load_model(device: str) -> Any:
    """The one place torchvision is imported for inference.

    The weights are not gated — unlike pyannote's, they download unattended
    from `download.pytorch.org` on first use and cache in the torch hub
    directory, which is exactly the download the capability probe exists to
    declare *before* a clip is dispatched to it.
    """
    from torchvision.models.detection import (
        FasterRCNN_ResNet50_FPN_V2_Weights,
        fasterrcnn_resnet50_fpn_v2,
    )

    model = fasterrcnn_resnet50_fpn_v2(
        weights=FasterRCNN_ResNet50_FPN_V2_Weights.COCO_V1
    )
    model.to(device)
    model.eval()
    return model


def _open_container(path: Path) -> Any:
    """The one place PyAV is imported. Inside a function, like every heavy
    import here: this module is imported by the composition root."""
    import av

    return av.open(str(path))


def sample_step(fps: float, sample_hz: float) -> int:
    """How many decoded frames one sample is worth: `N = round(fps / sample_hz)`.

    At least 1, because a source filmed slower than the sampling rate still
    owes an answer per frame it has; at 25 fps and 4 Hz, every 6th frame. The
    step is derived from the stream's own declared rate rather than assumed, so
    a 30 fps source is not silently sampled at 25.
    """
    if fps <= 0.0 or sample_hz <= 0.0:
        raise ValueError(
            f"cannot derive a sample step from fps={fps} at sample_hz={sample_hz}; "
            "both rates must be positive"
        )
    return max(1, round(fps / sample_hz))


def scaled_size(
    width: int, height: int, *, max_edge: int = DOWNSCALE_MAX_EDGE
) -> tuple[int, int]:
    """The frame's size, capped at `max_edge` on its long edge.

    Never upscales: a source smaller than the cap goes to the model as it is,
    because inventing pixels to reach 640 would spend inference on detail that
    was never filmed.
    """
    long_edge = max(width, height)
    if long_edge <= max_edge:
        return (width, height)
    factor = max_edge / long_edge
    return (max(1, round(width * factor)), max(1, round(height * factor)))


def best_person(
    boxes: Sequence[Sequence[float]],
    labels: Sequence[int],
    scores: Sequence[float],
    *,
    source_size: tuple[int, int],
    scaled: tuple[int, int],
) -> tuple[BoundingBox, float] | None:
    """One frame's prediction reduced to the port's answer, in pure data.

    The highest-scoring person at or above the floor, rescaled from the
    downscaled frame back to source pixels; `None` when nothing qualifies —
    which the caller turns into an explicit miss, never a centred guess. Plain
    sequences at the boundary so this arithmetic is provable in the default
    suite: torch tensors are `.tolist()`-ed before they reach here, and no
    provider type crosses it.

    Coordinates are not clamped to the frame. A detection box may hang a few
    pixels over an edge — the model's own rounding — and the trajectory's clamp
    stage is what owns in-frame geometry, with a proof; clamping here too would
    be a second, weaker answer to the same question.
    """
    best_box: Sequence[float] | None = None
    best_score = MIN_PERSON_SCORE
    for box, label, score in zip(boxes, labels, scores, strict=True):
        if label != PERSON_LABEL or score < best_score:
            continue
        best_box, best_score = box, score
    if best_box is None:
        return None

    x1, y1, x2, y2 = (float(value) for value in best_box)
    scale_x = source_size[0] / scaled[0]
    scale_y = source_size[1] / scaled[1]
    return (
        BoundingBox(
            x=int(x1 * scale_x),
            y=int(y1 * scale_y),
            width=int((x2 - x1) * scale_x),
            height=int((y2 - y1) * scale_y),
        ),
        float(best_score),
    )


def _frame_rate(stream: Any) -> float:
    """The stream's declared rate, or its guessed one, or a refusal.

    `DetectionFailed` rather than an assumption: a default of 25 would sample a
    30 fps source at the wrong spacing and nothing downstream could tell — the
    same silent substitution every axis in this system refuses.
    """
    rate = stream.average_rate if stream.average_rate is not None else stream.guessed_rate
    if rate is None or float(rate) <= 0.0:
        raise DetectionFailed(
            "the video stream declares no frame rate, so no sample spacing can "
            "be derived from it"
        )
    return float(rate)


def _evaluate(model: Any, frame: Any, *, at_s: float, device: str) -> SubjectDetection:
    """One frame: downscale, infer, reduce. The frame is not retained."""
    import torch
    from torch.nn.functional import interpolate

    array = frame.to_ndarray(format="rgb24")
    source_h, source_w = int(array.shape[0]), int(array.shape[1])
    target_w, target_h = scaled_size(source_w, source_h)

    tensor = torch.from_numpy(array).permute(2, 0, 1).float().div(255.0)
    if (target_w, target_h) != (source_w, source_h):
        tensor = interpolate(
            tensor.unsqueeze(0),
            size=(target_h, target_w),
            mode="bilinear",
            align_corners=False,
        )[0]

    with torch.no_grad():
        prediction = model([tensor.to(device)])[0]

    answer = best_person(
        prediction["boxes"].tolist(),
        prediction["labels"].tolist(),
        prediction["scores"].tolist(),
        source_size=(source_w, source_h),
        scaled=(target_w, target_h),
    )
    if answer is None:
        return SubjectDetection(at_s=at_s, box=None, confidence=None)
    box, confidence = answer
    return SubjectDetection(at_s=at_s, box=box, confidence=confidence)


class TorchvisionSubjectTracker:
    """The real detection adapter. One render process, one clip, one model."""

    def __init__(
        self,
        *,
        max_clip_seconds: float,
        device: str = "cpu",
        finder: SpecFinder = importlib.util.find_spec,
        hub_dir: Path | None = None,
        model_loader: ModelLoader = _load_model,
        opener: ContainerOpener = _open_container,
    ) -> None:
        # Required, never defaulted: this is the same ceiling `render_clip`
        # enforces and `RenderCapabilities` declares, and a tracker that picked
        # its own would make two answers to one question.
        self._max_clip_seconds = max_clip_seconds
        self._device = device
        self._finder = finder
        self._hub_dir = hub_dir
        self._loader = model_loader
        self._opener = opener
        self._model: Any | None = None

    def capabilities(self) -> TrackerCapabilities:
        """The declaration, from the probe — which is a probe, not a proof.

        Reading this never builds the model and never downloads anything: the
        proof is paid at the first `detect()`, by the clip that actually asked
        for tracking, exactly as the diarizer pays its pipeline there and not
        in `capabilities()`.
        """
        return TrackerCapabilities(
            tracker_id=TRACKER_ID,
            detection=detection_support(
                installed=is_installed(finder=self._finder),
                weights=weights_cached(hub_dir=self._hub_dir),
            ),
        )

    def detect(
        self, media: SourceMedia, span: TimeSpan, *, sample_hz: float
    ) -> tuple[SubjectDetection, ...]:
        self._refuse_over_long(span)
        if span.duration_s <= 0.0:
            # A request to look at nothing is answerable — `TimeSpan` keeps
            # zero length legal and the fake answers it the same way.
            return ()

        support = self.capabilities().detection
        if support is not DetectionSupport.AVAILABLE:
            raise TrackingUnavailable(
                f"{TRACKER_ID} declares {support.value}: install "
                f"{VISION_REQUIREMENTS} and cache the weights "
                f"({WEIGHTS_FILENAME}) once, or render this clip without a "
                f"reframe"
            )

        model = self._built_model()
        try:
            container = self._opener(media.stored_path)
        except Exception as error:
            raise DetectionFailed(
                f"the source {media.stored_path.name} could not be opened for "
                f"detection: {type(error).__name__}: {error}"
            ) from error

        try:
            return self._over_span(model, container, span, sample_hz=sample_hz)
        except DetectionFailed:
            raise
        except Exception as error:
            # The decoder's and the framework's exceptions stop here, the way
            # provider exceptions stop at every other adapter boundary: a
            # caller must never have to catch a vision library's own type to
            # survive one bad clip in eighty.
            raise DetectionFailed(
                f"detection failed on {media.stored_path.name}: "
                f"{type(error).__name__}: {error}"
            ) from error
        finally:
            container.close()

    def _refuse_over_long(self, span: TimeSpan) -> None:
        """The threat matrix's "multi-hour source handed to the tracker", refused
        before the file is opened.

        `render_worker` already runs `check_clip_range` above this port, so in
        production the guard is a backstop; it exists because a bound only the
        caller enforces is a bound any second caller forgets. **Before the
        declaration and before the model**, because an over-long span is
        nonsense on any install, and the precise answer beats the general one.

        `DetectionFailed`, of the two errors the port declares: an over-long
        span is a property of *this clip request*, not of the build —
        `TrackingUnavailable` would send the operator to install extras that
        are already there. `ClipRangeInvalid` would fit the semantics but is
        the render side's vocabulary, and this port does not declare it.
        """
        if span.duration_s > self._max_clip_seconds:
            raise DetectionFailed(
                f"clip span {span.start_s}s..{span.end_s}s is "
                f"{span.duration_s}s long, past the {self._max_clip_seconds}s "
                f"ceiling this tracker enforces; refused before any decode, "
                f"because detection cost is bounded by clip length, never by "
                f"the length of the source"
            )

    def _built_model(self) -> Any:
        """The model, built on the first detect and held for the adapter's life.

        Never caches a failure, and never accepts `None` as a model — both are
        the diarizer's lifecycle rules, and the second exists because a loader
        that returns nothing is a refusal wearing a success shape.
        """
        if self._model is None:
            try:
                built = self._loader(self._device)
            except Exception as error:
                raise TrackingUnavailable(
                    f"the vision model behind {TRACKER_ID} could not be built: "
                    f"{error}; install {VISION_REQUIREMENTS} and let the "
                    f"weights ({WEIGHTS_FILENAME}) download once"
                ) from error
            if built is None:
                raise TrackingUnavailable(
                    f"the vision model behind {TRACKER_ID} could not be built: "
                    f"the loader returned nothing for weights that should exist"
                )
            self._model = built
        return self._model

    def _over_span(
        self, model: Any, container: Any, span: TimeSpan, *, sample_hz: float
    ) -> tuple[SubjectDetection, ...]:
        """The scoped decode: seek to the span, evaluate every Nth frame of it,
        stop at its end.

        An empty result is a `DetectionFailed`, not an answer: a zero-sample
        series reads exactly like a subject who never moved, which is the
        misreading the miss axis exists to prevent — so a span the picture
        never reaches (a video stream shorter than the container, a seek past
        the last frame) is refused by name instead.
        """
        streams = container.streams.video
        if not streams:
            raise DetectionFailed(
                f"the source {span.start_s}s..{span.end_s}s was asked of a "
                "container that carries no video stream"
            )
        stream = streams[0]
        step = sample_step(_frame_rate(stream), sample_hz)

        container.seek(int(span.start_s * AV_TIME_BASE))
        detections: list[SubjectDetection] = []
        index = 0
        for frame in container.decode(stream):
            if frame.pts is None:
                # A frame that cannot say when it was cannot become a sample;
                # skipping it is honest, guessing its time would not be.
                continue
            at_source_s = float(frame.pts * stream.time_base)
            if at_source_s < span.start_s:
                continue  # keyframe pre-roll the backward seek lands on
            if at_source_s > span.end_s:
                break  # the span, not the source, bounds this loop
            if index % step == 0:
                detections.append(
                    _evaluate(
                        model, frame, at_s=at_source_s - span.start_s, device=self._device
                    )
                )
            index += 1

        if not detections:
            raise DetectionFailed(
                f"no frame decoded inside {span.start_s}s..{span.end_s}s, so "
                "there is nothing to answer with"
            )
        return tuple(detections)
