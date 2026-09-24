# Proposal: Refactor to FastAPI Clean Architecture Layout

> Phase: `sdd-propose` · Artifact store: hybrid (mirror of Engram `sdd/refactor-fca-layout/proposal`)
> Inputs: Engram #155 (`architecture/fca-refactor-plan`, exploration done this session), the
> `fastapi-clean-architecture` skill (SKILL.md + `references/architecture.md`), `openspec/config.yaml`,
> format models `video-transcription-pipeline/proposal.md` and `archive/2026-09-17-multi-operator-access/proposal.md`,
> and read-only inspection of the current tree.
> **[BINDING]** = user decision from exploration, not re-openable here.

## Intent

Two motivations, both real:

1. **Skill adoption exercise [BINDING].** The user is exercising the `fastapi-clean-architecture`
   (FCA) skill end-to-end on this real repository: **FULL adoption** of the skill's layout, with
   project-specific deviations where the skill's assumptions (ORM, JWT, single-process app) do not
   apply. What does not apply is **not** refactored — deviation is documented, not improvised.
2. **Genuine gains on the merits.** The current hexagonal layout is sound but coarse: `domain/`,
   `ports/`, `usecases/` are three flat packages spanning three very different capability clusters
   (job lifecycle, transcription, clip production), so any change touches "the core" and the
   boundary test sees only "core must not import adapters". FCA delivers:
   - **Module isolation** — three cohesive modules with per-module four-layer structure and
     enforced cross-module import rules, not one shared core.
   - **CQRS clarity** — writes and reads split into `commands/` vs `queries/`, one handler per file;
     the read paths (`GetJob`, `ListJobs`) become explicit instead of ad-hoc route logic.
   - **API versioning** — `/api/v1/...` gives future contract changes a home instead of a silent break.
   - **Security hardening** — pagination on `GET /api/jobs` (currently absent; a real OWASP API4
     finding) and `extra="forbid"` on request schemas (mass-assignment-shaped ambiguity closed).

This is a **behavior-preserving relocation plus the named HTTP-surface additions**. It is not a
rewrite of the pipeline.

## Scope

### In Scope

- Relayout of `src/onevoicecut/` to the FCA structure: one bounded context, three modules, four
  layers each, plus a `shared/` kernel (see *System Naming* and *Module Map*).
- CQRS split of the 15 function-style use cases into `application/use_cases/commands/` and
  `queries/`, one handler per file.
- `ports/` → per-module `domain/interfaces/`, remaining `typing.Protocol`.
- `main.py` as the web composition root + `{module}_module_api.py` per module; routers registered
  under `/api/{version}/{module}`.
- Presentation layer per module: `schemas/v1`, `routes/v1`, `controllers/v1` (schema → DTO →
  handler → schema, controllers thin).
- HTTP-surface changes: path migration `/api/...` → `/api/v1/...` (**breaking**, see *HTTP Surface*),
  pagination on `GET /api/v1/jobs`, `extra="forbid"` on all request schemas.
- `fca_config.yaml` created (systems, modules, API versions, tooling, security posture).
- `tests/test_architecture.py` rewritten to enforce the FCA layer import rules (replacing the
  hexagonal `domain/usecases/ports`-must-not-import-`adapters/runtime` guard with the skill's
  stronger, module-aware rule set).
- Test tree reorganized to mirror the source tree; **tests move with the code they cover in the
  same slice**.
- Documentation closure: `CLAUDE.md` architecture/HTTP-surface sections, `openspec/config.yaml`
  context block, README run commands where the entrypoint changes.

### Out of Scope (explicit non-goals)

- **No behavior change to pipeline, worker, render, or supervisor logic.** The load-bearing
  decisions in `openspec/changes/video-transcription-pipeline/design.md` are not reversible by this
  change: immutability of domain entities, derived progress (never a counter), single-writer rule,
  one supervised worker process per job, capability-declaration-over-silent-degradation,
  `SegmentKind` marking, device proof, `TrackingUnavailable` paths, atomic `save_chunk_result`,
  heartbeat/watchdog/reap semantics, drain cadences.
