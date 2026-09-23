"""Turns "the tracker this render wants" into the adapter that will run it.

The engine resolver's shape, on an axis with one value: a factory that imports
the adapter on call and never on import, so a composition root can name the
tracker this build has without carrying torchvision into every process that
reads it. Tracker choice has no per-job key — one vision tracker exists — so
there is no mapping to resolve against and no `EngineUnavailable`-shaped
refusal; the honest absence here is *declared*, not raised.

**A bare checkout resolves to a tracker that declares `REQUIRES_SETUP`, and
that is the whole design.** The alternative — resolving to nothing, or to a
placeholder declaring `UNSUPPORTED` — would either fail the process or tell the
operator "no vision adapter exists in this build", which stopped being true the
day the adapter shipped. `REQUIRES_SETUP` names the actual remedy (install
`requirements-vision.txt`, cache the weights once), and
`render_worker._require_detection` turns it into the already-proven
`TrackingUnavailable` path: a `FAILED` export an operator can read, instead of
a process that cannot start. Resolution therefore cannot misconfigure: the
probe answers per machine, at construction, with no environment of its own.
"""

from collections.abc import Callable

from onevoicecut.ports.subject_tracker import SubjectTrackerPort

TrackerFactory = Callable[[], SubjectTrackerPort]


def vision_tracker(*, max_clip_seconds: float) -> TrackerFactory:
    """A factory that imports the adapter when called, never at import time.

    The indirection is the engine resolver's, for the same reason: the render
    worker imports this module, and the default run — the one that exists
    precisely to need no heavy extra — must be able to import the worker.

    No device is wired here. The adapter runs on `cpu`, the torch build this
    project pins, and a device knob nobody has measured a GPU path against is
    a silent-degradation axis, not a setting — the 7c `_prove` lesson, applied
    by refusing to offer the choice at all.
    """

    def build() -> SubjectTrackerPort:
        from onevoicecut.adapters.vision.torchvision_tracker_adapter import (
            TorchvisionSubjectTracker,
        )

        return TorchvisionSubjectTracker(max_clip_seconds=max_clip_seconds)

    return build


def resolve_tracker(*, max_clip_seconds: float) -> SubjectTrackerPort:
    """A fresh adapter per call.

    One render process, one clip, one model: the adapter caches its built model
    for its own lifetime, so sharing one across renders would share a lifetime
    nothing else defines.

    The ceiling is a parameter with no default, on the argument
    `render_clip.max_clip_seconds` already makes: two things want to say it —
    the worker's own guard and the tracker's pre-decode refusal — and a default
    spelled twice is a drift nobody reads.
    """
    return vision_tracker(max_clip_seconds=max_clip_seconds)()
