# Closure Notes — refactor-fca-layout (slice 6c)

> Phase: `sdd-apply` · tasks 6c.1–6c.3 · artifact store: hybrid
> Recorded 2026-09-30 on branch `refactor-fca-layout` at HEAD `74d0d48`.
> First change commit `9a737db` (docs-only; parent/base `2dc6a80`).
> Deliverable of 6c.1 (evidence record) and 6c.2 (archive-prep notes), committed by 6c.3.
> No product code, test, or config line changes in this slice.

## 6c.1 — Proposal success-criteria sweep (proposal.md lines 302–320)

Scope: all ten Success Criteria, each verified against an observed run on this tree.
Test runs prepend the ffmpeg bin directory to `$env:Path` in the same command as pytest
(`...\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.1-full_build\bin`).

### 1. Every slice ends green (suite + strict mypy)

- `.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel" -q`
  → **2244 passed, 44 deselected, 0 skipped** (2 warnings, 59.98 s). Matches the recorded
  baseline; no skips, so the ffmpeg surface was exercised, not elided.
- `.venv\Scripts\python.exe -m mypy src tests`
  → **Success: no issues found in 373 source files** (exit 0).

### 2. No default-run paid/local invocation (marker contract)

- `pytest --collect-only -m "paid or localmodel"` → **44 of 2288 collected**, 2244 deselected.
- `-m paid` → **10**; `-m localmodel` → **34**. 10 + 34 = 44 = the deselected count, so every
  marked test is excluded from the default run and none of them ran. `pytest.ini` uses
  `--strict-markers`, so a typo would error rather than silently include.

### 3. Architecture guard fails on a planted violation — cited from 6a.2, not re-planted

- Recorded proof (tasks.md 6a.2 tick; Engram `sdd/refactor-fca-layout/apply-progress-6a`):
  baseline GREEN → **13/13 registered rule groups FAILED**, each naming its planted file →
  all plants removed → GREEN with a clean worktree. Plants were written into the real `src/`
  tree one at a time, so the observation is against `check_tree(SRC_ROOT)`, not a fixture.
- Current guard state on this tree: `pytest tests\test_architecture.py -q` → **51 passed**.
- No plant was created in 6c; the guard was not weakened.

### 4. Eight operations on `/api/v1`, auth semantics intact

- Route-table gate (AUTH-06 / AV-08), generated from `app.routes`, not a hand list:
  `tests\unit\adapters\web\test_auth_gate.py` → **18 passed**. The parametrized gate holds
  exactly **8** cases — `POST /api/v1/jobs`, `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}`,
  `POST .../cancel`, `PUT .../media`, `POST .../clips`, `GET .../clips/{clip_id}`,
  `GET .../clips/{clip_id}/{profile}` — the spec's eight (AV-01/AUTH-02). Within the 18:
  7 byte-identical 401 causes (`{"detail":"not authenticated"}` + `WWW-Authenticate: Bearer`),
  deny-by-default, authenticated pass, and the non-empty route table.
- Versioning: `tests\test_api_versioning.py` → **11 passed** (AV-02 every served route under
  `/api/v1/`; AV-03 six retired unversioned paths → 404 with no side effects — note it probes
  6 of the 8 operation paths, while AV-02 proves no unversioned route exists at all;
  AV-04 registry == route prefixes == {v1}; AV-06/AV-07 unknown JSON key → 422; AV-08).
- Precedence 401 → 404 → 403: `tests\unit\test_main.py` → **12 passed** (incl.
  `test_the_real_entrypoint_keeps_401_before_404_before_403`); `test_cancel_route.py` →
  **12 passed** (precedence test included); `test_upload_ownership.py` → **8 passed**.
- Owner-only mutation matrix: `tests\unit\adapters\web\test_mutation_ownership_matrix.py`
  → **9 passed** (OWN-05, generated from the route table).
- Clips auth parity: `test_clip_route_authorization_parity.py` → **6 passed**.
- No unversioned served path: re-grep `/api/(?!v1)` over `src/` + `tests/` = 15 hits, all
  accounted for — Ollama's own `/api/generate` and `/api/tags` (9, external service paths) and
  the AV-03 retired-path literals + version regex in `test_api_versioning.py` (6).
  Docs (CLAUDE.md, README.md, fca_config.yaml) = **0**.

### 5. Pagination bounds proven, `mine`/attribution unchanged