- **No ORM.** No SQLAlchemy, no Alembic, no database of any kind. Filesystem storage survives
  relocated, not rewritten.
- **No JWT.** Bearer tokens from `ONEVOICECUT_OPERATOR_TOKENS` survive, adapted to `CurrentPrincipal`.
  The three authorization invariants survive: deny-by-default generated from the route table,
  401 → 404 → 403 precedence, shared-read / owner-only-mutate — with their route-table-generated tests.
- **No publishing.** `PublishPort` stays declared and unimplemented.
- **No browser UI**, no new product features, no job-record on-disk format change, no multi-language
  support, **no new third-party dependencies** (a pure relocation owes no `pip install`).

## System Naming

**One system, named `pipeline`, with three modules** (`jobs`, `transcripts`, `clips`):

```
src/onevoicecut/systems/pipeline/{jobs,transcripts,clips}/
```

Justification:

- **One bounded context, not three.** The whole product is a single continuous flow on one shared
  server: admit → upload → chunk → transcribe → stitch → generate → render. The three modules share
  one `JobRecord`, one ownership model, one worker-supervision regime, and one operator team.
  Multiple *systems* in the skill's sense would imply independent deployment or ownership
  boundaries that do not exist here. The modules are staged capability clusters inside one context.
- **Why `pipeline` and not `transcription`:** proposal rev 4 of `video-transcription-pipeline`
  made rendered vertical clips the actual deliverable; a system named `transcription` would
  misname a context whose terminal artifact is a rendered clip. `pipeline` is already the repo's
  ubiquitous language ("the pipeline runs end to end" — CLAUDE.md).
- **Why not `systems/onevoicecut/`:** it would nest the package name inside itself
  (`onevoicecut/systems/onevoicecut/`) and add no information.
- The skill's repo-root `systems/` tree is adapted to this repo's **src layout**: `systems/` and
  `shared/` live under `src/onevoicecut/`, keeping one installable package, the existing
  `PYTHONPATH=src` convention, and the existing mypy scope.

## Module Map

| Module | Commands (writes) | Queries (reads, never mutate) | Domain | Interfaces (from `ports/`) | HTTP surface (v1) |
| --- | --- | --- | --- | --- | --- |
| `jobs` | `admit_job`, `ingest_media`, `cancel_job`, `resume_job` (+ `ownership` guard used by mutating paths) | `GetJob`, `ListJobs` (**new**, wrapping load + `derive_progress` and unscoped list + owner attribution) | `jobs.py`, `ids.py`, `media.py` | `MediaSourcePort` (+ storage seam — see OQ3) | `POST /api/v1/jobs`, `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}`, `PUT /api/v1/jobs/{id}/media`, `POST /api/v1/jobs/{id}/cancel` |
| `transcripts` | `plan_chunks`, `transcribe_job`, `stitch_transcript` | — (none today; progress surfaces through `jobs.GetJob`) | `chunking.py`, `transcript.py` | `AudioExtractorPort`, `TranscriptionPort` | none today (worker-driven; `transcripts_module_api` exports no router yet — stated honestly, not padded) |
| `clips` | `generate_artifacts`, `request_clip_export`, `render_clip`, `purge_job_artifacts` | `render_profiles` (registry resolution), `plan_trajectory`, `build_subtitle_cues` (pure derivations) | `generation.py`, `rendering.py`, `framing.py` | `TextGenerationPort`, `SubjectTrackerPort`, `VideoRenderPort`, `PublishPort` (declared-only) | `POST /api/v1/jobs/{id}/clips`, `GET /api/v1/jobs/{id}/clips/{clip_id}`, `GET /api/v1/jobs/{id}/clips/{clip_id}/{profile}` |
| `shared/` (kernel) | `application/principal.py` (from `adapters/web/auth.py` bearer-token map + `CurrentPrincipal` resolution) | same | `domain/errors.py` (`DomainError` hierarchy) | — | `presentation/security.py` (`CurrentPrincipal`, `require_roles` — unused today, no privileged routes exist) |

