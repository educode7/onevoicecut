"""Turning four detections a second into twenty-five commands a second.

`sendcmd` holds a commanded value until the next command arrives, so a crop
driven at the detection rate moves in visible 250 ms steps. Smooth motion needs
commands at roughly frame rate — and the trap design.md names is that resampling
the *trajectory* to get them would mark about 84% of its keyframes
`INTERPOLATED`, collapsing the tracked ratio and flagging every trajectory
low-confidence over a purely cosmetic resample.

So the densification happens here, in the command-file writer, and
`CropTrajectory` stays one-to-one with detection samples. Provenance keeps
meaning what it says.

**That is only legitimate because this module decides nothing.** Linear
interpolation between two already-committed rects introduces no judgement, and
by convexity every interpolated rect lies inside the frame if its two endpoints
do. No smoothing window, no dead zone, no clamp is consulted — and a test parses
this module's imports to prove it, because "we did not re-decide" is a claim
about absence and cannot be shown by calling something.

The origin blindness matters for the same reason: a densified command carries no
`KeyframeOrigin` at all. It is a pixel position at a time, and the confidence
signal it was derived from stays where it was computed.
"""

import ast
import inspect
from pathlib import Path

import pytest

from onevoicecut.adapters.ffmpeg import sendcmd as sendcmd_module
from onevoicecut.adapters.ffmpeg.sendcmd import (
    DEFAULT_COMMAND_HZ,
    build_sendcmd_script,
)
from onevoicecut.domain.framing import (
    CropKeyframe,
    CropRect,
    CropTrajectory,
    KeyframeOrigin,
    TrackingConfidence,
)

FRAME_W, FRAME_H = 1920, 1080
CROP_W, CROP_H = 606, 1080


def _rect(x: int) -> CropRect:
    return CropRect(x=x, y=0, width=CROP_W, height=CROP_H)


def _keyframe(at_s: float, x: int, origin: KeyframeOrigin) -> CropKeyframe:
    return CropKeyframe(at_s=at_s, rect=_rect(x), origin=origin)


def _trajectory(*keyframes: CropKeyframe) -> CropTrajectory:
    return CropTrajectory(
        keyframes=keyframes, tracking=TrackingConfidence.WELL_TRACKED
    )


def _sampled_at_4hz(*xs: int) -> CropTrajectory:
    """Detection rate: one keyframe every 250 ms, all genuinely tracked."""
    return _trajectory(
        *(
            _keyframe(i * 0.25, x, KeyframeOrigin.TRACKED)
            for i, x in enumerate(xs)
        )
    )


def _commands(script: str) -> list[tuple[float, str, str]]:
    """Parse `<t> crop x '<v>';` lines back into (time, property, value)."""
    parsed: list[tuple[float, str, str]] = []
    for line in script.splitlines():
        line = line.strip().rstrip(";")
        if not line:
            continue
        at, _filter, prop, value = line.split(" ", 3)
        parsed.append((float(at), prop, value.strip("'")))
    return parsed


class TestItDensifies:
    def test_four_hertz_becomes_twenty_five_hertz(self) -> None:
        """One second of detections at 4 Hz produces commands at 25 Hz. Without
        this the crop advances in visible 250 ms steps."""
        script = build_sendcmd_script(
            _sampled_at_4hz(0, 100, 200, 300, 400), command_hz=25.0
        )

        times = sorted({at for at, _, _ in _commands(script)})
        assert len(times) == 26  # 0.00 through 1.00 inclusive

    def test_the_documented_default_is_twenty_five(self) -> None:
        """Pinned because it is a guess about frame rate that design.md records
        as one, and a silent change alters how every clip moves."""
        assert DEFAULT_COMMAND_HZ == 25.0

    def test_intermediate_positions_are_linear_between_their_endpoints(self) -> None:
        """The whole justification for doing this in the adapter. Anything other
        than linear would be a *decision*, and deciding here is what the spec
        forbids.

        Driven at 8 Hz so a tick lands exactly on the midpoint between two 4 Hz
        keyframes. At 25 Hz the grid steps 0.04 s and never hits 0.125, which
        would make the assertion about the grid rather than about linearity.
        """
        script = build_sendcmd_script(_sampled_at_4hz(0, 100), command_hz=8.0)

        halfway = [v for at, p, v in _commands(script) if p == "x" and at == 0.125]
        assert halfway == ["50"]

    def test_it_is_linear_across_the_whole_span_not_only_at_the_midpoint(
        self,
    ) -> None:
        """A midpoint alone passes for anything symmetric — an ease-in-out curve
        included. Every quarter has to sit where a straight line puts it.

        One keyframe pair a second apart rather than a detection-rate pair,
        because the quarters have to land on times the command file can express.
        Commands are written at millisecond precision, so a rate whose step is
        not representable there — 16 Hz steps by 0.0625 s and writes `0.062` —
        cannot be looked up by the tick it was asked for. That is the format
        doing its job, not a defect: a frame at 25 fps is 40 ms wide.
        """
        script = build_sendcmd_script(
            _trajectory(
                _keyframe(0.0, 0, KeyframeOrigin.TRACKED),
                _keyframe(1.0, 100, KeyframeOrigin.TRACKED),
            ),
            command_hz=20.0,
        )

        positions = {at: int(v) for at, p, v in _commands(script) if p == "x"}
        assert [positions[t] for t in (0.25, 0.5, 0.75)] == [25, 50, 75]

    def test_a_command_carries_only_x_and_y(self) -> None:
        """Stage 1 fixed the crop size for the whole clip, and
        `CropTrajectory.__post_init__` enforces it. Commanding `w` or `h` would
        make "native or upscaled" describe only part of the file."""
        script = build_sendcmd_script(_sampled_at_4hz(0, 100), command_hz=25.0)

        assert {p for _, p, _ in _commands(script)} == {"x", "y"}


