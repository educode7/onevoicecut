# Archive Report: multi-operator-access

**Archived**: 2026-09-17
**Change**: multi-operator-access
**Artifact Store**: hybrid
**Archive Location**: `openspec/changes/archive/2026-09-17-multi-operator-access/`

---

## Summary

Backend-only change making the shared server honest: authenticated operators, owner-attributed jobs,
owner-only mutation, shared read visibility, a persisted capacity gate, and worker liveness hardening.
Six ordered slices, 17 work units, 80/80 tasks complete, 68/68 spec scenarios closed.

## Implementation State

**Complete and committed.** All six slices landed; the change's behaviour is live in the codebase.
`main` is at `aa450f7`; today's HEAD is `e21e1c0` on branch `feat/13b-iv-b-render-drain`.
Work landed AFTER this change completed — slices 7c, 10c, 13a, 13b, and 13b-iv-b.

## Verification (Measured Final State)

Neither `apply-progress` nor `verify-report` was ever persisted for this change. The following
are whole-repository measurements taken on HEAD `e21e1c0` (this change's tests are included):

| Command | Result |
|---------|--------|
| `pytest -m "not paid and not localmodel"` | **1939 passed, 0 skipped, 30 deselected in 50.43s** |
| `mypy src tests` | **Success: no issues found in 233 source files** |
| `tests/integration` | **53 passed** |

Real ffmpeg 9.0.1 was reachable during these runs. An earlier stale-PATH run reporting 36 skips
is NOT the evidence of record.

**No verification report artifact exists for this change.** Archive records honestly that
verification was never captured as a report artifact, while carrying the measured numbers above
as the actual final state.

## Authorization Invariants

The change's three authorization invariants are enforced by generated tests, not by review:

1. **Deny by default.** The 401 check is generated from `app.routes`, so any route added later
   joins automatically.
2. **Reading shared but mutating owner-only.** The 403 check is likewise generated, and
   `owner=None` on a legacy record matches nobody.
3. **Precedence 401 → 404 → 403.**

Today's slice 13b-iv-b added no HTTP route, and the generated checks still pass in the 1939.

## Task Progress

| Metric | Value |
|--------|-------|
| Completed | 80 |
| Total | 80 |
| Pending | 0 |
| All Complete | true |

## Scenario Coverage

68/68 scenarios closed across 9 capability groups:

| Group | Scenarios |
|-------|-----------|
| AUTH | 9 |
| OWN | 11 |
| VIS | 8 |
| CXL | 8 |
| CAP | 14 |
| LEG | 9 |
| HARD | 9 |

## Spec Sync

| Domain | Action |
|--------|--------|
| job-cancellation | Created `openspec/specs/job-cancellation/spec.md` |
| job-ownership | Created `openspec/specs/job-ownership/spec.md` |
| job-visibility | Created `openspec/specs/job-visibility/spec.md` |
| legacy-job-compatibility | Created `openspec/specs/legacy-job-compatibility/spec.md` |
| operator-authentication | Created `openspec/specs/operator-authentication/spec.md` |
| worker-capacity-gate | Created `openspec/specs/worker-capacity-gate/spec.md` |
| worker-liveness-hardening | Created `openspec/specs/worker-liveness-hardening/spec.md` |

All 7 delta specs synced cleanly — no destructive merges required (no pre-existing main specs
for these domains). All diff -r readbacks were empty (byte-identical).

## Known-Open Gaps (Deliberate)

Two gaps were left open by this change and survive the archive as known-open, not defects:

1. **Worker stderr never reaches the operator.** Reaping records *that* a worker exited and
   with which status and points at the server log, but the engine's actual complaint goes to
   the web process's stderr. Capturing the child's stderr means pipe management and a deadlock
   risk if that pipe fills during a three-hour job.

2. **Real singing is unproven.** Every ASR fixture is synthesised with ffmpeg and no synthetic
   signal reaches `no_speech_prob <= 0.6`. A human voice singing plausibly does, which would
   classify sung lyrics as `SPEECH` and put them in the message — the project's stated normal
   case. `scripts/try_local_asr.py` exists to test it against real material.

## Residual Risks (Accepted)

- RR1: Duplicate spawn across a web crash between Popen and claim — bounded window, contained
  by the terminal-state guard.
- RR2: A hung worker past the 7200 s bound may let the drain spawn one worker beyond true load
  until restart reconcile — the price of closing pid-reuse.
- RR3: An invalid body can draw a 422 before authentication — no state change; moving auth
  ahead would require the rejected middleware/Depends.

## Files in Archive

- `proposal.md` — SDD proposal (363 lines)
- `design.md` — SDD design (900+ lines, D1-D10 decisions, 68 scenario traceability)
- `tasks.md` — Implementation tasks (674 lines, 80/80 complete)
- `explore.md` — Exploration notes (pre-proposal)
- `specs/` — 7 delta specs (now also synced to `openspec/specs/`)

## SDD Cycle Complete

The change is archived. Implementation: **complete**. Verification: **not run as a report
artifact; measured repo-wide on HEAD e21e1c0 — 1939 passed, mypy clean**. Unfinished tasks
and unresolved findings: **none observed**. Two deliberate gaps remain open (documented above).

## Artifact Persistence

- Openspec file: `openspec/changes/archive/2026-09-17-multi-operator-access/archive-report.md`
- Engram observation: topic key `sdd/multi-operator-access/archive-report`, project `onevoicecut`
