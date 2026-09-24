# Archive Report: video-transcription-pipeline

**Archived**: 2026-09-24
**Change**: video-transcription-pipeline (proposal rev 4)
**Artifact Store**: hybrid
**Archive Location**: `openspec/changes/archive/2026-09-24-video-transcription-pipeline/`

---

## Summary

The repository's founding change: multi-hour Spanish source video -> chunked ASR transcript
-> map-reduce summary plus timestamped clip candidates with scripts -> rendered vertical
clips, on a shared authenticated server. Sixty-one slice headings in `tasks.md`; the last
unit to land was slice 13c-ii (real subject-tracker contract test, tasks 13c.8-13c.12).
Archived now so the follow-on change `refactor-fca-layout` targets canonical specs instead
of this change's deltas.

## Implementation State

**Complete.** All 396 checkboxes in `tasks.md` are checked. Independently counted at archive
time: 396 checked, 0 unchecked. Zero unfinished tasks. What the change still owes is recorded
under Known-Open Gaps below — those are project-level, known-and-deliberate items, not tasks
of this change.

## Verification (Measured Final State)

**No `verify-report.md` and no `apply-progress` artifact was ever persisted for this change**
(both confirmed absent at archive time). Archive records that honestly: verification was never
captured as a report artifact.

Evidence of record (measured on this tree, carried from the final-state facts supplied at
archive launch; the suite was deliberately NOT re-run at archive time — behavior is frozen):

| Command | Result |
|---------|--------|
| `pytest -m "not paid and not localmodel"` | 2076 passed |
| `pytest -m localmodel` | 34 |
| `pytest -m paid` | 10 |
| Total / skips | 2120 tests, zero skips |
| `mypy src tests` | clean, strict, 256 source files |

## Task Progress

| Metric | Value |
|--------|-------|
| Completed | 396 |
| Total | 396 |
| Pending | 0 |
| All Complete | true |

## Spec Sync (Promotion Inventory)