- `tests\systems\pipeline\jobs\presentation\test_job_list_pagination.py` → **13 passed**:
  VIS-10 `limit=101` → 422 before any listing work; VIS-11 seven malformed cases
  (non-integer/zero/negative/over-max `limit`; non-integer/negative/over-max `offset`) → 422;
  VIS-09 page size honored with owner attribution; omitted `limit` → bounded default 20;
  VIS-12 `mine` × pagination composition with page union; VIS-13/14 field allow-list.
- Completeness across pages: `tests\systems\pipeline\jobs\presentation\test_job_list_route.py`
  → **8 passed** (VIS-03/04/05 — `test_the_listing_hides_nothing` walks the union of pages;
  legacy `owner=null` rows surface; `test_reading_writes_nothing`).

### 6. `extra="forbid"` proven

- Both JSON request schemas declare it at their source: `AdmitJobRequest`
  (`jobs/presentation/schemas/v1/job_schemas.py:24`) and `ClipExportRequest`
  (`clips/presentation/schemas/v1/clip_schemas.py:31`); the list response schemas carry it
  too (`job_schemas.py:134, 148`). The raw-body upload carries no JSON schema — the AV
  requirement scopes it out.
- Behavior: AV-06 (unknown key on admission → 422, no job) and AV-07 (unknown key on clip
  export → 422, no export), both green in the 11 passed above.
- Honest scope note: no single structural test enumerates every request schema; the proof is
  schema-source inspection (2 of 2 body request schemas) plus the two behavioral 422 tests.

### 7. `runtime/` bodies — 4f receipt and chain-wide `git diff` classification

- **Scope A — slice 4f's own commit (`c09ee40`)**: the receipt holds. Default-context diff
  over `src/onevoicecut/runtime/` = `render_worker.py` **2 hunks** (inside the import block,
  including removal of a stale 3-line "Temporary composition wiring" comment) + `worker.py`
  **2 hunks** (import block) + new `runtime/storage.py` (64 lines, no behaviour).
  `supervisor.py`, `engine_resolver.py`, `tracker_resolver.py`, `app.py`: no diff in 4f.
  `tests/unit/runtime/` diff at 4f: import lines only.
- **Scope B — chain-wide `git diff 9a737db..HEAD -- src/onevoicecut/runtime/`** (identical to
  `2dc6a80..HEAD`; the first commit is docs-only): **8 files, +316/−504, 70 hunks**
  (unified=0). Every hunk classified, no hunk unattributed:
  - **35 import-only** — engine_resolver 10, render_worker 9, worker 7, app 4, supervisor 3,
    tracker_resolver 2.
  - **19 `storage:` parameter-annotation lines** — app 7, supervisor 7, render_worker 4,
    worker 1; all introduced by `8ebe96c` (6a, `TranscriptStoragePort` → `StorageComposite`);
    type annotations only, no body logic.
  - **16 other, each attributed to a slice (or flagged as non-slice)**:
    - app.py module-docstring rewrite — 1d/5a (the web entrypoint moved to `main.py`)
    - app.py interval-constant definitions removed — 1d (sourced from `main.py`; the shadowed
      local had left `WATCHDOG_SWEEP_INTERVAL_S` dead)
    - app.py 178-line block removed (`DrainConfig`/`WatchdogConfig`/`build_dependencies`/
      `build_app`/`get_app`) — 1d (the web composition root moved to `main.py`; the
      supervision loops stayed)
    - settings.py file deleted — 1b (relocated to `shared/infrastructure/settings.py`)
    - storage.py file added — 4f (`FilesystemTranscriptStorage` + `StorageComposite`)
    - supervisor.py win32 liveness probe, 4 hunks (constants + `_win32_process_is_alive`,
      old def removal, docstring, platform branch) — commit `3500cd3`, the ODD bugfix,
      **not a slice of this change**
    - supervisor.py module-docstring prose (`TranscriptStoragePort` → `storage`) — 8ebe96c
    - render_worker.py 4 handler call-site rewirings (`render_profiles` → `ff549a1`,
      `build_subtitle_cues` → `71b32f3`, `build_trajectory` → `7681c52`, `render_clip` →
      `3fffbaa`, the 4c CQRS conversions)
    - render_worker.py docstring prose naming `RenderProfilesHandler.handle` — 4c-era
    - worker.py 1 handler call-site rewiring (`run_generation` → `GenerateArtifactsHandler`
      `c925715`, 4c)
  - Honest conclusion against proposal criterion 7's literal wording ("unchanged except
    imports"): the chain is import-dominant (35/70) but **not imports-only** — 19 annotation
    lines, 5 design-mandated CQRS call-site rewires, the non-slice win32 fix, and the 1d
    composition-root move. Each is attributed above; none is an unaccounted body edit.
