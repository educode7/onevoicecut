# Archive Report: refactor-fca-layout

**Archived**: 2026-09-30
**Change**: refactor-fca-layout — hexagonal → FastAPI Clean Architecture (FCA) layout
**Artifact Store**: hybrid
**Archive Location**: `openspec/changes/archive/2026-09-30-refactor-fca-layout/`
**Branch / HEAD at launch**: `refactor-fca-layout` @ `1e7026e`

---

## Summary

The tree-wide migration from hexagonal layout to FastAPI Clean Architecture: `shared/`
domain kernel, three pipeline modules (`jobs`, `transcripts`, `clips`) each with
`domain/` + `domain/interfaces/`, CQRS `use_cases/{commands,queries}/`, `infrastructure/`
and `presentation/{schemas,routes,controllers}/v1`, the `runtime/` parallel composition
roots, `/api` → `/api/v1`, pagination, and the AB-01…AB-12 AST architecture guard.
29 slice headings in `tasks.md`; the last unit to land was slice 6c (closure verification,
commits `8338594` + `1e7026e`). Archived now so its five delta specs become canonical and
the change folder leaves the active changes directory.

## Implementation State

**Complete.** All 118 checkboxes in `tasks.md` are checked. Independently counted at
archive time from the archived bytes: **118 checked, 0 unchecked.** Native dispatcher
reported `taskProgress: 118/118`, `allComplete: true`. Zero unfinished tasks.

## Verification (Final-State Authority)

**No `verify-report.md` exists** — verification was never requested as a phase for this
change (confirmed absent after the move). The dispatcher states a missing/stale/failed
report does not block archive; this report does not invent one. What follows is ranked
final-state evidence, not a verification certificate.

Rank 1 — persisted tasks artifact: 118/118 checked (counted above).

Rank 2 — explicit final-state facts in the archive launch prompt (2026-09-30, post-dating
any apply snapshot), corroborated by `closure-notes.md` §6c.1 (commit `8338594`):

| Command | Result |
|---------|--------|
| `pytest -m "not paid and not localmodel" -q` | **2244 passed, 44 deselected, 0 skipped** (ffmpeg bin on PATH; 0 skips ⇒ ffmpeg surface exercised) |
| `mypy src tests` | **Success: no issues found in 373 source files** |
| `pytest tests\test_architecture.py -q` | **51 passed** |
| Guard RED evidence (slice 6a.2, cited not re-planted) | **13/13 registered rule groups FAILED**, each naming its planted file → plants removed → GREEN |
| Marker contract | `-m paid` = 10, `-m localmodel` = 34, sum 44 = deselected count |

Archive-time sanity re-run performed by this phase (cheap guard check that promotion
broke nothing):

- `.venv\Scripts\python.exe -m pytest tests\test_architecture.py tests\test_fca_config.py -q`
  → **55 passed in 0.52s** (baseline 51 + 4).

The full default suite was NOT re-run at archive time: this phase changes no `src/`,
`tests/`, or `openspec/config.yaml` line, only spec text and file locations.

## Task Progress

| Metric | Value |
|--------|-------|
| Completed | 118 |
| Total | 118 |
| Pending | 0 |
| All Complete | true |

## Spec Sync (Promotion Inventory)

Five delta specs under `specs/`. `rules.archive` from `openspec/config.yaml` is
"Warn before merging destructive deltas" — it did **not** trigger: no delta contains a
`## REMOVED Requirements` section (grep over the five deltas found only ADDED/MODIFIED),
so nothing was destructively merged and no confirmation was required.

| Domain | Action | Requirements |
|--------|--------|--------------|
| api-versioning | **Created** `openspec/specs/api-versioning/spec.md` (full spec; no prior canonical) | 5 created |
| architecture-boundary | **Created** `openspec/specs/architecture-boundary/spec.md` (full spec; no prior canonical) | 6 created |
| job-cancellation | **Updated** (native composition) | 1 modified, 0 added, 0 removed; 5 → 5 |
| job-visibility | **Updated** (native composition) | 2 added, 1 modified, 0 removed; 4 → 6 |
| operator-authentication | **Updated** (native composition) | 3 added, 1 modified, 0 removed; 4 → 7 |

