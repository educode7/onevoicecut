# Feature: Make the worker liveness probe non-signalling on Windows

Repository-relative locator: `odd/tasks/fix-windows-liveness-ctrl-c.md`

## Objective

Replace the Windows path of `process_is_alive` in `src/onevoicecut/runtime/supervisor.py`
with a kernel query (`OpenProcess` + `GetExitCodeProcess` via stdlib `ctypes`) that
never signals anything, keep `os.kill(pid, 0)` on POSIX where it is a genuine
no-op probe, and correct the docstring that asserts sig 0 is safe on Windows.

## Problem

`process_is_alive(pid)` probes liveness with `os.kill(pid, 0)`. On win32,
`signal.CTRL_C_EVENT == 0` and `signal.CTRL_BREAK_EVENT == 1` (verified in this
venv), and CPython routes sig 0 and sig 1 to `GenerateConsoleCtrlEvent`. So
`os.kill(pid, 0)` is **not a neutral probe**: when `pid` resolves to a process
group attached to the caller's console, it broadcasts Ctrl+C to every process
sharing that console.

## Why it matters

This is a production defect, not a test artifact. The web process's job drain,
startup reconcile and watchdog all call `process_is_alive` on every sweep, so a
live worker pid that matches the console-group topology makes the server signal
itself and everything attached to its console. In the observed run it
interrupted pytest mid-suite, reached the parent shell/wrapper, and the
interrupted process then took ~60s more to exit. Because delivery depends on
console/process-group topology, the failure presents as nondeterministic — the
full suite sometimes passes.

## Proven root cause (already diagnosed; evidence below)

Call site: `src/onevoicecut/runtime/supervisor.py` line 67 (`process_is_alive`),
the `os.kill(pid, 0)` at line 87.

Captured evidence from an instrumented run (pid 16116 was the pytest process's
own pid):

```
=== os.kill #1 pid=16116 sig=0 wall=1.318s
=== os.kill #2 pid=16116 sig=0 wall=1.340s
!!! SIGINT/Ctrl+C DELIVERED to this process at wall=2.083s
```

Call chain, with real stacks:

```
tests/unit/runtime/test_restart_semantics.py:81 and :102   (job record built with pid=os.getpid(), line 56)
  -> src/onevoicecut/runtime/app.py:303 drain_once -> :307 genexpr
  -> src/onevoicecut/runtime/supervisor.py:117 worker_is_alive
  -> src/onevoicecut/runtime/supervisor.py:87 process_is_alive -> os.kill(pid, 0)
  -> GenerateConsoleCtrlEvent(CTRL_C_EVENT, <own pid>) -> Ctrl+C to the console group
```

The existing docstring on `process_is_alive` claims sig 0 "does not terminate
anything" and was "verified on CPython 3.12 / Windows". That verification only
observed return-vs-`OSError`; it never tested the console-control broadcast
semantics. The docstring is wrong and is corrected as part of this fix.

## Scope (authorized)

- `src/onevoicecut/runtime/supervisor.py`: the win32 branch of
  `process_is_alive` + the docstring. Nothing else in the module changes.
  `kill_worker` is deliberately left alone (`os.kill(pid, SIGTERM)` mapping to
  `TerminateProcess` is its intended behaviour); any concern there is reported
  as a risk, not changed.
- New test file `tests/unit/runtime/test_process_liveness_probe.py`.
- This document.
- No new third-party dependency (`ctypes` is stdlib; `psutil` is refused).

Out of scope: the in-flight OpenSpec change `refactor-fca-layout` and its
uncommitted slice 1d work (28 tree entries) — not reverted, not stashed, not
edited, not committed. Only this unit's files are staged, by explicit path.

## Hard safety rule

Never execute a real `os.kill(<any pid>, 0)` or `os.kill(<any pid>, 1)` on this
machine, in any script or test, until the fix is in place — it broadcasts
Ctrl+C to the console group and kills the test run and possibly the parent
shell. Every test that exercises the probe monkeypatches `os.kill` so the real
call cannot happen. The behavioural "own pid reports alive" test is added only
after GREEN, because it is unsafe to run against the pre-fix code.

## Checklist

- [x] WL-1 RED (safe): spy test asserting the win32 probe never passes a
      console-control signal (sig 0/1 == `CTRL_C_EVENT`/`CTRL_BREAK_EVENT`) to
      `os.kill`; `os.kill` monkeypatched so no real call can occur. Observed
      FAIL against pre-fix code: `it called os.kill[(5564, 0)]`.