class TestItDecidesNothing:
    def test_no_new_origin_is_introduced(self) -> None:
        """The densifier is origin-blind: a command is a pixel position at a
        time, with no provenance of its own. Marking these `INTERPOLATED` is
        exactly the option design.md rejected for destroying the tracked ratio.
        """
        script = build_sendcmd_script(_sampled_at_4hz(0, 100), command_hz=25.0)

        for origin in KeyframeOrigin:
            assert origin.value not in script

    def test_it_imports_no_trajectory_policy(self) -> None:
        """Structural, because this is a claim about *absence*. Smoothing, the
        dead zone and clamping were decided in `plan_trajectory`; importing its
        policy here would be the re-decision the spec forbids, and calling the
        module could never prove it did not.
        """
        tree = ast.parse(Path(inspect.getsourcefile(sendcmd_module) or "").read_text(encoding="utf-8"))
        imported = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }

        assert not any("plan_trajectory" in name for name in imported)
        assert "TrajectoryPolicy" not in Path(
            inspect.getsourcefile(sendcmd_module) or ""
        ).read_text(encoding="utf-8")

    def test_every_densified_rect_stays_inside_the_frame(self) -> None:
        """By convexity rather than by clamping: a point between two values that
        are both in `[0, frame - crop]` is in that interval too. Asserted over a
        trajectory that runs the full legal width, because a clamp added here
        later would pass a gentler fixture.
        """
        limit = FRAME_W - CROP_W
        script = build_sendcmd_script(
            _sampled_at_4hz(0, limit, 0, limit), command_hz=25.0
        )

        for _, prop, value in _commands(script):
            if prop == "x":
                assert 0 <= int(value) <= limit


class TestDegenerateInputs:
    def test_a_single_keyframe_still_commands_once(self) -> None:
        """A clip shorter than one detection interval. Emitting nothing would
        leave `crop` at its argv default of `x=0`, framing every such clip on
        the left edge of the source."""
        script = build_sendcmd_script(
            _trajectory(_keyframe(0.0, 700, KeyframeOrigin.TRACKED)), command_hz=25.0
        )

        assert _commands(script) == [(0.0, "x", "700"), (0.0, "y", "0")]

    def test_an_empty_trajectory_produces_an_empty_script(self) -> None:
        """Not a malformed one. `sendcmd` reads this file; a stray line would
        fail the render rather than the composition."""
        assert build_sendcmd_script(_trajectory(), command_hz=25.0) == ""

    def test_keyframes_out_of_order_are_sorted_before_interpolating(self) -> None:
        """The order of the tuple must not change the commands produced.

        Asserting the emitted *times* come out ascending does not catch this: an
        unsorted pair makes `first > last`, the span collapses, and the module
        emits a single command whose one timestamp is trivially "sorted". The
        harm is in the values — the convexity that keeps a rect inside the frame
        holds only over an ordered sequence, so an unsorted pair interpolates
        outside both endpoints with no clamp having been removed. Comparing
        against the sorted trajectory is what discriminates.
        """
        forward = _trajectory(
            _keyframe(0.0, 0, KeyframeOrigin.TRACKED),
            _keyframe(0.25, 100, KeyframeOrigin.TRACKED),
        )
        reversed_pair = _trajectory(
            _keyframe(0.25, 100, KeyframeOrigin.TRACKED),
            _keyframe(0.0, 0, KeyframeOrigin.TRACKED),
        )

        assert build_sendcmd_script(
            reversed_pair, command_hz=25.0
        ) == build_sendcmd_script(forward, command_hz=25.0)

    def test_a_non_positive_rate_is_refused(self) -> None:
        """Zero would divide, and a negative rate would step backwards forever."""
        with pytest.raises(ValueError):
            build_sendcmd_script(_sampled_at_4hz(0, 100), command_hz=0.0)


def test_the_script_is_the_sendcmd_line_format() -> None:
    """`<time> <filter> <property> '<value>';` — what ffmpeg's sendcmd parses.
    Pinned because the file is read by ffmpeg, not by us, so a format drift
    fails at render time on a real job rather than here."""
    script = build_sendcmd_script(
        _trajectory(_keyframe(0.0, 12, KeyframeOrigin.TRACKED)), command_hz=25.0
    )

    assert script == "0.000 crop x '12';\n0.000 crop y '0';\n"