Plus, per the skill: `shared/infrastructure/settings.py` (relocated from `runtime/settings.py` —
the only environment reader; worker/render_worker import it), module `infrastructure/` for
module-owned adapters, and shared `infrastructure/` for domain-agnostic adapters.

**CQRS classification note:** the split is write-vs-read, not big-vs-small. Pure derivations that
touch no store (`plan_trajectory`, `build_subtitle_cues`, render-profile resolution) are reads and
sit in `queries/`; anything that persists or spawns goes in `commands/`. Handler shape (skill's
`{Name}Handler` class vs current module-level function) is a design-phase detail — either satisfies
"one handler per file"; design.md decides and the conversion is behavior-frozen.

**Adapter placement (behavior-frozen relocations):**

| Current | Target | Rule |
| --- | --- | --- |
| `adapters/storage/` | `shared/infrastructure/storage/` (or module infrastructure — OQ3 adjacent) | Relocated, never rewritten: atomic writes, derived progress, legacy decode |
| `adapters/web/` | split into `systems/*/presentation/` + `shared/` auth; **package deleted at Phase 5** | Route/schema/controller decomposition per skill |
| `adapters/ffmpeg/`, `adapters/asr/`, `adapters/vision/`, `adapters/llm/` | module `infrastructure/` when module-specific (`ffmpeg/subtitles`, vision, render), shared `infrastructure/` when domain-agnostic (extractor, ASR engines, Ollama) | Zero behavior change |
| `runtime/{worker,render_worker,supervisor,engine_resolver,tracker_resolver}.py` | stay in `runtime/` | See Deviation 3 — parallel composition roots, behavior-frozen |

## What Changes (summary of deltas beyond relocation)

1. **Path migration `/api/...` → `/api/v1/...` — BREAKING.** All 8 operations across 7 paths.
2. **Pagination on `GET /api/v1/jobs`** — `limit` (≤ 100) + offset paging; completeness restated as
   the union of pages (delta to `job-visibility`, whose VIS-05 currently requires "exactly N items"
   in one response). Server-side `mine` filter and shared-read semantics unchanged.
3. **`extra="forbid"` on all request schemas** — unknown JSON keys go from silently ignored to 422.
   A deliberate contract tightening, landed in the same slice as its test updates.
4. **`fca_config.yaml`** — records the system/module map, `api_prefix: /api/v1`, active `v1`,
   `database: filesystem`, `security.auth: bearer`, tooling (pytest, mypy).
5. **`tests/test_architecture.py` rewrite** — enforces: `domain` imports stdlib + `shared.domain`
   only (no FastAPI/Pydantic/infrastructure); `presentation → application → domain`;
  `infrastructure → application/domain`; no cross-module `domain`/`infrastructure` imports;
  wiring only in `main.py` / `*_module_api.py`.
6. **Entrypoint** — `main.py` becomes the web composition root (skill contract, parallel to
   `runtime/`); the documented uvicorn invocation updates in the closure slice. Whether
   `runtime.app:get_app` survives as a re-export alias is a design detail.

### HTTP Surface: how the breaking migration is handled

There is **no browser UI and no deployed external client** (CLAUDE.md: the HTTP surface is
authenticated but nothing renders it); consumers are the repo's own tests (~100 hardcoded `/api/`
path literals) and the local operator's curl invocations. Therefore:

- The migration is **atomic in one Phase-5 slice**: route prefixes, every test path literal, and
  every documented example change together; the slice is not done until the default suite is green.
- The route-table-generated 401/403 tests derive paths from `app.routes`, so they follow the
  prefix automatically — that machinery is preserved, not hand-updated.
- **No unversioned `/api` alias is kept** (documented deviation from the skill's "keep vN" gate —
  see Deviation 6). An alias would double the route surface for zero known consumers and leave the
  ambiguity the version prefix exists to close. If a real external client is discovered, the alias
  can be added as a small follow-up; this proposal does not foreclose it.
- `operator-authentication` AUTH-02's route enumeration and `job-cancellation`'s route path are
  updated in their deltas (listed under Capabilities).

## Capabilities

> Contract for `sdd-spec`: these are exactly the spec files to create/update.

### New Capabilities