Totals: **11 requirements created, 5 added, 3 modified, 0 removed → +16 net.**
Canonical inventory after this archive: **19 capability specs, 125 requirements**
(was 17 / 109 per the `video-transcription-pipeline` archive report). No requirement was
dropped: pre/post `### Requirement:` counts were measured for all three composed specs
(5→5, 4→6, 4→7 — exactly `old − modified + added`), and the two untouched requirements of
each original spec are present in the composed file by name.

### Native composition (main spec existed) — command invocations

All three ran through `gentle-ai sdd-archive-compose` (no model-driven Read/Edit merge),
each followed by `Move-Item` of the `.compose-tmp` over the canonical:

    gentle-ai sdd-archive-compose --canonical "openspec\specs\job-cancellation\spec.md" --delta "openspec\changes\refactor-fca-layout\specs\job-cancellation\spec.md" --output "openspec\specs\job-cancellation\spec.md.compose-tmp"
    gentle-ai sdd-archive-compose --canonical "openspec\specs\job-visibility\spec.md" --delta "openspec\changes\refactor-fca-layout\specs\job-visibility\spec.md" --output "openspec\specs\job-visibility\spec.md.compose-tmp"
    gentle-ai sdd-archive-compose --canonical "openspec\specs\operator-authentication\spec.md" --delta "openspec\changes\refactor-fca-layout\specs\operator-authentication\spec.md" --output "openspec\specs\operator-authentication\spec.md.compose-tmp"

Exit codes: **0 / 0 / 0**, empty stderr each. No refusal, so no blocking failure. The
canonical-vs-composed unified diffs (captured before the `mv`) show only the intended
requirement replacements/additions — the modified blocks plus the new requirement blocks —
and no unrelated requirement content. `.compose-tmp` files confirmed absent after the move
(0 remaining under `openspec/specs/`).

**Composition note (not a defect, recorded):** in `job-visibility` the delta's trailing
`## Open Questions` block was carried by compose into the canonical, positioned between
`Complete Listing With Owner Attribution` and `Additive Owner Field On Job Responses`.
This is native-tool output — content preserved byte-for-byte as the delta carried it, not
edited by this phase. The questions are the stale ones named in drift **D5** (design
already closed them: `limit` default 20, `offset le=10_000`); they now live in the
canonical as written, and D5 below records their staleness rather than silently fixing
them here.

### Mechanical full-spec copies (no main spec) — `diff -r` evidence

`api-versioning` and `architecture-boundary` have no canonical spec and carry no delta
sections (plain `# … Specification / ## Requirements / ### Requirement:` shape), so each
delta IS a full spec and was copied mechanically (shell `Copy-Item` → `diff -r` →
`Move-Item`; never Read → Write):

- `diff -r openspec/changes/refactor-fca-layout/specs/api-versioning/spec.md <temp>` → **exit 0, empty output**
- `diff -r openspec/changes/refactor-fca-layout/specs/api-versioning/spec.md openspec/specs/api-versioning/spec.md` (post-move) → **exit 0, empty output**
- `diff -r …/architecture-boundary/spec.md <temp>` → **exit 0, empty output**
- `diff -r …/architecture-boundary/spec.md openspec/specs/architecture-boundary/spec.md` (post-move) → **exit 0, empty output**

### Folder-move readback — `diff -r` evidence

Pre-move recursive snapshot (`cp -R` of the change folder to a temp root) vs
`openspec/changes/archive/2026-09-30-refactor-fca-layout/` after `git mv`:

    === MANDATORY READBACK: diff -r snapshot vs destination ===
    diff -r exit=0 (empty output above = byte-identical)
    MOVE_OK

Source confirmed absent before the comparison (`source-exists=False`). The only file not
in that comparison is this `archive-report.md`, which is additive (written after the move).