10 delta specs under `specs/`. `rules.archive` from `openspec/config.yaml` ("Warn before
merging destructive deltas") did not trigger: no delta contains a REMOVED section, nine deltas
are full specs for domains with no prior canonical spec, and the tenth is ADDED-only.

| Domain | Action | Requirements |
|--------|--------|--------------|
| audio-extraction | Created `openspec/specs/audio-extraction/spec.md` | 4 |
| clip-rendering | Created `openspec/specs/clip-rendering/spec.md` | 15 |
| media-ingest | Created `openspec/specs/media-ingest/spec.md` | 5 |
| project-bootstrap | Created `openspec/specs/project-bootstrap/spec.md` | 3 |
| script-generation | Created `openspec/specs/script-generation/spec.md` | 7 |
| speech-transcription | Created `openspec/specs/speech-transcription/spec.md` | 9 |
| subject-tracking | Created `openspec/specs/subject-tracking/spec.md` | 13 |
| transcript-artifacts | Created `openspec/specs/transcript-artifacts/spec.md` | 8 |
| transcription-jobs | Created `openspec/specs/transcription-jobs/spec.md` | 6 |
| slice6-speaker-mode | Already in sync — canonical left byte-untouched | 5 |

Totals: 70 requirements promoted as new; 5 already canonical; 75 delta requirements in all.
Canonical inventory after this archive: **17 capability specs, 109 requirements** (was 8 / 39).
No requirement was dropped or rewritten by this archive.

### Native composition for slice6-speaker-mode (main spec existed)

Command invoked:

    gentle-ai sdd-archive-compose --canonical "openspec/specs/slice6-speaker-mode/spec.md" --delta "openspec/changes/video-transcription-pipeline/specs/slice6-speaker-mode/spec.md" --output "openspec/specs/slice6-speaker-mode/spec.md.compose-tmp"

Exit 1; stderr verbatim:

    Error: sdd-archive-compose: unapplied ADDED delta for requirement "Admission Validates Speaker Mode and Engine Before Storage": a requirement named "Admission Validates Speaker Mode and Engine Before Storage" already exists in the canonical spec

Nothing was written (`.compose-tmp` confirmed absent after the refusal). Resolution is from
precedent and byte evidence, not a manual merge: this delta was already promoted when slice 6
was archived (2026-08-31, `slice-6-archive-report.md`: canonical "created (no prior main spec
existed)" and delta "synced to `openspec/specs/slice6-speaker-mode/spec.md`"), and `diff -r`
between the delta and the canonical before the folder move exited 0 — byte-identical, so all
five ADDED requirements are present in the canonical. The refusal is compose declining a
redundant re-application of an already-applied delta; no composition was pending, so the
canonical was left byte-untouched. It still carries the `## ADDED Requirements` heading from
the original 2026-08-31 promotion — preserved as historical fact, not modified here.

### diff -r readbacks (all verbatim outputs empty — the only passing evidence)

- 9 full-spec copies, pre-mv: `diff -r <delta> <tmp>` empty for all nine (`ALL_COPIES_OK`).
- 9 full-spec copies, post-mv: `diff -r <delta> <canonical>` empty for all nine (`ALL_POST_MV_DIFFS_EMPTY`).
- Folder move: pre-move recursive snapshot vs destination — empty (`MOVE_OK`); source confirmed gone before comparison.
- slice6 delta vs canonical (pre-move): empty, exit 0.

## Archive Contents (26 files observed)

- `proposal.md` — present (rev 4)
- `design.md` — present
- `tasks.md` — present, 396/396 checked; historical bytes preserved exactly
- `exploration.md` — present
- `specs/` — present, 10 delta spec files
- `slice-6-proposal.md`, `slice-6-design.md`, `slice-6-tasks.md`, `slice-6-archive-report.md` — present (slice-6 partial-archive history)
- `slice-7-tasks.md` through `slice-13-tasks.md` (7 files) — present
- `.gentle-ai-instance` — present (gitignored; moved alongside the tracked files so the archived tree matches the pre-move snapshot byte-for-byte)
- `verify-report.md` — **missing** (never created; recorded honestly; does not block archive)
- `apply-progress` — **missing** (never persisted; recorded honestly; does not block archive)

## Artifact Retrieval Note (Traceability)

Hybrid store; the dispatcher supplied repo-path locators, so all required artifacts
(proposal, specs, design, tasks, verify-report) were read from disk paths. Engram observation
IDs actually read for this phase: **none** — no topic-key locators were consulted.

## Known-Open Gaps (Informational Carry-Forward)

Project-level, known-and-deliberate — NOT unfinished tasks of this change (tasks are 396/396):

1. **No browser UI.** The HTTP surface is complete and authenticated; nothing renders it.
2. **The worker's own message never reaches the operator.** Reaping records *that* a worker
   exited and with which status and points at the server log; the engine's actual complaint
   goes to the web process's stderr. Capturing the child's stderr means pipe management and a
   deadlock risk if that pipe fills during a three-hour job.
3. **Real singing is unproven.** Every ASR fixture is synthesised with ffmpeg, and no
   synthetic signal reaches `no_speech_prob <= 0.6`. A human voice singing plausibly does,
   which would classify sung lyrics as `SPEECH` — the project's stated normal case.
   `scripts/try_local_asr.py` exists to test against real material; media must never be
   committed.
4. **Five render-drain review WARNINGs, explicitly informational** (none reopen the unit):
   (a) one malformed export record stops every pending render until an operator removes the
   file; (b) the refused-range batch's terminal `FAILED` state is recorded in code but not
   pinned by a test; (c) no test asserts `render_drain_supervisor` forwards `reap()` into the
   sweep's `exited` parameter; (d) `RenderWorkerProcesses.finished`'s docstring reads stronger
   than the truth; (e) the job drain lacks the render drain's launch-window guarantees — the
   asymmetry is deliberate and must not be "fixed" by copying one sweep onto the other without
   re-argument.

## Scope of This Archive

Touched: `openspec/specs/` (9 specs created; 1 verified already in sync) and the change
folder's location. Not touched: `src/`, `tests/`, `openspec/changes/refactor-fca-layout/`.
No test run, no commits — the move is staged as git renames and the nine new canonical specs
are untracked until an operator commits them.

## SDD Cycle Complete

The change is archived. Implementation: **complete — 396/396 tasks**. Verification: **no
verify-report artifact was ever persisted**; measured evidence of record on this tree — 2120
tests (2076 default / 34 localmodel / 10 paid, zero skips), mypy strict clean over 256 source
files. Unfinished tasks: **none observed**. Unresolved findings: **none**; the four items
under Known-Open Gaps are deliberate project-level gaps carried forward as informational.

## Artifact Persistence

- Openspec file: `openspec/changes/archive/2026-09-24-video-transcription-pipeline/archive-report.md`
- Engram observation: topic key `sdd/video-transcription-pipeline/archive-report`, project `onevoicecut`