- `architecture-boundary`: FCA layer import rules and cross-module isolation (the `test_architecture.py`
  rewrite as a normative spec — AST-enforced, violation = failing default run).
- `api-versioning`: versioned HTTP surface under `/api/v1`, version registry in `fca_config.yaml`,
  migration policy (atomic break, no unversioned alias, deprecation procedure for future versions).

### Modified Capabilities

- `operator-authentication`: authentication resolution relocates to `shared/presentation/security.py`
  as `CurrentPrincipal` over the unchanged bearer token map (no JWT); deny-by-default route-table
  test, fail-closed boot, constant-time comparison, and 401/404/403 precedence preserved verbatim;
  AUTH-02 route enumeration updated to `/api/v1/...` paths.
- `job-cancellation`: requirement text's route path becomes `POST /api/v1/jobs/{id}/cancel`;
  semantics (owner-only, idempotent, `control.json` seam) unchanged.
- `job-visibility`: listing gains bounded pagination (limit ≤ 100); VIS-05's single-response
  "exactly N items" is restated as completeness across the page union; shared read, owner
  attribution, legacy-null attribution, and server-side `mine` filter unchanged.

Unaffected (explicitly): `job-ownership`, `job-visibility`'s read-only status requirement,
`legacy-job-compatibility` (on-disk record format untouched), `worker-capacity-gate`,
`worker-liveness-hardening`, `slice6-speaker-mode`, and every delta spec of the in-flight
`video-transcription-pipeline` change (none name HTTP paths — verified by search).

## Approach

**Relocation first, deltas at the end, green at every seam.**

- **Relocation slices** are proven by the *unchanged* suite: move code + its tests + fix imports
  in one slice; the tests that passed before must pass after. Strict TDD's RED-before-GREEN does
  not manufacture a failing test for a path rename — the honesty rule here is "tests move with
  code, suite stays green, diff is reviewable".
- **Behavior slices** (pagination, `extra=forbid`, path migration, `test_architecture` rewrite,
  `GetJob`/`ListJobs` introduction) are strict RED-before-GREEN per `strict_tdd: true`.
- **Modules migrate one at a time** (`shared/` → `jobs` → `transcripts` → `clips`), each ending
  with the full default suite green and mypy strict clean. Old packages shrink as they drain;
  nothing is deleted until its replacement is green.
- **`runtime/` is touched only for import rewiring** — never for logic. Its slices are the ones a
  reviewer should read hardest, because a "pure import change" in `worker.py` is where a
  behavior freeze is easiest to break accidentally.
- Load-bearing decisions from `video-transcription-pipeline/design.md` are treated as
  **non-reversible by this change**; any slice that would alter one is rejected, not mitigated.

### Phasing (approved plan)