- [x] WL-2 Dead/impossible pid reports not-alive (safe pre- and post-fix;
      `os.kill` monkeypatched to raise `OSError`).
- [x] WL-3 POSIX branch covered host-independently (monkeypatch `sys.platform`
      to a non-win32 value; assert sig 0 is the probe and `OSError` means dead).
- [x] WL-4 GREEN: implement the ctypes probe — `OpenProcess`
      (`PROCESS_QUERY_LIMITED_INFORMATION`) + `GetExitCodeProcess ==
      STILL_ACTIVE` (259), handle closed in `finally`, explicit
      `argtypes`/`restype` so handles are not truncated on 64-bit, failed
      `OpenProcess` means not alive; rewrite the docstring to the real
      invariant.
- [x] WL-5 Post-GREEN: behavioural test that the process's own pid reports
      alive on the real kernel path, and that the probe closes the handle it
      opened (CloseHandle spy; the leak would compound every five seconds).
- [x] WL-6 Verification: see Verification results below.
- [ ] WL-7 One work-unit commit (fix + tests + this document), Conventional
      Commits, no attribution, no push/PR/merge. Commit identity is recorded in
      the Engram mirror after the commit exists (a document cannot contain the
      hash of the commit that introduces it).

## Acceptance criteria

- No `os.kill` call with sig 0 or 1 exists on the Windows liveness path.
- POSIX behaviour is byte-for-byte equivalent: `os.kill(pid, 0)`, `OSError`
  means not alive.
- A live pid (own process) reports alive via the real kernel probe on this
  machine; a dead/impossible pid reports not alive; the opened handle is
  closed.
- The docstring states the real invariant and why sig 0 cannot be used on
  Windows.
- The previously-hanging subset (13 runtime test files) completes without a
  KeyboardInterrupt; full default suite and mypy are green.
- `kill_worker` unchanged.

## Applicable checks

- `.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"`
  (foreground, generous tool timeout; never `Start-Process`/redirect/`Select-Object` piping).
- `.venv\Scripts\python.exe -m mypy src tests`

## TDD resolution

- Mode: **strict TDD** — `openspec/config.yaml` `strict_tdd: true`.
- Runner: `.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"`.
- Type check: `.venv\Scripts\python.exe -m mypy src tests`.
- RED is observed with the safe spy test (WL-1) before any implementation is
  written; no RED evidence is claimed that was not observed.

## Verification results (all foreground, real wall-clock completions)

- RED: `pytest tests/unit/runtime/test_process_liveness_probe.py -v` against
  pre-fix code — **1 failed, 4 passed**; the spy test failed with
  `the probe must not signal on win32; it called os.kill[(5564, 0)]`
  (sig 0 == `CTRL_C_EVENT`). No real `os.kill` executed at any point.
- GREEN: same file post-fix — **7 passed** (own-pid-alive and handle-close
  tests added only after the implementation existed).
- The 13-file previously-hanging subset — **185 passed in 2.35s**, completed,
  no KeyboardInterrupt (before the fix: 172 passed, then interrupted).
- `pytest tests/unit/runtime -q` — **344 passed in 6.80s**.
- Full default suite `pytest -m "not paid and not localmodel" -q` —
  **2119 passed, 44 deselected in 84.27s**, no KeyboardInterrupt
  (reference: HEAD clean 2100/44; slice-1d tree ~2112 collected; +7 new).
- `mypy src tests` — **Success: no issues found in 275 source files**
  (two initial `comparison-overlap` errors in the new test were fixed by
  comparing `int(signal.CTRL_C_EVENT)` rather than the enum literal).
- Measured diff (this unit only): `supervisor.py` 58 insertions / 7 deletions;
  new test file 136 lines; this document. Total ~343 changed lines — inside
  the 800-line budget.

## Progress

- (init) Document created; root cause verified by reading
  `src/onevoicecut/runtime/supervisor.py:67-90` — `os.kill(pid, 0)` is the only
  live sig-0 call site in `src/` (grep-confirmed). Checklist all open.
- WL-1..WL-3 done: RED observed safely (spy caught the sig-0 call; no real
  `os.kill` ever ran).
- WL-4..WL-5 done: ctypes kernel probe implemented behind the win32 dispatch;
  POSIX branch unchanged; docstring rewritten to the real invariant; 7/7 green.
- WL-6 done: all four verification runs green, results above.
- WL-7 pending: commit next; hash goes to the Engram mirror.