- Chain-wide `tests/unit/runtime/` diff: 28 files, +380/−248, dominated by 1b's settings-test
  restructure and `3500cd3`'s new `test_process_liveness_probe.py` (+136); at 4f the runtime
  test diff was import-lines-only (the receipt). Runtime tests pass inside the 2244.

### 8. Load-bearing design.md decisions untouched

- `design.md` and `proposal.md` have **one commit each** across the chain — `9a737db`, the
  change's first commit. No decision text was edited after writing.
- The six named decisions still enforced by tests in the default run (each run individually):
  - immutability (frozen domain entities): `jobs/domain/test_jobs.py` → **14 passed**
  - derived progress: `jobs/domain/test_progress.py` → **11 passed** (non-vacuous coverage via
    `queries/test_get_job.py` asserts)
  - single-writer / control.json cancellation: `jobs/infrastructure/storage/
    test_cancellation_control.py` → **11 passed**
  - capability refusal: `tests/shared/domain/test_capabilities.py` → **14 passed**
  - `SegmentKind` marking: `tests/contract/transcription.py` contract bodies in the suite
  - device proof at engine construction: `asr/local/test_faster_whisper_device_proof.py`
    → **4 passed**

### 9. `fca_config.yaml` matches the shipped tree

- Read against the tree: `pipeline/{jobs,transcripts,clips}` × `v1 active`, `api_prefix:
  /api/v1`, `auth: bearer`, `roles: []`, `rate_limiting: none`, `docs_in_production: false`,
  `database: filesystem` — all match; 6b.4's human cross-check recorded **no drift**, and the
  file is unchanged in 6c.
- `pytest tests\test_fca_config.py -q` → **4 passed** (AV-05 registry schema); AV-04
  route/registry equality green in the 11 above.
- Two standing disclosures (recorded, not fixed — design-pinned): `tooling.linter: ruff`
  while ruff is installed nowhere (D2), and `transcripts` declaring `v1 active` while owning
  no router (D3, design-intended).

### 10. CLAUDE.md + openspec/config.yaml context current; no stale hexagonal claims

- Delivered by slice 6b (`47bc9dd`, 241 lines; ticks `74d0d48`): CLAUDE.md architecture/ports/
  HTTP/config sections, `openspec/config.yaml` context, README prose. 6c edits none of them;
  `openspec/config.yaml` is untouched in this slice (test/build commands byte-identical).
- Re-checked here: CLAUDE.md + README legacy-path grep (`usecases/`, `ports/`, `adapters/web`)
  → **0**; `hexagonal` → **1** hit, the historical "moved the tree from hexagonal to FCA"
  sentence; unversioned `/api/(?!v1)` examples in docs → **0**.

### 6c.1 work-unit evidence

| Evidence | Value |
| --- | --- |
| Focused test command + result | `pytest -m "not paid and not localmodel" -q` → 2244 passed / 44 deselected / 0 skipped, plus the twelve focused files above run individually (all pass; counts inline) and `mypy src tests` → 373 files clean |
| Runtime harness | Real HTTP/ASGI boundary via the in-suite client (gate, pagination, precedence tests) and real ffmpeg integration tests inside the 2244 (bin on PATH) — not applicable-by-omission |
| TDD mode | Strict-TDD hard gate not triggered: this slice changes no `src/`, `tests/`, or config line, so there is no production change to hold RED-first. Verification/notes only, per tasks.md's explicit "relocation/notes slices do not manufacture RED" convention |
| Rollback boundary | Delete `closure-notes.md` (commit 1) / revert the two 6c commits; nothing else to roll back |

## 6c.2 — Archive-prep notes for `sdd-archive`

### Delta-spec confirmation (five, read in full against the tree)

- **api-versioning** — reflects shipped state: eight operations under `/api/v1` (AV-01/02
  observed via the 8 route-gate cases), no unversioned alias (AV-03), registry equality
  (AV-04), strict schemas (AV-06/07), generated auth coverage (AV-08). **No drift.**
