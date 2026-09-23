"""Whether this install can locate the preacher, answered without loading anything.

Split out of the adapter for the reason `adapters/asr/local/declarations.py` was
split: this is a question *about* which extras a machine carries, so it must be
readable — and testable — on a checkout carrying none. The adapter module keeps
its own imports lazy, but the probe's decision function belongs where the
default suite can drive it with facts instead of with an install.

**`REQUIRES_SETUP` is the only honest answer for a missing fact, and there are
two facts.** The packages (`torchvision` and `av`) are the same kind of fact
diarization probes. The cached weights are the second, and they are *not* the
same as pyannote's gated models: nothing here needs a licence or a token, and
the 167 MB download from `download.pytorch.org` is unattended. It is still
probed, because the alternative is declaring `AVAILABLE` on a machine whose
first clip then stalls mid-render on a download — possibly with no network at
all — and "install the extras and let the weights download" is the exact
remediation `DetectionSupport.REQUIRES_SETUP` was written to name.

**This is a probe, not a proof.** The packages being importable and the weights
file existing say the setup is plausible, not that a forward pass will run —
slice 7c's lesson, restated on a third axis. The proof is paid at the first
`detect()`, by the clip that actually asked for tracking, never by a
`capabilities()` read.
"""

import importlib.util
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from onevoicecut.ports.capabilities import DetectionSupport

# The two packages `requirements-vision.txt` pins. torchvision 0.29 removed its
# own video APIs (no `read_video`, no `VideoReader`), so decoding goes through
# PyAV directly — which makes `av` a first-class extra, not a transitive detail.
VISION_PACKAGES = ("torchvision", "av")

# Basename of the pinned weights URL
# (FasterRCNN_ResNet50_FPN_V2_Weights.COCO_V1.url), and therefore the name
# torch hubs it under. Pinned here rather than read from torchvision, because
# reading it would import the very package whose absence is being probed; a
# `localmodel` test asserts the two still agree, so an upgrade that moves the
# file fails a test instead of silently declaring every install incomplete.
WEIGHTS_FILENAME = "fasterrcnn_resnet50_fpn_v2_coco-dd69338a.pth"

# Named for the refusal, not read here — the same split as `HF_TOKEN_ENV`.
VISION_REQUIREMENTS = "requirements-vision.txt"

SpecFinder = Callable[[str], Any]


def is_installed(*, finder: SpecFinder = importlib.util.find_spec) -> bool:
    """Are both vision packages importable, without importing either.

    `find_spec` rather than a `try: import` for the cost reason its ASR twin
    records: importing torchvision pulls torch, hundreds of megabytes and
    seconds, on a call whose whole purpose is to be cheap enough to make before
    deciding anything. Both package names are top-level, so the dotted-name
    parent-import gotcha does not reach them — but the `except` stays, because
    a half-initialised or namespace-shadowed install raises `ValueError` here
    exactly as it does there, and crashing the probe on the machines it exists
    to describe is the failure both modules were written to refuse.
    """
    try:
        return all(finder(package) is not None for package in VISION_PACKAGES)
    except (ImportError, ValueError):
        return False


def default_hub_dir() -> Path:
    """Where torch caches downloaded weights, resolved without importing torch.

    Mirrors `torch.hub.get_dir()`'s own precedence — `TORCH_HOME` wins; below
    it, `XDG_CACHE_HOME` or `~/.cache`, then `torch/hub`. A probe that guessed
    a single location would declare `REQUIRES_SETUP` on every machine that
    moved the cache, sending its operator to reinstall extras that are there.

    These are *torch's own* variables, not project configuration, which is why
    this read lives here rather than at a composition root: the probe predicts
    where the library itself will look, and reading less than the library
    reads would make it answer about a directory the download never uses.
    """
    torch_home = os.environ.get("TORCH_HOME")
    if torch_home:
        return Path(torch_home) / "hub"
    xdg_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg_cache) if xdg_cache else Path.home() / ".cache"
    return base / "torch" / "hub"


def weights_cached(*, hub_dir: Path | None = None) -> bool:
    """Is the pinned weights file already on disk.

    A path check, never a download: reading the declaration must not fetch
    167 MB, the same way reading the diarization declaration must not build a
    pipeline.
    """
    directory = hub_dir if hub_dir is not None else default_hub_dir()
    return (directory / "checkpoints" / WEIGHTS_FILENAME).is_file()


def detection_support(*, installed: bool, weights: bool) -> DetectionSupport:
    """The declaration, from the two facts that decide it.

    Both are required. Packages without weights is a first clip that stalls on
    a download; weights without packages is nothing at all. `UNSUPPORTED` is
    deliberately unreachable from here: it is a claim about a *build* — "no
    vision adapter exists in it" — and this build ships one, so the worst an
    install can honestly declare is that it is not set up yet.
    """
    if not installed or not weights:
        return DetectionSupport.REQUIRES_SETUP
    return DetectionSupport.AVAILABLE