| Phase | Content | Ends with |
| --- | --- | --- |
| **0 — Prep** | Green baseline recorded; `fca_config.yaml` drafted; module map frozen; `test_architecture` rewrite *plan* written (not landed) | Baseline commit + plan artifact |
| **1 — Shared kernel + main.py** | `shared/{domain,application,infrastructure,presentation}` (errors, principal, settings, security); `main.py` error mapping (`DomainError` → 404/403/409/422, generic 500) | Suite green; auth tests still green via new wiring |
| **2 — jobs module** | jobs four layers, CQRS split, `GetJob`/`ListJobs`, job routes → `presentation/v1` (still under `/api` until Phase 5) | Suite green |
| **3 — transcripts module** | plan/chunk/transcribe/stitch move; ASR + extractor adapters → infrastructure | Suite green |
| **4 — clips module** | generation/render/trajectory/subtitles/purge move; ffmpeg-render/vision/llm adapters → infrastructure; `adapters/web` drained as clip routes land in module presentation | Suite green |
| **5 — HTTP surface** | `/api` → `/api/v1` atomic migration; pagination; `extra=forbid`; `adapters/web` deleted | Suite green; route-table auth tests green on v1 |
| **6 — Closure** | `test_architecture.py` rewrite lands; `CLAUDE.md` + `openspec/config.yaml` context + README updates; archive prep | Suite + mypy green; docs match tree |

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `src/onevoicecut/domain/` (9 modules) | Relocated | → `shared/domain/errors.py` + `systems/*/domain/{jobs,ids,media,chunking,transcript,generation,rendering,framing}.py` |
| `src/onevoicecut/ports/` (8 modules) | Relocated | → `systems/*/domain/interfaces/` (typing.Protocol unchanged) |
| `src/onevoicecut/usecases/` (15 modules) | Relocated + split | → `systems/*/application/use_cases/{commands,queries}/`; + `GetJob`, `ListJobs` |
| `src/onevoicecut/adapters/web/` (5 modules) | Split, then removed | → module `presentation/{schemas,routes,controllers}/v1` + `shared` auth; package deleted Phase 5 |
| `src/onevoicecut/adapters/{ffmpeg,storage,asr,vision,llm}/` (~25 modules) | Relocated | → `infrastructure/` (shared or module-owned); zero behavior change |
| `src/onevoicecut/runtime/` (9 modules) | Import rewiring only | Stays; `settings.py` relocates to `shared/infrastructure/`; workers/supervisor logic frozen |
| `src/onevoicecut/main.py` | **New** | Web composition root (skill contract) |
| `src/onevoicecut/shared/`, `src/onevoicecut/systems/` | **New** | Kernel + bounded context tree |
| `fca_config.yaml` | **New** | Systems/modules/versions/security/tooling registry |
| `tests/` (~181 files) | Relocated + updated | Mirrors source tree; path literals updated in Phase 5; move with code |
| `tests/test_architecture.py` | **Rewritten** | FCA layer rules replace hexagonal guard |
| `CLAUDE.md`, `openspec/config.yaml` context, `README.md` | Modified | Architecture description, HTTP surface, entrypoint, budget/policy notes |
| **Scale** | ~75 source files + ~181 test files + docs | ~2,120 tests, mypy strict over 256+ source files — every slice re-proven |

## Deviations from the skill (Output Contract)