- **architecture-boundary** — AB-01…AB-12 all live (51 tests; 6a.2's 13/13 planted RED);
  legacy-clause scenarios are conditional on packages that no longer exist (6a.1). **One
  prose/guard asymmetry — D4 below.**
- **job-cancellation** — CXL-01/CXL-02 as implemented (handler-dispatched cancel, owner-only
  403, control file untouched on refusal): green in `test_cancel_route.py` (12).
  **No drift.**
- **job-visibility** — VIS-03/04/05/09–14 as implemented and green (21 tests across the two
  presentation files). **Textual drift — D5 below** (stale open questions; un-named offset
  upper bound).
- **operator-authentication** — AUTH-02's enumerated list is exactly the observed eight
  routes; AUTH-06 derived gate live; AUTH-13/14/15 precedence green; AUTH-16/17 SecretStr
  discipline green (one `get_secret_value()` call in `main.py`, at token-map parse); no-JWT
  stack (AUTH-11) as specified. **No drift.**

### Drift list (record only — nothing fixed in 6c)

1. **D1 — `PublishPort` claim.** `openspec/specs/script-generation/spec.md:172` (canonical,
   promoted from `video-transcription-pipeline`): "`PublishPort` is declared and deliberately
   unimplemented." No module declares it — **0 occurrences of `PublishPort` in `src/`** — and
   `refactor-fca-layout/design.md:396` says it "does not exist in the tree today (verified) …
   when declared it belongs to `clips/domain/interfaces/`". Spec/reality drift, not a code
   defect; archive-time wording decision for the archiver (reading it as "declared in the
   specs" would soften it, but the sentence as written reads as a code declaration).
2. **D2 — `fca_config.yaml` `tooling.linter: ruff`** pins a linter installed nowhere on this
   machine (`.venv\Scripts\ruff.exe` absent; 0 hits in `requirements*.txt`). Design-pinned;
   disclosed in 6b.4, carried here.
3. **D3 — `fca_config.yaml` `transcripts` declares `v1 active` with no router.**
   Design-intended (transcripts is worker-driven); AV-04 compares versions, not module
   participation, so the registry test stays green. Disclosed, by design.
4. **D4 — architecture-boundary prose vs shipped guard.** The requirement says "A module MUST
   NOT import another module's `domain` or `infrastructure`", but
   `jobs/domain/jobs.py:6` imports `transcripts.domain.chunking` and the shipped guard
   permits it: the `JOBS_DOMAIN` group does not forbid `transcripts.domain`, and AB-07 is
   anchored owner-side (`jobs-domain-isolation` refuses *foreign* domains reaching into
   `jobs.domain`). Known and deliberate (the `derive_progress` coupling is documented in
   `test_architecture.py`'s group comments); no rule was weakened. At archive, either scope
   the requirement prose to the guarded directions or record the jobs→transcripts edge as an
   accepted exception — an archiver/orchestrator decision, not a 6c fix.
5. **D5 — job-visibility open questions are stale.** The delta still carries both "Open
   Questions" (default page size; offset upper bound) as unanswered, though design closed them
   (`limit` default 20; `offset` `le=10_000`) and slice 5b shipped tests for both —
   `test_vis11...[offset-above-maximum]` proves the bound the spec does not name. Textual
   drift only; behavior matches design.
6. **D6 — stale code comment (minor).** `runtime/worker.py:265` still carries the "Temporary
   composition wiring: 4e's `clips_module_api` …" comment whose `render_worker` twin 4f
   removed as stale. Prose only; no behavior impact. Listed for the archiver, not fixed.
7. **D7 — proposal criterion 7's wording.** "runtime bodies unchanged except imports" holds
   for slice 4f's own commit (receipt verified in §7) but not chain-wide: 19 `storage:`
   annotation lines, 5 CQRS call-site rewires, and the non-slice win32 fix also touched
   `runtime/`. Recorded so archive does not restate the literal wording without the
   classification above.

### OQ1 — archive order

OQ1 (whether this change archives before/after `video-transcription-pipeline`) remains an
**orchestrator sequencing choice, not a code dependency**: nothing in this change's tree,
specs, or tests imports from the archived change's directory, and the two changes' runtime
diffs are independent. `sdd-archive` may run in either order.

### What `sdd-archive` will do (unchanged by this slice)

Promote the five delta specs into
`openspec/specs/{api-versioning,architecture-boundary,job-cancellation,job-visibility,operator-authentication}/spec.md`
and archive the change directory. D1–D7 are inputs to that pass; none blocks it.

## 6c.3 — Verify + commit

- Commit 1 `chore(fca): closure verification and archive preparation` — **only**
  `closure-notes.md` (this file).
- Commit 2 `docs(openspec): tick slice 6c check-offs` — `tasks.md` ticks only.
- Neither commit touches `src/`, `tests/`, or `openspec/config.yaml`.