`diff` binary used: GNU diffutils 3.12 at `C:\Program Files\Git\usr\bin\diff.exe`
(PowerShell's `diff` is the `Compare-Object` alias, not a diff tool).

## Drift Carried (D1–D7, from `closure-notes.md` §6c.2 — recorded, none fixed here)

None blocks archiving. D1, D4 and D6 were re-verified against this tree at archive time.

1. **D1 — `PublishPort` claim.** `openspec/specs/script-generation/spec.md:172` (canonical,
   promoted from `video-transcription-pipeline`) reads "`PublishPort` is declared and
   deliberately unimplemented". **Re-verified at archive time: 0 occurrences of
   `PublishPort` anywhere under `src/`**; `design.md:396` says it "does not exist in the
   tree today (verified)". Spec/reality wording drift, not a code defect. Carried unchanged
   — the sentence as written reads as a code declaration; reinterpreting it as "declared in
   the specs" would silently soften it.
2. **D2 — `fca_config.yaml` `tooling.linter: ruff`** pins a linter installed nowhere on
   this machine (absent from `.venv` and from `requirements*.txt`). Design-pinned;
   disclosed in 6b.4.
3. **D3 — `fca_config.yaml` `transcripts` declares `v1 active` with no router.**
   Design-intended (transcripts is worker-driven); AV-04 compares versions, not module
   participation, so the registry test stays green.
4. **D4 — architecture-boundary prose vs shipped guard.** The promoted requirement says
   "A module MUST NOT import another module's `domain` or `infrastructure`", but
   **re-verified at archive time: `jobs/domain/jobs.py:6` imports
   `transcripts.domain.chunking`**, and the shipped guard permits it (AB-07 is
   owner-anchored: `jobs-domain-isolation` refuses *foreign* domains reaching into
   `jobs.domain`). Known and deliberate; the prose/guard asymmetry is now canonical —
   either scope the prose to the guarded directions or record the jobs→transcripts edge as
   an accepted exception; an archiver/orchestrator decision, deliberately not taken here
   because editing composed spec text by hand is exactly what the native-composition rule
   forbids.
5. **D5 — job-visibility open questions are stale.** Both "Open Questions" (default page
   size; offset upper bound) are answered by design (`limit` default 20; `offset`
   `le=10_000`) and shipped tests (slice 5b), yet the delta carries them unanswered — and,
   via composition, the canonical now carries them too (see composition note above).
   Textual drift only; behavior matches design.
6. **D6 — stale code comment (minor).** **Re-verified at archive time:
   `runtime/worker.py:265`** still carries the "Temporary composition wiring: 4e's
   `clips_module_api` …" comment whose `render_worker` twin 4f removed as stale. Prose
   only; no behavior impact; not a spec file, so out of scope for spec sync.
7. **D7 — proposal criterion 7's wording.** "runtime bodies unchanged except imports"
   holds for slice 4f's own commit (`c09ee40` receipt in `closure-notes.md` §7) but is not
   true chain-wide: 19 `storage:` annotation lines, 5 design-mandated CQRS call-site
   rewires, the 1d composition-root move, and the non-slice win32 fix `3500cd3` also touched
   `runtime/`. Do not restate the literal wording without that classification.

### OQ1 — archive order

OQ1 (whether this change archives before/after `video-transcription-pipeline`) is an
**orchestrator sequencing choice, not a code dependency**: nothing in this change's tree,
specs, or tests imports from the archived change's directory, and the two changes' runtime
diffs are independent. `sdd-archive` may run in either order. Recorded as such; not
resolved into a code fact.

## Chain-Wide `runtime/` Classification (carried from `closure-notes.md` §7)

`git diff 9a737db..HEAD -- src/onevoicecut/runtime/` = **8 files, +316/−504, 70 hunks**,
every hunk attributed: **35 import-only**; **19 `storage:` parameter-annotation lines**
(introduced by `8ebe96c`, type annotations only); **16 attributed other** — 1d composition-
root move (module docstring, interval constants, 178-line `build_app` block), 1b settings
relocation, 4f new `storage.py`, 5 CQRS rewires (4c), 4 docstrings, and 1 non-slice win32
liveness fix (`3500cd3`). None is an unaccounted body edit.

## Archive Contents (9 tracked files observed, + this report = 10)

- `proposal.md` — present
- `design.md` — present
- `tasks.md` — present, **118/118 checked**; historical bytes preserved exactly
  (move is a git rename with 0 content change)
- `closure-notes.md` — present (251 lines; slice 6c evidence record + D1–D7)
- `specs/` — present, **5 delta spec files** (api-versioning, architecture-boundary,
  job-cancellation, job-visibility, operator-authentication)
- `verify-report.md` — **missing** (never created; recorded honestly; does not block archive)
- `state.yaml`, `exploration.md`, `research.md` — **missing** (never created for this change)
- Engram apply-progress observations exist per slice (no filesystem equivalent): #250 (4b),
  #262 (6a), #265 (6b), #266 (6c) among the consulted set.

## Artifact Retrieval Note (Traceability)

Hybrid store; the dispatcher supplied **repo-path locators**, so proposal, specs, design,
tasks and closure-notes were read from disk under `openspec/changes/refactor-fca-layout/`
(pre-move paths). Engram consulted this phase: `mem_search("sdd/refactor-fca-layout",
project: onevoicecut)` returned 20 previews (IDs 167, 175, 211, 212, 213, 215, 218, 220,
222, 223, 227, 230, 235, 247, 250, 253, 262, 263, 265, 266); **`mem_get_observation(262)`
was read in full** — it is the guard-RED source (`apply-progress-6a`: "13/13 groups failed
naming their file") and independently records the D4 jobs→transcripts disclosure.
No `verify-report` observation exists, so none was read.

## Scope of This Archive

Touched: `openspec/specs/` (2 capabilities created, 3 composed in place) and the change
folder's location (9 git renames). Not touched: `src/`, `tests/`, `openspec/config.yaml`,
`fca_config.yaml`, `.env`, `.atl/`, `.opencode/`. No historical artifact was rewritten —
archive moved bytes, it did not edit them.

**Commits (operator size decision, 2026-09-30):** the one-shot staged diff measured
**768 changed lines (733 insertions + 35 deletions)** — over the session's 400-line review
budget — so the operator chose to split it into three under-budget commits, each measured
before creation and created strictly in this order:

| Commit | Message | Changed lines |
|--------|---------|---------------|
| `12977b6` | `docs(openspec): archive refactor-fca-layout and add its archive report` | 247 (9 R100 renames, 0 content change, + this report) |
| `b0cf422` | `docs(specs): promote api-versioning and architecture-boundary delta specs` | 267 |
| `07cdb9e` | `docs(specs): promote job-cancellation, job-visibility, and operator-authentication deltas` | 254 (219 +/35 −) |

This paragraph itself landed in a fourth follow-up commit recording the split. Sanity after
the split: `pytest tests\test_architecture.py tests\test_fca_config.py -q` → **55 passed**.
Delivery (push/PR) remains the operator's decision.

## SDD Cycle Complete

The change is archived. Implementation: **complete — 118/118 tasks**. Verification: **no
verify-report artifact was ever persisted** (never requested as a phase); measured
final-state evidence of record — 2244 passed / 44 deselected / 0 skipped, mypy strict clean
over 373 files, guard 51 passed with 13/13 planted RED proven at 6a.2 — plus this phase's
own 55-passed guard re-run after promotion. Unfinished tasks: **none observed**. Unresolved
findings: **D1–D7 carried above, none blocking**, and OQ1 recorded as a sequencing choice.

## Artifact Persistence

- Openspec file: `openspec/changes/archive/2026-09-30-refactor-fca-layout/archive-report.md`
- Engram observation: topic key `sdd/refactor-fca-layout/archive-report`, project `onevoicecut`