| # | Skill rule | Deviation | Justification |
| --- | --- | --- | --- |
| 1 | SQLAlchemy 2.x + Alembic migrations; `infrastructure/database/` | **No ORM, no migrations.** `infrastructure/storage/` (filesystem), relocated from `adapters/storage/` | Atomic `save_chunk_result`, derived progress, and key-tolerant legacy decode are load-bearing design.md invariants; introducing an ORM would rewrite the one component the whole resume/progress model rests on. `fca_config.yaml` records `database: filesystem`. |
| 2 | JWT issuing/verification; `security.auth: jwt` | **Static bearer token map** (`ONEVOICECUT_OPERATOR_TOKENS`) adapted to `CurrentPrincipal`; `security.auth: bearer` (extends the template's `jwt\|none` enum) | JWT adds signing keys, expiry, refresh, and a new failure surface to a single-machine operator tool that already solved identity with a constant-time token map. The three authorization invariants and their route-table-generated tests are preserved verbatim. |
| 3 | One `main.py` composition root | `runtime/{worker,render_worker,supervisor}` remain **separate-process composition roots parallel to `main.py`** | They are distinct OS processes reading their own environment. They import `{module}_module_api` wiring; never the reverse. Their logic (single-writer, heartbeat, watchdog, drains, reaping) is behavior-frozen — a multi-hour transcription's supervision regime is not a layout concern. |
| 4 | Adapters module-owned under `infrastructure/` | Adapters relocate **with zero behavior change**; domain-agnostic ones (ffmpeg extractor, ASR engines, Ollama client) go to shared infrastructure rather than being duplicated per module | Capability declaration, `SegmentKind` marking, device proof, and `TrackingUnavailable` paths are specified elsewhere and must not drift during a move. |
| 5 | Execution steps: run `bandit` + `pip-audit` | **Not added** | A relocation owes no new dependencies (`multi-operator-access` set the precedent). The substantive OWASP controls (pagination, `extra=forbid`, `CurrentPrincipal`, deny-by-default) are in scope; scanner tooling can be its own change. |
| 6 | Breaking API change → add `vN+1`, keep old `vN` | `/api` migrates **atomically to `/api/v1` with no unversioned alias** | `/api` is pre-versioning legacy, not a maintained `v0` with a deprecation record; zero known external consumers (no browser UI). Keeping it would double the auth-tested route surface for nothing. Reversible: the alias can be added later if a consumer appears. |
| 7 | Repo-root `systems/`, `shared/`, `main.py` | All live under the `src` layout (`src/onevoicecut/systems/`, `src/onevoicecut/shared/`) except root `main.py` placement resolved in design | Preserves the single-package, `PYTHONPATH=src`, mypy-`src`-and-`tests` conventions recorded in `openspec/config.yaml`. |

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| **Interim boundary-enforcement gap.** The old `test_architecture.py` guards `domain/usecases/ports`; as files drain those packages the guard approaches vacuously passing, while the new FCA rules do not land until Phase 6 — leaving the `systems/` tree structurally unenforced for Phases 1–5 | **Med–High** | Flagged as OQ2: recommended mitigation is to land the new boundary rules **incrementally from Phase 1** (extend, don't merely plan-then-replace), keeping the old guards until their packages are empty. Phase 0 writes the rewrite plan either way. |
| **Import churn across ~75 source files breaks the suite mid-phase** | Med | One module per slice; tests move with code in the same slice; every slice ends with the full default suite + mypy green; split at seams, measure diff before commit |
| **`/api` → `/api/v1` breaks route-table tests, ~100 test path literals, and operator curl examples** | High *if* an undiscovered external client exists; Low for known consumers | Atomic Phase-5 slice (code + tests + docs together); route-table-generated auth tests follow `app.routes` automatically; no alias — stated openly so discovery of an external client triggers the alias follow-up (OQ not needed: none known, CLAUDE.md confirms no UI) |
| **`extra="forbid"` changes request validation from ignore-unknown to 422** — any existing test (or operator script) posting extra keys starts failing | Low | Same-slice test updates; listed as a named behavior delta, not smuggled into a relocation slice |
| **Pagination conflicts `job-visibility` VIS-05** ("listing MUST contain exactly N items" in one response) | Med (certain, actually — it *does* conflict) | `job-visibility` is a **Modified** capability: spec phase restates completeness as the union of pages; mine-filter, attribution, and shared-read requirements unchanged |
| **Runtime regression from import rewiring** — `worker.py`/`render_worker.py`/`supervisor.py` are behavior-frozen but their imports must follow module moves | Med | Runtime slices contain no logic edits (review rule); existing runtime tests unchanged in intent; a diff touching runtime bodies beyond imports is rejected |
| **CQRS conversion churn** — function-style use cases may become skill-template `Handler` classes, roughly doubling use-case diff size | Med | Handler shape decided in design.md; either shape satisfies one-handler-per-file; convert per module, never across modules in one slice |
| **Coordination with in-flight `video-transcription-pipeline`** — all 396 tasks checked but the change is **not archived**; its delta specs/tasks describe paths this refactor moves | Med | OQ1: recommended archive-first (specs promote to canonical, then deltas land against them), but either order is workable if no further slices are added to that change |
| **Doc drift** — CLAUDE.md's extensive hexagonal description becomes false during Phases 1–4 | Med (accepted, then closed) | Phase 6 closure is a named deliverable; CLAUDE.md is updated in the same change, not "later" |
| **Review size pressure** — session PR policy is 400 changed lines; config budget is 800/unit; this change is far larger than either | High without discipline | `delivery_strategy: auto-chain`, `chain_strategy: stacked-to-main`; split at seams (a module, or a half that is green alone), not at line counts; **measure every unit before commit** — the ×4 overestimate history is a warning, not a multiplier |
| Silent behavior drift in a "pure move" (e.g., a reordered `hmac` scan, a changed default) | Low | Contract/route tests unchanged in body; relocation slices prove equivalence by the existing suite, not by new assertions |

## Open Questions

| # | Question | Status |
| --- | --- | --- |
| OQ1 | **Archive order vs `video-transcription-pipeline`.** That change is fully checked but unarchived; its delta specs promote to canonical `openspec/specs/` at archive. Refactor deltas (e.g. `operator-authentication` route paths) are cleaner against canonical specs. | **OPEN.** Recommended: archive `video-transcription-pipeline` before Phase 1. Not blocking proposal acceptance; blocks only the spec-phase delta targeting if that change adds new slices. |
| OQ2 | **Interim boundary enforcement.** Should the new FCA import rules land incrementally from Phase 1 (extend `test_architecture.py` as modules appear), or only at Phase 6 as the approved phasing literally states (Phase 0 = rewrite *plan*, Phase 6 = rewrite *lands*)? | **OPEN.** The approved phasing is Phase 6; the enforcement gap above argues for incremental landing. This is a process decision, not a product decision — orchestrator may resolve it; neither answer changes scope. |
| OQ3 | **`TranscriptStoragePort` placement.** The single storage port spans all three modules (job records, chunk results, transcript, artifacts). Modules may not import each other's `domain/interfaces`, and `shared/` is for domain-agnostic code only. | **OPEN for design.** Candidates: (a) split into three narrow per-module interfaces that one filesystem adapter satisfies structurally; (b) keep one port in `jobs` and reach it only through wiring; (c) a justified `shared/` exception. Design.md decides; proposal does not pre-commit. |

## Rollback Plan

1. **Prerequisite:** Phase 0 records a green baseline commit. Every subsequent slice is a commit on
   the stacked chain; rollback is `git revert` of the slice.
2. **Per-slice revert safety:** each slice ends green alone, so reverting slice *N* leaves slices
   1..*N-1* passing. Relocation slices restore old import paths; tests moved in the same slice revert
   with them.
3. **On-disk data is never touched.** No job-record format change, no migration, no backfill —
   `legacy-job-compatibility` holds in both directions: pre-refactor and post-refactor builds read
   the same `job.json` population, and rolling back code requires no data cleanup.
4. **Phase 5 (path migration) rolls back as one unit:** route prefixes, test path literals, and docs
   revert together. The only operational cost is operator curl examples flipping back to `/api/...`
   — documented in the slice.
5. **No external state.** No database, no remote writes, no published artifacts (publishing is
   still a declared seam). Rollback remains a local operation. Inert per-job directories and renders
   may remain on disk; safe to delete manually.
6. **`fca_config.yaml` and the `test_architecture.py` rewrite revert with their own slices**
   (Phase 0/6); reverting the rewrite restores the hexagonal guard, which still correctly describes
   the pre-change tree.

## Dependencies

- **None new.** No third-party dependency is added; no slice owes a `pip install`.
- Existing prerequisites unchanged: venv + pip, ffmpeg system binary (integration tests), Ollama +
  model weights (`localmodel`/`paid` markers only — never the default run).
- **Phase 0 green baseline** is the only hard prerequisite.

## Success Criteria

- [ ] Every slice ends with `.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"` green
      and `.venv\Scripts\python.exe -m mypy src tests` clean (strict).
- [ ] No default-run test invokes a paid API or loads model weights (unchanged marker contract).
- [ ] `tests/test_architecture.py` enforces the FCA layer rules and fails the default run on a
      planted violation (RED-before-GREEN proof of the rewrite itself).
- [ ] All 8 operations serve under `/api/v1/...` with auth semantics byte-equivalent: deny-by-default
      route-table 401 test green, 401 → 404 → 403 precedence intact, owner-only mutation matrix intact.
- [ ] `GET /api/v1/jobs` paginates with `limit ≤ 100`; completeness across pages proven; `mine`
      filter and owner attribution unchanged.
- [ ] All request schemas carry `extra="forbid"`; unknown-key → 422 tested.
- [ ] `runtime/` worker, render_worker, and supervisor bodies unchanged except imports; their test
      files pass without semantic edits.
- [ ] Load-bearing design.md decisions verified untouched (immutability, derived progress,
      single-writer, capability refusal, `SegmentKind` marking, device proof).
- [ ] `fca_config.yaml` exists and matches the shipped tree (systems, modules, versions, security).
- [ ] `CLAUDE.md` + `openspec/config.yaml` context describe the new layout and HTTP surface; no
      stale hexagonal claims remain.
