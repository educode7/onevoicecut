"""The liveness probe must never signal the process it is looking at.

On win32 `signal.CTRL_C_EVENT` is 0, and CPython routes `os.kill(pid, 0)` to
`GenerateConsoleCtrlEvent` — so the POSIX "signal 0 proves existence" idiom is
a Ctrl+C broadcast to every process sharing the caller's console whenever the
pid resolves to a group attached to it. The drain, reconcile and the watchdog
probe worker pids on every sweep, which made the web process interrupt itself,
its test suite and the parent shell. Delivery depends on console/process-group
topology, so the failure presented as nondeterministic.

Every test in this file monkeypatches `os.kill` before the probe can reach a
real call, or exercises a path that no longer signals at all. The behavioural
own-pid test and the handle-closing test run against the real kernel path;
they were written only after the fix existed, because against the pre-fix code
a real probe on a live pid *is* the Ctrl+C broadcast this file forbids.
"""

import os
import signal
import sys

import pytest

from onevoicecut.runtime.supervisor import process_is_alive

win32_only = pytest.mark.skipif(
    sys.platform != "win32", reason="the kernel32 probe exists only on Windows"
)

# Outside the range Windows assigns, so OpenProcess cannot succeed on it.
IMPOSSIBLE_PID = 0xFFFFFFFF


@win32_only
def test_console_control_signals_occupy_zero_and_one() -> None:
    """The platform fact the whole fix rests on, pinned rather than assumed.

    Because `CTRL_C_EVENT` is 0, there is no signal value on Windows that means
    "probe only" — the POSIX idiom has no translation, which is why the win32
    branch must leave `os.kill` alone entirely.
    """
    assert int(signal.CTRL_C_EVENT) == 0
    assert int(signal.CTRL_BREAK_EVENT) == 1


@win32_only
def test_the_win32_probe_never_touches_os_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """No call at all, not merely a safe-looking one: every `os.kill` sig on
    win32 is either a console-control event (0, 1) or a `TerminateProcess` in
    disguise, and a liveness probe has no business with either. The spy stands
    in so the pre-fix implementation records its console broadcast instead of
    delivering one."""
    calls: list[tuple[int, int]] = []

    def spy(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    monkeypatch.setattr(os, "kill", spy)

    assert process_is_alive(os.getpid()) is True
    assert calls == [], f"the probe must not signal on win32; it called os.kill{calls}"


def test_a_dead_pid_is_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Safe against both implementations: the stand-in raises the `OSError` the
    pre-fix probe reads as death, and the post-fix win32 probe never reaches it
    because `OpenProcess` fails on an impossible pid."""

    def dead(pid: int, sig: int) -> None:
        raise OSError("no such process")

    monkeypatch.setattr(os, "kill", dead)

    assert process_is_alive(IMPOSSIBLE_PID) is False


def test_the_posix_branch_probes_with_signal_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX keeps the idiom, because there sig 0 genuinely is a no-op probe.
    Covered through the dispatcher with the platform faked, so the guarantee
    does not depend on which host runs the suite."""
    monkeypatch.setattr(sys, "platform", "linux")
    calls: list[tuple[int, int]] = []

    def spy(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    monkeypatch.setattr(os, "kill", spy)

    assert process_is_alive(4321) is True
    assert calls == [(4321, 0)]


def test_the_posix_branch_reads_oserror_as_dead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half of the POSIX contract, unchanged by the win32 fix."""
    monkeypatch.setattr(sys, "platform", "linux")

    def dead(pid: int, sig: int) -> None:
        raise OSError("no such process")

    monkeypatch.setattr(os, "kill", dead)

    assert process_is_alive(4321) is False


def test_the_processs_own_pid_is_alive() -> None:
    """The real kernel path, no stand-ins: this test only exists after the fix,
    because against the pre-fix code probing a live pid on win32 *is* the
    console broadcast this file forbids. On POSIX it exercises the unchanged
    sig-0 probe, which is safe there."""
    assert process_is_alive(os.getpid()) is True


@win32_only
def test_a_live_probe_closes_the_handle_it_opened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The drain probes every job every five seconds for the life of the web
    process. A handle leaked per probe would exhaust the process during one
    long sermon, so the close is pinned as an observable fact, not left to
    reading the `finally`."""
    from onevoicecut.runtime import supervisor

    closed: list[int] = []
    real_close = supervisor._kernel32.CloseHandle

    def close_spy(handle: int) -> int:
        closed.append(handle)
        result: int = real_close(handle)
        return result

    monkeypatch.setattr(supervisor._kernel32, "CloseHandle", close_spy)

    assert process_is_alive(os.getpid()) is True
    assert len(closed) == 1
