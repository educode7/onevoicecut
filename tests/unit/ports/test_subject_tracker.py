"""The detector's answer, and the one distinction it exists to make.

`SubjectTrackerPort` says where the preacher is. It says nothing about cropping,
smoothing, aspect ratio or rendering — that separation is what lets the whole
trajectory arithmetic be proven against a fake with no vision weights loaded.

**A miss has no box; a weak hit has one.** That is the spec's "the no-detection
result MUST be distinguishable from a low-confidence true detection", made
structural rather than conventional. A detector that returned a centred guess with
`confidence=0.1` instead of `box=None` would be indistinguishable from one that
genuinely found a barely-visible subject, and the trajectory built from it would
mark a fabricated position `TRACKED` — the exact silent degradation the provenance
axis exists to prevent. There is no threshold here at which a weak hit becomes a
miss, because *whether a weak hit is good enough* is policy, and policy lives in
the use case.

`BoundingBox` is deliberately **not** `CropRect`, despite carrying the same four
integers. `CropRect` is a window the renderer cuts; `BoundingBox` is where a
person was found. Sharing the type would put cropping vocabulary inside the
detector's answer, which is precisely what the port is forbidden to know.
"""

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

from onevoicecut.ports import subject_tracker
from onevoicecut.ports.subject_tracker import BoundingBox, SubjectDetection


class TestABoundingBox:
    def test_it_is_frozen_like_every_other_entity(self) -> None:
        box = BoundingBox(x=10, y=20, width=100, height=200)

        with pytest.raises(dataclasses.FrozenInstanceError):
            box.x = 0  # type: ignore[misc]

    def test_it_carries_pixels_in_the_source_frame(self) -> None:
        box = BoundingBox(x=10, y=20, width=100, height=200)

        assert (box.x, box.y, box.width, box.height) == (10, 20, 100, 200)

    def test_the_port_never_imports_the_cropping_vocabulary(self) -> None:
        """Same four integers as `CropRect`, different meaning: that is a window
        the renderer cuts, this is where a person was found.

        Asserted as an *absent import* rather than as `BoundingBox is not
        CropRect`, which mypy already rejects as a non-overlapping comparison —
        a stronger guarantee than the runtime check could give. What mypy cannot
        catch is the port growing a cropping concept later, and this does: the
        spec forbids this module knowing about cropping at all.
        """
        source = ast.parse(
            Path(inspect.getsourcefile(subject_tracker) or "").read_text(
                encoding="utf-8"
            )
        )
        imported = {
            alias.name
            for node in ast.walk(source)
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }

        assert "CropRect" not in imported
        assert "CropTrajectory" not in imported


class TestAMissVersusAWeakHit:
    def test_a_miss_carries_no_box(self) -> None:
        """The structural distinction. Nothing to inspect, so nothing downstream
        can mistake it for a position."""
        miss = SubjectDetection(at_s=1.0, box=None, confidence=None)

        assert miss.box is None

    def test_a_low_confidence_hit_still_carries_its_box(self) -> None:
        """A barely-visible preacher is still a located preacher. Collapsing this
        into a miss would discard a real observation on the detector's own
        authority, and where the threshold sits is the use case's decision."""
        weak = SubjectDetection(
            at_s=1.0, box=BoundingBox(x=0, y=0, width=10, height=10), confidence=0.05
        )

        assert weak.box is not None
        assert weak.confidence == 0.05

    def test_the_two_are_distinguishable_without_a_threshold(self) -> None:
        """No number decides which is which — the presence of a box does. A
        detector reporting a centred guess at `confidence=0.05` instead of a
        miss would be indistinguishable from one that genuinely found a
        barely-visible subject, and the trajectory would mark the fabrication
        `TRACKED`.
        """
        miss = SubjectDetection(at_s=1.0, box=None, confidence=None)
        weak = SubjectDetection(
            at_s=1.0, box=BoundingBox(x=0, y=0, width=10, height=10), confidence=0.05
        )

        assert (miss.box is None) is not (weak.box is None)

    def test_a_miss_may_still_report_a_confidence(self) -> None:
        """Some detectors emit a score for the frame even when they locate
        nothing. The box is what decides; the score is allowed to exist."""
        miss = SubjectDetection(at_s=1.0, box=None, confidence=0.4)

        assert miss.box is None


class TestTheDetectionItself:
    def test_it_is_frozen(self) -> None:
        detection = SubjectDetection(at_s=1.0, box=None, confidence=None)

        with pytest.raises(dataclasses.FrozenInstanceError):
            detection.at_s = 2.0  # type: ignore[misc]

    def test_every_field_is_required(self) -> None:
        """No defaults. A detection that could omit its box would let a caller
        construct one without deciding hit or miss — the single decision this
        type exists to record."""
        for field in dataclasses.fields(SubjectDetection):
            assert field.default is dataclasses.MISSING
            assert field.default_factory is dataclasses.MISSING
