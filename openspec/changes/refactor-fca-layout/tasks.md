# Tasks: Refactor to FastAPI Clean Architecture Layout

> Phase: `sdd-tasks` · Artifact store: hybrid (mirror of Engram `sdd/refactor-fca-layout/tasks`)
> Inputs: `proposal.md`, `design.md` (11 decisions), all five `specs/*/spec.md` (19 requirements,
> 40+ scenarios), `openspec/config.yaml`, format model
> `archive/2026-09-24-video-transcription-pipeline/tasks.md`, and the three loaded skills
> (`fastapi-clean-architecture`, `work-unit-commits`, `chained-pr`).

## Conventions for this task list

- **Every slice ends green**: `.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"`
  plus `.venv\Scripts\python.exe -m mypy src tests`. No slice may invoke a paid API or load model
  weights in the default run. No task requires media, model weights, or `.env`.
- **Behavior slices are strict RED-before-GREEN** (`strict_tdd: true`): write the failing test
  naming its spec scenario, then implement.
- **Relocation slices do not manufacture RED.** A pure move is proven by the unchanged suite:
  tests move with their code in the same slice, assertions untouched, suite green + mypy clean.
  Each relocation slice carries an explicit verification task saying so.
- **Work-unit commit boundary**: one slice = one commit unless the slice states a split. Each task
  line carries its unit tag `[unit X]`. Commit messages are Conventional Commits
  (`refactor(fca):`, `feat(api)!:`, `chore(fca):`, `docs:`).
- **Guard contract (AB-11, no vacuous-guard window)**: the slice that migrates a module into
  `systems/` MUST register that module's architecture rules in the same slice. Legacy hexagonal
  rules stay in force until `domain/`, `usecases/`, `ports/` no longer exist. No slice may reduce
  coverage of code that still exists.
- **`runtime/` body freeze**: `worker.py`, `render_worker.py`, `supervisor.py`,
  `engine_resolver.py`, `tracker_resolver.py` may change import lines only. A diff touching their
  bodies rejects the slice (explicit review note on slice 4f; standing rule for earlier slices).
- **Threat matrix**: every row in design.md's threat matrix is `N/A` (no executable docs, no git,
  no commit/push/PR automation in product code). No RED tests are manufactured for `N/A` rows.
  Existing security-invariant tests (path confinement, no `UploadFile` import, rename commit,
  list-form ffmpeg) ride the relocations unchanged.
- **Settled, not re-opened**: 401 to 404 to 403 precedence, `SecretStr`, OQ3 storage split,
  pagination bounds (default `limit=20`, `ge=1, le=100`; `offset` `ge=0, le=10_000`),
  incremental guard, atomic `/api/v1` with no unversioned alias.

---

## Slice 0a: Green baseline + `fca_config.yaml` (~80 lines)

Closes: `api-versioning` AV-05 (registry distinguishes active vs deprecated). Prepares AV-04
(equality with the route table lands in slice 5a, once routes carry the prefix).

- [x] 0a.1 Confirm the green baseline before any change: run the default suite and
      `.venv\Scripts\python.exe -m mypy src tests`; record both results as the Phase 0 baseline
      evidence. No code change, no commit — verification only. `[unit 0a-precheck]`
- [x] 0a.2 RED: `tests/test_fca_config.py` — parse root `fca_config.yaml`: `project.name`
      is `onevoicecut`, `api_prefix` is `/api/v1`, exactly one system `pipeline` with modules
      `jobs`/`transcripts`/`clips` each declaring `v1` active, **no version other than v1
      active** (AV-05), `database: filesystem`, `security.auth: bearer`, `tooling` names pytest
      + mypy. Fails because the file does not exist yet. `[unit 0a]`
- [x] 0a.3 GREEN: create `fca_config.yaml` at the repo root from the skill template, shaped per
      the design decision (versions carry `status: active|deprecated` so AV-05's distinction is
      structural). `[unit 0a]`
- [x] 0a.4 Verify: default suite + mypy green. Commit
      `chore(fca): add fca_config registry (v1, filesystem, bearer)`. `[unit 0a]`

---

## Slice 0b: Incremental architecture-guard first landing (~150 lines)

Closes: `architecture-boundary` AB-12 (the guard rewrite proven RED before GREEN on first
landing), AB-05 legacy half (restated under the new framework), AB-11 (the dual-coverage
mechanism is established here; per-module registration happens from each migration slice).
Accepted resolution of proposal OQ2: coverage lands incrementally from Phase 0, never as a
plan-then-replace at Phase 6.

- [x] 0b.1 RED: plant a forbidden import under legacy `domain/` (e.g. an
      `import onevoicecut.adapters...` line in a domain module) — the default run MUST fail
      naming that file; remove the plant and the run returns to green (AB-12, AB-05 legacy
      half). File: `tests/test_architecture.py`. `[unit 0b]`
- [x] 0b.2 GREEN: restructure `tests/test_architecture.py` into an incremental rule registry:
      keep every legacy hexagonal rule (`domain`/`usecases`/`ports` must not import
      `adapters`/`runtime`) in force verbatim; add the registration seam by which migration
      slices add FCA rules for a module **in the slice that migrates it** (AB-11 mechanism);
      rules remain AST/source-text based so a violation counts whether or not the imported
      package is importable. `[unit 0b]`
- [x] 0b.3 RED: AB-11 contract test — assert the registry accepts a rule group for a named
      subtree and that planted violations under both a registered `systems/` scaffold path and
      a legacy path fail (proves both sides can be covered mid-migration). `[unit 0b]`
- [x] 0b.4 Verify: suite + mypy green with no coverage of existing code removed. Commit
      `test(architecture): land incremental rule-registry guard (AB-11/AB-12)`. `[unit 0b]`

---

## Slice 1a: `shared/domain` kernel relocation + shared guard rules (~300 lines)

Closes: AB-08, AB-03 (shared/domain as the innermost layer), AB-11 (legacy `domain/` and
`ports/` still exist while `shared/` appears — dual coverage live), AB-12 (first FCA rule group
proven RED). Behavior-frozen relocation.

- [x] 1a.1 RED: register the `shared/` rule group in `tests/test_architecture.py` — plant
      `import onevoicecut.systems...` under a `shared/` file then fail (AB-08); plant a
      `shared/infrastructure`/`shared/presentation` import under `shared/domain/` then fail
      (AB-03); remove plants then green. Legacy rules must still bite a legacy plant in the
      same run (AB-11 both sides while `domain/`, `ports/` exist). `[unit 1a]`
- [x] 1a.2 GREEN: relocate `domain/errors.py` to `shared/domain/errors.py`, `domain/ids.py` to
      `shared/domain/ids.py`, `ports/capabilities.py` to `shared/domain/capabilities.py`;
      update every importer across `src/` and `tests/`; move the corresponding unit tests to
      `tests/shared/domain/`. **No new behavior test** — this is a behavior-frozen relocation
      proven by the unchanged assertions. `[unit 1a]`
- [x] 1a.3 Verify (relocation honesty): full default suite green with the *same* test bodies,
      mypy strict clean; `git diff` shows only file moves and import-line edits — `runtime/`
      bodies untouched (import lines only if any runtime module named these types).
      `[unit 1a]`
- [x] 1a.4 Commit
      `refactor(fca): move kernel vocabulary (errors, ids, capabilities) to shared/domain`.
      `[unit 1a]`

---

## Slice 1b: Settings tangle split + `SecretStr` (~350 lines)

Closes: `operator-authentication` AUTH-16, AUTH-17, AUTH-18; supports AB-08 (shared kernel must
not reach module code — the whole point of the split). Design decision: pure env parsing in
`shared/infrastructure/settings.py`; profile preflight moves to composition roots.

- [x] 1b.1 RED: extend `tests/shared/infrastructure/test_settings_auth.py` — AUTH-16: a
      `Settings` instance carrying operator tokens produces `repr`/`str` containing **no token
      value**, and `operator_tokens` is an instance of `SecretStr`. Fails against today's plain
      `str` field. `[unit 1b]`
- [x] 1b.2 GREEN: relocate `runtime/settings.py` to `shared/infrastructure/settings.py`; type
      `operator_tokens: SecretStr`; inline `DEFAULT_MAX_UPLOAD_BYTES` as `16 * 1024**3` and
      `DEFAULT_SCRIPT_TARGETS` as its literal string (killing the imports from
      `usecases.*`/`adapters.web.app`/`domain.rendering`); move `CHUNK_TIMEOUT_ENV_NAMES` and
      `load_env_file()` verbatim. `[unit 1b]`
- [x] 1b.3 RED: AUTH-17 AST test — every `get_secret_value()` call on the operator-token field
      resides in a composition root (`main.py` or `runtime/`); no file under `shared/`
      presentation/application/domain or (later) `systems/` extracts the plaintext. Fails while
      extraction still happens inside shared/module code paths. `[unit 1b]`
- [x] 1b.4 GREEN: move the one-time extraction to the composition root(s) that build the token
      map (during Phases 1–4 that is `runtime/app.py` until `main.py` lands in 1d — both are
      legal per AUTH-17); `parse_operator_tokens` keeps its str-in signature; existing
      `test_secret_discipline.py` and `InvalidTokenMap` refusal tests stay green unchanged
      (AUTH-18 preservation — no new test invented where one already exists; extend only if a
      gap is found). `[unit 1b]`
- [x] 1b.5 RED: preflight-drift test — pin `Settings().script_targets` is a subset of
      `RENDER_PROFILES` as a cross-module unit test so the inlined default cannot drift from
      the registry; plus a boot-refusal test: a dangling target still raises
      `RenderProfileInvalid` naming every offending row (the behavior of the moved validator,
      not a new rule). `[unit 1b]`
- [x] 1b.6 GREEN: extract the `_targets_name_defined_profiles` model validator out of `Settings`
      into `check_target_profiles`, a composition-root preflight called on the boot path before
      serving (same exception, same message discipline). `[unit 1b]`
- [x] 1b.7 Verify: `runtime/` import lines rewire to `shared.infrastructure.settings` — import
      lines only, bodies frozen; suite + mypy green. Commit
      `refactor(fca): split settings tangle, carry operator tokens as SecretStr`. `[unit 1b]`

---

## Slice 1c: `Principal` + `CurrentPrincipal` security layer (~300 lines)

Closes: AUTH-10, AUTH-11, AUTH-12 (extended); AUTH-02 through AUTH-06 preserved through the new
wiring (deny-by-default generated tests stay green, now derived via the dependency). Design
decision: no JWT; static bearer map adapted to `CurrentPrincipal`.

- [x] 1c.1 RED: AUTH-10 — a request bearing `Bearer t-a` against a server configured with
      operator `a`/token `t-a` resolves `CurrentPrincipal` to identity `"a"`; the use case
      receives `"a"` as its principal; no token value crosses from presentation into
      application (assert the principal carries only identity/roles). Fails: the type and
      dependency do not exist yet. `[unit 1c]`
- [x] 1c.2 RED: AUTH-11 — inspection test over the shipped auth stack: no JWT issuing or
      verification component participates; only the static operator-token map can authenticate.
      Fails pre-implementation because `shared/application/principal.py` and
      `shared/presentation/security.py` do not exist (import failure is the RED). `[unit 1c]`
- [x] 1c.3 GREEN: create `shared/application/principal.py` — frozen `Principal(identity, roles)`
      plus `parse_operator_tokens`/`build_authenticator` moved **verbatim** from
      `adapters/web/auth.py` (constant-time scan behavior-frozen); create
      `shared/presentation/security.py` — `make_current_principal(authenticate)` returning an
      `Annotated[Principal, Depends(...)]`, unconstructable without an authenticator
      (deny-by-default in two layers). `[unit 1c]`
- [x] 1c.4 GREEN: rewire the still-present `adapters/web` routes from the closure-over-
      `WebDependencies` `_authorized` helper to `principal: CurrentPrincipal`; routes stay
      under `/api` until Phase 5. Existing generated 401 (AUTH-06) and owner-only 403 checks
      derive from `app.routes` and must pass without hand-edited path lists (AV-08's machinery
      preserved). AUTH-12 log discipline re-verified by extending `test_secret_discipline.py`
      if any gap is found (no invented duplicate). `[unit 1c]`
- [x] 1c.5 Verify: AUTH-02 through AUTH-06 generated/precedence tests green through the new
      dependency wiring; suite + mypy. Commit
      `refactor(fca): resolve principals through CurrentPrincipal (bearer map, no JWT)`.
      `[unit 1c]`

---

## Slice 1d: `main.py` composition root + central `DomainError` handler (~350 lines)

Closes: central error mapping equivalence (existing status-code integration tests preserved),
AUTH-12 (unhandled 500 leaks no headers or body), AUTH-13/14/15 precedence preserved through
the new root. Design decision: one handler installed at the composition root; presentation-level
`HTTPException`s for pure HTTP/state concerns stay in route/controller code.

- [x] 1d.1 RED: central-handler test — raise a novel `DomainError` from a test route on
      `create_app` (imported from `main.get_app`) and assert it maps to 422 without any
      route-local `try`; plus a planted unhandled `Exception` maps to 500 with no headers/body
      (AUTH-12). Fails: no central handler exists. `[unit 1d]`
- [x] 1d.2 GREEN: create `src/onevoicecut/main.py` as the web composition root: builds
      `Settings`, parses the token map (the one `get_secret_value()` call), runs the
      `check_target_profiles` preflight, constructs storage/auth dependencies, assembles the
      existing routers, installs the `DomainError` handler per the design table (JobNotFound
      404, JobNotOwned 403, JobAlreadyExists/ArtifactsNotAvailable 409, UploadTooLarge 413,
      UnsupportedContainer 415, validation 422, other `DomainError` 422, unhandled 500) and the
      generic `Exception` handler; lifespan/supervisor startup wiring moves with it —
      supervisor loops themselves stay in `runtime/` untouched. `[unit 1d]`
- [x] 1d.3 GREEN: `runtime/app.py:get_app` becomes a one-line re-export of `main.get_app`
      (lives until Phase 5); all existing tests importing `runtime.app:get_app` keep passing
      unchanged. `[unit 1d]`
- [x] 1d.4 REFACTOR: remove route-local `DomainError` translation now subsumed by the central
      handler; keep the documented presentation-level `HTTPException`s verbatim (Content-Length
      413 pre-check, "job not PENDING"/"job not COMPLETED" 409, malformed-id 404). Equivalence
      proven by the existing integration tests with unchanged bodies. `[unit 1d]`
- [x] 1d.5 Verify: suite + mypy; 401 to 404 to 403 precedence tests green through `main.get_app`.
      Commit `refactor(fca): make main.py the web composition root with central error mapping`.
      `[unit 1d]`

---

## Slice 2a: jobs domain + interfaces + jobs guard rules (~250 lines)

Closes: `architecture-boundary` AB-01, AB-02, AB-04, AB-05, AB-06, AB-07, AB-09, AB-10
registered **for the jobs module from this slice** (no vacuous-guard window), AB-11 dual
coverage while legacy packages still exist, AB-12 (plant proof for the new rule group).
Behavior-frozen relocation.

- [x] 2a.1 RED: register the `jobs` rule group in `tests/test_architecture.py` and plant, one
      at a time: presentation importing infrastructure (AB-01), application importing
      presentation (AB-02), domain importing `fastapi`/`pydantic` (AB-04), domain importing
      `onevoicecut.adapters`/`onevoicecut.runtime` (AB-05), jobs application importing
      `onevoicecut.systems.pipeline.transcripts.infrastructure` (AB-06 — AST does not require
      the target to exist), clips-style domain import of `jobs.domain` planted under a
      `systems/` path (AB-07), presentation importing a concrete adapter (AB-09), application
      importing `onevoicecut.runtime` (AB-10); each plant fails naming its file; legacy plants
      still fail in the same run (AB-11). `[unit 2a]`
- [x] 2a.2 GREEN: create `systems/pipeline/jobs/domain/` — relocate `domain/jobs.py`,
      `domain/media.py`, and `usecases/ownership.py` (as `jobs/domain/ownership.py`, a pure
      domain rule per design); create `systems/pipeline/jobs/domain/interfaces/` — relocate
      `ports/media_source.py` as `MediaSourcePort` and declare the new narrow `MediaProbePort`
      (probe-only slice of `AudioExtractorPort`, per the upload decision); `JobStore`
      Protocol declared per the OQ3 method table; update all importers; move unit tests to
      `tests/systems/pipeline/jobs/`. **No new behavior test** — behavior-frozen relocation
      proven by unchanged assertions (the `JobStore` Protocol itself is structure, exercised
      for behavior in slice 2b). `[unit 2a]`
- [x] 2a.3 Verify (relocation honesty): suite green with same test bodies + mypy; legacy
      `domain/` and `ports/` shrink but their guard rules stay in force (AB-11). Commit
      `refactor(fca): migrate jobs domain and interfaces into systems/pipeline/jobs`.
      `[unit 2a]`

---

## Slice 2b-i: Storage core extraction + monolith delegation (~230 lines)

Closes: OQ3 groundwork (domain-agnostic core takes Path/str/dict only — supports AB-08 by
construction); preserves atomic `save_chunk_result`, derived progress, key-tolerant legacy
decode **byte-frozen** (existing storage tests unchanged). Split at the core/facade seam: both
halves of 2b are green alone.

- [x] 2b-i.1 RED-equivalent verification first: relocate the existing storage test bodies
      covering layout helpers, atomic rename-commit, and key-tolerant JSON decode to
      `tests/shared/infrastructure/storage/` — they fail on import while the modules are
      absent (honest RED-by-import for a pure extraction; **no new assertions invented**).
      `[unit 2b-i]`
- [x] 2b-i.2 GREEN: create `shared/infrastructure/storage/core.py` from
      `adapters/storage/filesystem_transcript_storage.py` + `serialization.py` primitives —
      on-disk layout helpers, atomic rename-commit, key-tolerant JSON; signatures take
      `Path`/`str`/plain dicts only, never a domain type (AB-08 by construction). `[unit 2b-i]`
- [x] 2b-i.3 GREEN: refactor the existing monolith to delegate its primitive operations to
      `core` (internal re-plumbing only — every public method keeps its exact behavior;
      the unchanged suite is the equivalence proof; this prevents core and monolith from
      becoming two implementations of one layout). `[unit 2b-i]`
- [x] 2b-i.4 Verify: suite + mypy; `git diff` on `adapters/storage/` shows delegation, not
      logic edits. Commit `refactor(fca): extract domain-agnostic storage core`. `[unit 2b-i]`

---

## Slice 2b-ii: `JobStore` facade over the core (~220 lines)

Closes: OQ3 jobs half (narrow per-module Protocol, structural satisfaction).

- [x] 2b-ii.1 RED: move the job-path storage tests (job dir, source path, create/load/update,
      list FIFO order, save/load media, heartbeat freshness fail-closed, request/cancellation
      read) to `tests/systems/pipeline/jobs/infrastructure/storage/` — fail on import until
      the facade exists; add a structural-typing assertion that the facade satisfies `JobStore`.
      Existing assertions move verbatim (behavior-frozen). `[unit 2b-ii]`
- [x] 2b-ii.2 GREEN: create `systems/pipeline/jobs/infrastructure/storage/job_store.py` —
      facade importing only `jobs.domain` plus `core`; composition roots construct `core` once
      and build the facade. The monolith still satisfies `JobStore` structurally, so
      `runtime/` keeps using it until slice 4f — no runtime edit in this slice. `[unit 2b-ii]`
- [x] 2b-ii.3 Verify: suite + mypy; AB rules green (infrastructure imports own domain + shared
      core only). Commit `refactor(fca): add jobs JobStore facade over storage core`.
      `[unit 2b-ii]`

---

## Slice 2c: jobs commands — CQRS handler conversion (~350 lines)

Closes: `job-cancellation` CXL-01/CXL-02 at the handler layer (HTTP proof lands 2e/5a);
AUTH-15 ownership stays out of presentation (handlers call the domain ownership rule);
admission/ingest behavior preservation (existing use-case tests move). Design decision: skill
`Command`/`Query` dataclasses + `Handler` classes, verbatim function bodies.

- [x] 2c.1 RED: handler-shape tests (moved from the current use-case tests, same assertions)
      — `AdmitJobHandler`, `IngestMediaHandler`, `CancelJobHandler` exist under
      `systems/pipeline/jobs/application/use_cases/commands/`, each with a frozen `{Name}Command`
      carrying `principal: Principal` where identity matters and a `handle()` method; fail on
      import until handlers land. `[unit 2c]`
- [x] 2c.2 GREEN: convert `usecases/{admit_job,ingest_media,cancel_job}.py` into the three
      handler files — wrap-and-rename only; bodies verbatim (ingest keeps statement order:
      ownership, state check, size pre-check, store, re-read, probe, `save_media`,
      `update_job(QUEUED)`; command carries `AsyncIterator[bytes]` + percent-decoded filename);
      `cancel_job` writes `control.json` through the injected store seam unchanged (CXL-01
      recording semantics). `[unit 2c]`
- [x] 2c.3 GREEN: rewire callers — `runtime/` composition and the still-present
      `adapters/web` routes obtain handlers from temporary wiring in the composition root
      (construction stays out of routes; `WebDependencies` pattern preserved until 2e
      introduces `jobs_module_api`). `resume_job` is **not** converted here — its
      `pending_chunks` derivation is pure over `ChunkResult` (transcripts-owned); it lands in
      slice 3c to satisfy AB-06/07 (destination unchanged: jobs command). `[unit 2c]`
- [x] 2c.4 Verify: every existing admit/ingest/cancel test green against handlers without
      semantic edits; suite + mypy. Commit
      `refactor(fca): convert jobs write use cases to Command/Handler files`. `[unit 2c]`

---

## Slice 2d: `GetJob` / `ListJobs` queries (~300 lines)

Closes: `job-visibility` VIS-03, VIS-04 at the query layer; AUTH-14 handler half
(JobNotFound before filesystem side effects); read-only GET guarantee preserved (no store
writes from either query). New behavior — strict RED-first per design testing strategy.
Pagination slicing is **deferred to slice 5b** (design rollout: queries land Phase 2,
pagination tests land Phase 5).

- [x] 2d.1 RED: `GetJobQuery` — returns the record plus `derive_progress` output; unknown or
      malformed id raises `JobNotFound`; the fake store records zero mutations across the call
      (read-only). Fails: query does not exist. `[unit 2d]`
- [x] 2d.2 GREEN: create `systems/pipeline/jobs/application/use_cases/queries/get_job.py`
      (`GetJobQuery`/`GetJobHandler`) wrapping load + `derive_progress` verbatim. `[unit 2d]`
- [x] 2d.3 RED: `ListJobsQuery` — unscoped `list_jobs()` source (the same listing reconcile
      uses), every job returned with owner attribution (VIS-03), legacy `owner=None` surfaces
      as null attribution (VIS-04), server-side `mine` filter narrows the tuple before any
      presentation concern. Fails: query does not exist. `[unit 2d]`
- [x] 2d.4 GREEN: create `queries/list_jobs.py` (`ListJobsQuery`/`ListJobsHandler`) —
      attribution and `mine` filtering verbatim from the current route logic it will replace
      in 2e. `[unit 2d]`
- [x] 2d.5 Verify: suite + mypy; commit
      `feat(jobs): add GetJob and ListJobs CQRS queries`. `[unit 2d]`

---

## Slice 2e: jobs presentation + `jobs_module_api` (~450 lines) — near/over 400, split if measured over

Closes: CXL-01/CXL-02 (route to controller to handler dispatch), AUTH-13, AUTH-14, AUTH-15
(precedence on all five job operations), AUTH-06 generated coverage still green, read-only GET
test preserved. Relocation + decomposition: routes move under module presentation still at the
`/api` prefix (version flip is Phase 5).

- [x] 2e.1 RED-by-move: relocate the jobs route/schema tests from `tests/unit/adapters/web/` to
      `tests/systems/pipeline/jobs/presentation/` — they fail on import until wiring lands
      (honest for decomposition; **bodies unchanged**: same status codes, same 401 to 404 to
      403 precedence, same effects). `[unit 2e]`
- [x] 2e.2 GREEN: create `systems/pipeline/jobs/presentation/{schemas,routes,controllers}/v1/`
      from `adapters/web/schemas.py` (jobs schemas) and `adapters/web/routers/jobs.py` — thin
      controllers (schema to DTO, validate ids 404, translate `JobNotOwned` to 403 and nothing
      else), routes declaring `principal: CurrentPrincipal` with **relative** paths; create
      `systems/pipeline/jobs/jobs_module_api.py` wiring handlers + storage facade (skill
      pattern); `main.py` registers the jobs router at prefix `/api/jobs` (still unversioned).
      `[unit 2e]`
      **Execution note (measured against FastAPI 0.141.1):** the prefix is carried by each
      router, not supplied by `include_router`. An outer prefix wraps the routes in an
      `_IncludedRouter` whose nested `APIRoute.path` omits it, and the generated AUTH-06/OWN-05
      gates build their request URLs from `route.path` — they would call `/{job_id}/cancel`
      and 404. Router-carried prefix keeps the route table true to the served paths;
      `main.py` registers both routers prefix-less. Everything else matches the task.
- [x] 2e.3 GREEN: drain the five jobs operations and their schemas out of `adapters/web/`
      (clips operations and any shared schema remainder stay until 4e); relocate the async
      upload adapter `adapters/storage/media_source.py` to
      `systems/pipeline/jobs/infrastructure/` (the one async port stays web-only);
      `FfmpegAudioExtractor` continues to satisfy `MediaSourcePort`/`MediaProbePort`
      structurally and is bound at composition roots (main may import any module — wiring
      roots are excepted from cross-module rules). Construction of handlers/adapters moves
      into `jobs_module_api`/`main.py` — presentation constructs nothing (AB-09). `[unit 2e]`
- [x] 2e.4 Verify: route-table-generated 401 (AUTH-06) and owner-only 403 checks derive from
      `app.routes` and pass with no hand-maintained list; CXL-01/CXL-02 and AUTH-13/14/15
      integration tests green with unchanged bodies; suite + mypy. Commit
      `refactor(fca): move jobs HTTP surface into module presentation`. `[unit 2e]`
      Verified on this tree: **2161 passed, 44 deselected, 0 skipped** (default run);
      `mypy src tests` clean over 323 files; `tests/test_architecture.py` 15 passed.
      RED-by-move proof: before wiring, the relocated suite raised
      `ModuleNotFoundError: No module named 'onevoicecut.systems.pipeline.jobs.presentation'`
      (2 collection errors) plus `FileNotFoundError` on the not-yet-created
      `presentation/routes/v1/job_routes.py` — tally `1 failed, 139 passed, 2 errors`.

---

## Slice 3a: transcripts domain + interfaces + transcripts guard rules (~250 lines)

Closes: architecture-boundary rule registration **for transcripts from this slice** (AB-01,
AB-02, AB-04, AB-05, AB-06, AB-07, AB-09, AB-10, AB-11, AB-12 — plant proofs), dual coverage
with legacy packages. Behavior-frozen relocation.

- [x] 3a.1 RED: register the `transcripts` rule group and plant violations per rule (same
      method as 2a.1, now under `systems/.../transcripts/` trees, plus a plant of
      `transcripts/application` importing `jobs.infrastructure` for AB-06); each fails naming
      its file; legacy plants still fail (AB-11). `[unit 3a]`
- [x] 3a.2 GREEN: relocate `domain/chunking.py` and `domain/transcript.py` to
      `systems/pipeline/transcripts/domain/`; relocate `ports/audio_extractor.py` and
      `ports/transcription.py` to `systems/pipeline/transcripts/domain/interfaces/`
      (`AudioExtractorPort`, `TranscriptionPort`); declare `TranscriptStore` per the OQ3
      method table (`save_chunk_result` MUST stay atomic — docstring moves with the method);
      update importers; move tests to `tests/systems/pipeline/transcripts/`. **No new
      behavior test** — behavior-frozen relocation. `[unit 3a]`
- [x] 3a.3 Verify: suite + mypy; runtime bodies untouched (import lines only). Commit
      `refactor(fca): migrate transcripts domain and interfaces into systems/pipeline`.
      `[unit 3a]`
      Verified on this tree: **2172 passed, 44 deselected, 0 skipped** (default run;
      2161 baseline + the 11 plant tests 3a.1 added, none lost); `mypy src tests` clean
      over 331 files; `tests/test_architecture.py` 26 passed. RED-by-move proof: with the
      four modules relocated and importers unwired — `257 passed, 33 errors` over
      `tests/unit/{domain,ports,usecases}` plus `4 errors` for the four relocated test
      files, every error a `ModuleNotFoundError` naming `onevoicecut.domain.chunking` /
      `onevoicecut.domain.transcript`, and direct import probes failing for all four
      legacy paths (`domain.chunking`, `domain.transcript`, `ports.audio_extractor`,
      `ports.transcription`). **Option A (recorded decision)**: the
      `JOBS_DOMAIN_ISOLATION` conflict this task's port relocation would otherwise hit —
      probe-proven, engram observation `#223` — was resolved by moving
      `{SourceMedia, AudioTrack, MediaProbe, FrameSize}` + `SpeakerMode` to
      `shared/domain/` first (kernel-vocabulary precedent, design 1a), as a four-commit
      shim → flip → unshim chain `9a98cf4` → `4261432` → `2618708` → `f9b1e02`, each
      green alone (141 / 59 / 269 / 14 lines); it also absorbs 4a's `FrameSize`
      landmine. The migration commit itself: 95 files, +192/−151 = **343 git lines**.
      Two specials: `jobs/.../commands/ingest_media.py` retyped `AudioExtractorPort` →
      jobs-owned `MediaProbePort` (JOBS_APPLICATION may not import `transcripts`; the
      handler only ever calls `probe`), and `.gitignore`'s bare `transcripts/`
      generated-artifacts pattern was anchored to `/transcripts/` — the unanchored form
      silently ignored every new file under the relocated source package.

---

## Slice 3b: `TranscriptStore` facade (~350 lines)

Closes: OQ3 transcripts half; atomic `save_chunk_result` and legacy decode proven on the
facade by the **moved, unchanged** storage tests (no invented duplicates).

- [x] 3b.1 RED-by-move: relocate chunk-plan/result/transcript/export storage tests to
      `tests/systems/pipeline/transcripts/infrastructure/storage/` — fail on import until the
      facade exists; include the crash-simulated atomic-write integration test unchanged;
      structural assertion that the facade satisfies `TranscriptStore`. `[unit 3b]`
      → moved `test_atomic_chunk_results.py` (9 tests, `git mv`, bodies verbatim except the
      three tests whose subject *is* the job record, retargeted to `FilesystemJobStore`:
      `update_job` is admission's method and the moved file may not reach back into
      `adapters/storage`) and split `test_filesystem_job_artifacts.py` — 13
      plan/transcript/export tests + the new structural test to
      `test_transcript_store.py`; the 3 `artifacts.json` tests stayed adapter-side (clip
      state, 4b). RED observed before the package existed: **2 collection errors, both
      `ModuleNotFoundError: No module named 'onevoicecut.systems.pipeline.transcripts.
      infrastructure'`**, 106 passed elsewhere in the same run. No behavior tests invented;
      no assertion body changed beyond the fixture retargeting.
- [x] 3b.2 GREEN: create `systems/pipeline/transcripts/infrastructure/storage/transcript_store.py`
      over `core` (imports own domain + core only); composition roots build it beside
      `JobStore`. Monolith still satisfies `TranscriptStore` structurally — `runtime/` edits
      deferred to 4f. `[unit 3b]`
      → `FilesystemTranscriptStore(core)` with the 9 protocol methods + `job_dir` (the moved
      tests locate the `.txt` through it, and `writable` needs no private access).
      **Codecs moved in**: tasks.md is silent, but "imports own domain + core only" forces
      `encode/decode_chunk_plan|chunk_result|transcript` + `_job_id`, `_word_timings`,
      `_segment`, `_segments` out of `adapters/storage/serialization.py` — a codec taking
      `ChunkResult` can live neither in the domain-agnostic `core` (AB-08: no `systems.*`
      vocabulary into `shared`) nor in the adapter the facade replaces. They are re-exported
      `X as X` for mypy `no_implicit_reexport`, the same seam 2b-ii used for the jobs half.
      `runtime/` untouched: no src composition root constructs either facade today, so
      "beside `JobStore`" holds vacuously exactly as in 2b-ii — wiring is 4f.
- [x] 3b.3 Verify: suite + mypy (derived-progress and resume tests still green — behavior
      frozen). Commit `refactor(fca): add transcripts TranscriptStore facade`. `[unit 3b]`
      → RED `106 passed, 2 errors`; focused `133 passed`; full default suite **2173 passed,
      44 deselected, 0 skipped** (2172 baseline +1 = the structural conformance test);
      `mypy src tests` clean over **337** files (+6 = the 4 empty `__init__.py`, the facade,
      the recreated artifacts file); `tests/test_architecture.py` **26 passed**. Diff for
      this unit: **545 insertions / 301 deletions = 846 lines** — over the 800 line budget
      and the 400 line default; it cannot shrink further (see return summary: a split at
      the seam would commit the RED-by-move tests green-alone, which is the one thing the
      slice's RED *is*), so `size:exception` is recommended rather than a silent split.

---

## Slice 3c: transcripts commands + `resume_job` landing (~400 lines)

Closes: CQRS conversion for plan/transcribe/stitch (behavior preservation); design footnote —
`resume_job`'s `pending_chunks` derivation is pure over `ChunkResult` and lives with
transcripts; the supervisor reaches it through the transcripts module API (AB-06/07
compliance; destination stays the jobs command set per the module map).

- [x] 3c.1 RED: handler-shape tests for `PlanChunksHandler`, `TranscribeJobHandler`,
      `StitchTranscriptHandler` (moved assertions, import-fail RED); plus a RED test that
      `resume_job`'s pending-chunk derivation is exposed through the transcripts module API and
      that the jobs-side resume command receives it via a **jobs-owned** interface declared in
      `jobs/domain/interfaces` (no `jobs` application import of `transcripts.domain` — AB-06/
      AB-07). Fails: handlers and seam absent. `[unit 3c]`
      → import-fail RED observed before each GREEN, in three commits: **A1 7 collection
      errors** naming `transcripts_module_api` (the 5 moved transcribe tests + cancel-boundary
      + chunk-timeout wiring), **A2 3 errors** naming `commands.plan_chunks` (2 moved tests +
      `test_flac_bitrate` transitively — the cross-test import flipped atomically), **A3
      4 errors** naming `commands.stitch_transcript` (3 moved tests + the new
      `test_handler_shape.py` — all three Command/Handler pairs frozen-dataclass + `handle()`
      asserted, no principal field: transcripts is worker-driven); seam RED **1 error**
      naming `jobs/.../commands.resume_job` (`tests/.../test_resume_seam.py`, written with
      the shape test, left untracked until 3c.3 so every commit stays green). No behavior
      tests invented; moved assertions unchanged.
- [x] 3c.2 GREEN: convert the three use cases to `systems/pipeline/transcripts/application/
      use_cases/commands/` handlers (bodies verbatim); create `transcripts_module_api.py`
      exporting the wiring and **no router** (stated honestly — transcripts is worker-driven).
      `[unit 3c]`
      → `TranscribeJobCommand/Handler` (A1 `37f8385`, 288 lines), `PlanChunksCommand/Handler`
      (A2 `19dd449`, 265 lines), `StitchTranscriptCommand/Handler` (A3). Bodies moved verbatim
      into `handle()` with a prologue aliasing command/handler fields to the legacy parameter
      names; **disclosed deviations**: `_plan`/`_stitch` in the transcribe handler now take
      their sibling handler as a parameter and their call lines construct the sibling command
      (constructor-injection threading — the facade, not a test, wires siblings); A1's handler
      temporarily still imported legacy `usecases.{plan_chunks,stitch_transcript}` (rule-legal
      — `TRANSCRIPTS_APPLICATION` forbids neither), cleared by A2/A3. Module API exports the
      facade (`transcribe_job`, signature byte-identical to the legacy function so
      `runtime/worker.py`'s frozen body never changed — import line only), the handler/command
      pairs `X as X`, and the loop's constants; no router, stated in the module docstring.
      Shape test green: **6 tests** (3 commands frozen, 3 handlers `handle()`).
- [x] 3c.3 GREEN: convert `resume_job` to `systems/pipeline/jobs/application/use_cases/commands/
      resume_job.py`; move the `pending_chunks` derivation to a transcripts-side implementation
      of the jobs-owned interface, bound at composition roots (supervisor + `main.py`);
      `runtime/supervisor.py` import lines rewire to the transcripts module API — **import
      lines only, body frozen** (standing rule; the bulk runtime slice is 4f). `[unit 3c]`
      → RED first: **2 collection errors** (`ImportError: cannot import name 'pending_chunks'
      from ...domain.chunking` on the moved test; `ModuleNotFoundError` on the jobs resume
      command from the seam test). GREEN: `pending_chunks` appended to
      `transcripts/domain/chunking.py` (function docstring verbatim; the module docstring
      extended one clause to name the derivation — otherwise it would lie after the append);
      `usecases/resume_job.py` deleted; transcribe handler's import flipped to its own domain;
      supervisor line 55 rewired to `transcripts_module_api` — line 403's call **untouched**;
      module API re-exports `pending_chunks as pending_chunks`; `jobs/domain/interfaces/
      resume.py` declares `PendingChunks` (Protocol; transcripts domain types re-exported
      `X as X` — `jobs/domain` may name them, and the seam test pins the signature) and
      `jobs/.../commands/resume_job.py` holds the frozen `ResumeJobCommand` +
      `ResumeJobHandler(*, pending_chunks)` delegation, importing **only `jobs.*`** so the
      architecture suite's `JOBS_APPLICATION` rule guards it automatically.
      **`main.py` binding disclosed**: no resume consumer exists (no HTTP resume operation),
      so the supervisor's module-API import *is* the binding — no dead wiring invented.
- [x] 3c.4 Verify: resume/worker-lifecycle tests unchanged in intent and green; suite + mypy.
      Commit `refactor(fca): convert transcripts commands, land resume_job via module API`.
      `[unit 3c]`
      → `test_resume_job.py` moved to `tests/systems/pipeline/transcripts/domain/` — bodies
      byte-identical, sole edit the import flip; 9 assertions unchanged. New
      `test_resume_seam.py` (3 tests): module-API exposure, jobs-owned interface signature,
      command delegation; one assertion corrected during GREEN (`tuple[...] is` → `==`:
      subscription builds a fresh alias, not a test-intent change). Full default suite
      **2182 passed, 44 deselected, 0 skipped** (baseline 2173 + 6 shape + 3 seam = 2182);
      `mypy src tests` clean over **346** files (344 + seam + 2 jobs files − 1 deleted
      legacy); `tests/test_architecture.py` green inside the focused runs. Commits: A1
      `37f8385` (288 lines), A2 `19dd449` (265), A3 `03adb7d` (343), B = this commit
      (273) — each `additions + deletions` ≤ 400, no `size:exception`.

---

## Slice 3d: ASR + extractor adapters to transcripts infrastructure (~400 lines)

Closes: adapter relocation with zero behavior change (capability declaration, `SegmentKind`
marking, device proof, `TrackingUnavailable` paths all ride unchanged); AB rules over the new
infrastructure tree already registered in 3a. Lazy-import discipline (`importorskip`,
factory-on-call) preserved exactly — the 7a-ii collection-defect lesson is not repeated.

- [x] 3d.1 RED-by-move: relocate adapter tests (`tests/unit/adapters/asr/...`,
      extractor tests) to `tests/systems/pipeline/transcripts/infrastructure/...` — import-fail
      RED; **bodies unchanged**; `localmodel`/`paid` markers preserved so the default run still
      collects with no extras installed. `[unit 3d]`
      → Safety net before the move **478 passed, 20 deselected**. Moved
      `tests/unit/adapters/asr/**` (9 test modules + 3 `__init__.py`) to
      `tests/systems/pipeline/transcripts/infrastructure/asr/**`, and the four
      extractor-subject tests (`test_extractor`, `test_slicing`, `test_availability`,
      `test_probe_frame`) to `.../infrastructure/ffmpeg/` **plus a verbatim copy of
      `tests/unit/adapters/ffmpeg/conftest.py`** — `tests/unit/adapters/ffmpeg` still
      needs its autouse `assume_binaries_present` fixture for the tests that stay, so the
      file is duplicated rather than moved (**disclosed**). Import lines re-pointed to
      the not-yet-existing new paths; **RED observed verbatim:
      `360 passed, 7 deselected, 12 errors in 10.64s`** under
      `--continue-on-collection-errors`, all 12 errors
      `ModuleNotFoundError: No module named 'onevoicecut.systems.pipeline.transcripts.
      infrastructure.{asr,ffmpeg}'` (default flags do not tally, they abort — recorded
      too: `Interrupted: 12 errors during collection`). Bodies unchanged but for the two
      disclosed lines in `test_faster_whisper_diarization.py`: `parents[5]` →
      `parents[7]` (+2 directory levels, same intent — the repo root where the gitignored
      `.env` lives) and its comment. Markers preserved: `--collect-only -m localmodel`
      collects **13** in the moved tree, `-m paid` **10** in the cloud contract.
- [x] 3d.2 GREEN: relocate `adapters/asr/{local,cloud}` to
      `systems/pipeline/transcripts/infrastructure/asr/`; `adapters/ffmpeg/extractor.py` to
      `systems/pipeline/transcripts/infrastructure/ffmpeg/`; evaluate domain-type-free ffmpeg
      helpers (`process`, `sendcmd`, `argv`) against the design rule and place them in
      `shared/infrastructure/ffmpeg/` if clean of domain imports, otherwise keep them beside
      the extractor in transcripts (record the placement reason in the commit body).
      `[unit 3d]`
      → `adapters/asr/{local,cloud}` → `.../transcripts/infrastructure/asr/`;
      `extractor.py`, `argv.py`, `sendcmd.py` → `.../transcripts/infrastructure/ffmpeg/`
      (new `__init__.py`); `process.py` → `shared/infrastructure/ffmpeg/`.
      **Placement evaluation (recorded in the commit body):** `process.py` imports stdlib +
      `shared.domain.errors` only — clean, so it lands in `shared/` (AB-08 by construction,
      the same shape as `shared/infrastructure/storage/core.py`); `argv.py` names
      `PlannedChunk` and `sendcmd.py` names `domain.framing.{CropKeyframe,CropTrajectory}`
      — both carry domain vocabulary, so the task's fallback keeps them beside the
      extractor. **Disclosed consequence:** the clips-side `video_render` now imports
      `argv`/`sendcmd` from transcripts infrastructure until 4d builds the clips adapters.
      Rewired 25 importers (src: `main.py`, `adapters/web/app.py`, the three runtime roots,
      `adapters/ffmpeg/video_render.py`, both ASR self-imports, `extractor.py`; tests:
      both contract tests, four runtime-wiring tests, four integration tests,
      `test_cloud_byte_cap`, four adapter-side helper tests) +4 prose references (cloud
      adapter comment, cloud contract docstring, flac comment, `scripts/try_local_asr.py`).
      `tests/test_architecture.py`'s plant strings **deliberately untouched** — they are
      AST-written text that never imports the target; `CLAUDE.md`, the archive and the
      `adapters/{llm,vision}` historical comments left stale on purpose (6b/4d own them).
      Focused run restored to **478 passed, 20 deselected** — the safety-net number.
- [x] 3d.3 GREEN: `runtime/engine_resolver.py` import lines rewire to the new adapter paths —
      import lines only, factory-on-call structure preserved, **body frozen**. `[unit 3d]`
      → Eight ASR import statements in `engine_resolver.py` (lines 79, 109, 178, 182, 210,
      214, 228, 232) re-pointed. `git diff -U0 -- src/onevoicecut/runtime/` is **12 changed
      lines across `app.py`, `engine_resolver.py`, `render_worker.py`, `worker.py` — every
      one a `from ... import` line, zero body diff**; the lazy factory-on-call structure
      (imports inside `local_transcriber`/`cloud_transcriber`/the declaration probes) is
      untouched.
- [x] 3d.4 Verify: suite + mypy; `pytest -m integration` still green when ffmpeg is present;
      no module-level import of optional extras introduced. Commit
      `refactor(fca): move ASR and extractor adapters under transcripts infrastructure`.
      `[unit 3d]`
      → Full default suite **2182 passed, 44 deselected, 2 warnings, 0 skipped** (105 s) —
      identical to the c2d9060 baseline, so the ffmpeg integration tests genuinely ran;
      explicit harness `pytest -m integration` with the WinGet bin dir prepended in the
      same command **53 passed, 2173 deselected, 0 skipped** (19 s);
      `mypy src tests` clean over **350** files (+4: the two new src `__init__.py`, the new
      test `__init__.py` + conftest); `tests/test_architecture.py` **26 passed**. No
      module-level import of an optional extra introduced — the moved modules' import
      blocks differ only by the rewired sibling paths, and every `importorskip` /
      factory-on-call site is untouched (proven by the `localmodel`/`paid` collect counts
      in 3d.1). `.gitignore` trap checked: `git check-ignore` reports **none** of the four
      new files ignored. **Measured: commit `d501ce0` reports 54 files changed, 148
      insertions(+), 66 deletions(-) = 214** — one unit, ≤400, no split needed, no
      `size:exception`. Excluding this file the code delta is 67 + 62 = 129 over 49 paths,
      plus the 18-line conftest copy and three zero-byte `__init__.py` (= **147**); this
      file adds 67/4. (An earlier wording of this note read "230", double-counting the
      conftest already staged inside the 148 — the commit message carries the same
      over-count, deliberately not amended; recorded here instead.)

---

## Slice 4a: clips domain + interfaces + clips guard rules (~250 lines)

Closes: architecture-boundary rule registration **for clips from this slice** (AB-01, AB-02,
AB-04, AB-05, AB-06, AB-07, AB-09, AB-10, AB-11, AB-12 — plant proofs), dual coverage.
Behavior-frozen relocation.

- [x] 4a.1 RED: register the `clips` rule group and plant violations per rule under
      `systems/.../clips/` trees (including AB-07: clips domain importing jobs domain, and
      AB-06: clips application importing jobs infrastructure); each fails naming its file;
      legacy plants still fail while legacy packages exist (AB-11). `[unit 4a]`
      (RED observed: `10 failed, 27 passed in 0.68s` — the registration test, 8 plant
      parametrizations and the AB-11 dual test failed; the ninth plant,
      `ab-07-clips-domain-imports-jobs-domain`, passed because 2a's `jobs-domain-isolation`
      already walks this subtree — disclosed dual coverage, not a second proof. GREEN
      observed: `37 passed in 0.45s`. Full default suite `2193 passed, 44 deselected,
      0 skipped`; mypy clean over 350 source files.)
- [x] 4a.2 GREEN: relocate `domain/{generation,rendering,framing}.py` to
      `systems/pipeline/clips/domain/`; relocate `ports/{text_generation,subject_tracker,
      video_render}.py` to `systems/pipeline/clips/domain/interfaces/`; declare `ClipStore`
      per the OQ3 method table; `PublishPort` is **not** created (does not exist in the tree —
      design footnote); update importers; move tests to `tests/systems/pipeline/clips/`.
      **No new behavior test** — behavior-frozen relocation. `[unit 4a]`
      (`git mv` renames: 6 source + 7 test files; 63 files import-rewired by a single-pass
      alternation, 0 residual old paths; `ClipStore` added at
      `clips/domain/interfaces/clip_store.py` with the OQ3 seven methods and their
      contract docstrings preserved from `ports/transcript_storage.py` before that port is
      deleted. **Disclosures.** (1) Retargeted assertion —
      `tests/unit/usecases/test_generation_scope_boundary.py` filtered
      `name.startswith("onevoicecut.ports")`, which would have matched nothing after the
      move and asserted against an empty set (passing for the wrong reason); the filter now
      also accepts `.domain.interfaces.` and the expected set names the new FQN. (2) Moved
      rather than kept — `tests/unit/usecases/test_text_generation_fake.py` went with the
      interface it exercises, to `.../clips/domain/interfaces/`. (3) `tests/unit/domain/`
      removed: all three members moved and only `__init__.py` was left. (4) Two prose
      docstring re-points, `usecases/generate_artifacts.py` (`domain/generation.py`) and
      `usecases/plan_trajectory.py` (`domain/framing.py`), now name the full new path.
      (5) `FrameSize` verified, no action: `domain/framing.py:40` already imported it from
      `onevoicecut.shared.domain.media`, `src/onevoicecut/domain/media.py` was moved to
      `shared/domain/media.py:31` by `9a98cf4` in 3a, so nothing referenced a path this
      slice removes. (6) `CLIPS_APPLICATION` deliberately does not forbid `transcripts.domain`
      — the frozen generation/subtitle bodies read the transcript, so forbidding it would
      fail 4c; disclosed in the rule's own comment.)
- [x] 4a.3 Verify: suite + mypy; `check_target_profiles` cross-module pin (from 1b) still
      green against relocated `RENDER_PROFILES`. Commit
      `refactor(fca): migrate clips domain and interfaces into systems/pipeline`. `[unit 4a]`
      (Default suite **2193 passed, 44 deselected, 0 skipped, 0 collection errors** —
      byte-identical count to the pre-relocation baseline, which is what behavior-frozen
      means here. mypy **clean over 356 source files** (350 + 7 new − 1 deleted).
      `tests/unit/runtime/test_settings.py` — the 1b `check_target_profiles` pin — green,
      in a 62-test run with the arch suite (`37 passed`) and the scope-boundary test.
      Arch rules verified against the **real** tree, not only plants: `clips-domain` guards
      9 real files with 0 violations; `clips-application`/`clips-presentation` guard 0
      because 4c/4e have not built those trees, as their comments state.
      **Measured diff: 241 insertions + 130 deletions = 371 lines, 73 files — under the
      400 budget**, against the 3a precedent `24d92e5` at 368. Note for review: a tool
      that counts renamed files at full content will report ~2,495 instead, because 13
      files moved; that is a rename artifact, not authored lines.)

---

## Slice 4b: `ClipStore` facade (~400 lines)

Closes: OQ3 clips half — all three narrow Protocols now exist; monolith still consumed by
`runtime/` until 4f (deletion deferred, "nothing deleted until its replacement is green").

- [x] 4b.1 RED-by-move: relocate artifacts/exports/render-claim storage tests to
      `tests/systems/pipeline/clips/infrastructure/storage/` — import-fail RED; rename-commit
      and claim-staleness assertions unchanged; structural assertion facade satisfies
      `ClipStore`. `[unit 4b]`
      — moved `test_filesystem_job_artifacts.py` → `test_artifacts.py` (3 tests, whole file;
      fixture retargeted from `FilesystemTranscriptStorage(tmp_path)` to the `core → jobs →
      storage` chain a composition root builds, `decode_artifacts` re-imported from
      `clip_store` rather than `adapters.storage.serialization`; assertion bodies verbatim).
      Split `test_filesystem_transcript_storage.py`: 10 tests → `test_clip_store.py` (8 in
      `TestClipExportsOnDisk`, the shared contract body, the no-network structural check) plus
      the **new** structural assertion `test_the_facade_satisfies_the_clip_store` = 14 tests in
      the new tree; disclosed as `A` new + `M` old, never as a rename. `test_the_adapter_
      satisfies_the_port` stays adapter-side (`M`, still binding `TranscriptStoragePort` for
      `runtime/` until 4f). Fixture retargets, all disclosed: the 8 `storage.create_job(...)`
      arrange lines are deleted (the precondition now lives in the `jobs` fixture every writing
      test requests), and `..._never_created_is_refused` builds
      `FilesystemClipStore(StorageCore(tmp_path))` over an uncreated job — assertions
      byte-identical; the no-network check retargets its subject from
      `filesystem_transcript_storage` to `clip_store`, the module that now owns `_export_path`
      (body unchanged). **No render-claim storage test exists to move**: `write_render_claim` /
      `render_claim_is_fresh` are asserted only through the fake in `test_render_drain_once.py`
      and `test_render_worker.py`, which stay put and are green in the RED run; likewise there
      is no clip-export rename-commit assertion anywhere — commit-by-rename for exports rests on
      `core.write_atomic`, pinned in `tests/shared/infrastructure/storage/test_atomic_write.py`.
      RED observed **309 passed, 2 errors** (focused: clips tree + `unit/adapters/storage` +
      `test_render_drain_once.py`), both errors the same
      `ModuleNotFoundError: No module named 'onevoicecut.systems.pipeline.clips.infrastructure'`,
      14 tests uncollectable.
- [x] 4b.2 GREEN: create `systems/pipeline/clips/infrastructure/storage/clip_store.py` over
      `core`; composition roots build all three facades from one core. `[unit 4b]`
      — `FilesystemClipStore(core)` with the 7 `ClipStore` methods + `job_dir` (the moved
      tests locate `artifacts.json` and `render/` through it, so `writable` needs no private
      access), bodies verbatim from the monolith, plus `_export_path`.
      **Codecs moved in** — `encode/decode_artifacts`, `encode/decode_clip_export` and their
      `_job_id`/`_clip_id`/`_rendered_clip` helpers — because a codec taking `ClipExport` can
      live neither in the domain-agnostic core (AB-08) nor in the adapter this facade starts
      replacing; `serialization.py` re-exports them `X as X`, and since its other two halves
      left in 2b and 3b it now defines **no codec at all**, so its docstring was rewritten to
      say exactly that rather than describing codecs it no longer holds.
      **Composition roots: honestly, none builds a facade today.** `StorageCore(data_dir)`
      is constructed in `src` at exactly one place, `filesystem_transcript_storage.py:77`,
      and `runtime/` keeps constructing the monolith until 4f — the same state 2b and 3b
      recorded. The one-core-three-facades construction is therefore proved where it exists,
      the `core → jobs → storage` fixture chain in the two moved test files (the
      `test_transcript_store.py` pattern), rather than wired into a root that would build
      facades nothing consumes yet. Wiring belongs to 4f and was not invented here.
- [x] 4b.3 Verify: suite + mypy (render-drain liveness tests green — behavior frozen). Commit
      `refactor(fca): add clips ClipStore facade over storage core`. `[unit 4b]`
      — GREEN focused **406 passed** (clips tree + `unit/adapters/storage` +
      `test_render_drain_once.py` + `test_render_worker.py` + the arch suite, 0 errors, so
      the claim-staleness and rename-free liveness assertions are green unchanged). Full
      default suite **2194 passed, 44 deselected, 0 skipped** — 2193 baseline + 1, the
      structural conformance test; zero skips means the ffmpeg surface was actually
      exercised. `mypy src tests` clean over **362** files (+6 = 7 new, minus the deleted
      `test_filesystem_job_artifacts.py`). Arch suite green: `clips/infrastructure` is
      guarded by no rule group (the 3d deviation, unchanged), and `adapters → systems`
      imports are already established by the 2b/3b re-export seams.
      Diff for this unit: **707 insertions / 390 deletions = 1,097 lines, 10
      files** — over the 800 budget and the 400 default, but it cannot shrink further: a
      split at the seam would commit the RED-by-move tests green-alone, which is the one
      thing the slice's RED *is* (the argument `3b.3` recorded), and tasks.md names a single
      commit for the slice. `size:exception` is recommended rather than a silent split; for
      scale, `3b` measured 846 and `2b` 957 on the same kind of move. Note for review: the
      artifacts file is a genuine rename (`{unit/adapters => systems/...}/storage/
      test_artifacts.py`, git 45/19), while `test_clip_store.py` (+266) is the new half of a
      split and cannot be one — the old file survives with its port test.

---

## Slice 4c: clips use cases — CQRS conversion (~400 lines)

Closes: behavior preservation for generation/render/export/purge commands and the three pure
derivation queries (`render_profiles`, `plan_trajectory`, `build_subtitle_cues` — reads, per
the CQRS classification note).

- [x] 4c.1 RED: handler-shape tests (moved assertions, import-fail RED) for
      `GenerateArtifactsHandler`, `RequestClipExportHandler`, `RenderClipHandler`,
      `PurgeJobArtifactsHandler` commands and `RenderProfilesQuery`, `PlanTrajectoryQuery`,
      `BuildSubtitleCuesQuery` queries under
      `systems/pipeline/clips/application/use_cases/{commands,queries}/`. `[unit 4c]`
- [x] 4c.2 GREEN: convert the seven use cases — wrap-and-rename, bodies verbatim; callers
      (still-drained-yet clip routes in `adapters/web`, render worker wiring prepared for 4f)
      rewired through temporary composition wiring until 4e's `clips_module_api`. `[unit 4c]`
- [x] 4c.3 Verify: suite + mypy; commit
      `refactor(fca): convert clips use cases to Command/Handler files`. `[unit 4c]`

  Landed as six green-alone units, not the single commit named above: the combined diff
  measured 1,388 native lines against the 400-line review budget, so it was split at each
  use case's own seam — `c925715` (generate_artifacts), `71b32f3` (build_subtitle_cues),
  `7681c52` (plan_trajectory), `ff549a1` (render_profiles), `f54dca1`
  (request_clip_export + purge), `3fffbaa` (render_clip + the commands shape test).
  `ff549a1`'s message claims "Closes 4c.2" while only the three queries had landed;
  `3fffbaa` is the commit that actually closes it. Verified at `3fffbaa`: the named
  command selects 322 passed, the full run 2209 passed / 44 deselected / **0 skipped**,
  mypy clean over 370 source files, and no reference to any of the seven converted
  modules survives under `src/` or `tests/`.

---

## Slice 4d: clips adapters to infrastructure (~350 lines)

Closes: adapter relocation with zero behavior change; AB rules over the new tree already live
(4a).

- [x] 4d.1 RED-by-move: relocate vision/llm/video-render/subtitles adapter tests to
      `tests/systems/pipeline/clips/infrastructure/...` — import-fail RED; bodies unchanged;
      capability-probe (`REQUIRES_SETUP`) tests unchanged; markers preserved. `[unit 4d]`
- [x] 4d.2 GREEN: relocate `adapters/llm/` to `systems/pipeline/clips/infrastructure/llm/`;
      `adapters/vision/` and video-render + subtitles pieces of `adapters/ffmpeg/` to
      `systems/pipeline/clips/infrastructure/`; shared ffmpeg helpers (if 3d placed any in
      `shared/infrastructure/ffmpeg/`) imported from there. `[unit 4d]`
- [x] 4d.3 GREEN: `runtime/tracker_resolver.py` and any render-side resolver import lines
      rewire — import lines only, **body frozen**. `[unit 4d]`
- [x] 4d.4 Verify: suite + mypy; commit
      `refactor(fca): move vision, llm, and render adapters under clips infrastructure`.
      `[unit 4d]`

  Landed as three green-alone commits, one per adapter seam, not the single commit named
  above: `dd2632b` (vision, 35 lines), `7bf694c` (llm, 21), `0d7330c` (video-render +
  subtitles, 36) — 92 native lines against the 400-line budget, so the split bought seam
  isolation rather than a smaller diff. Every RED was observed **per seam** (the three moves
  were never in the tree together), not as one combined run; per-seam tallies, each a
  `ModuleNotFoundError` naming the new package, with default flags aborting before they can
  tally:
  * vision **2 errors** — `...infrastructure.vision`; `--continue-on-collection-errors`
    also reports exactly 2 errors, so nothing in that file collected.
  * llm **3 errors** — `...infrastructure.llm`.
  * video-render + subtitles **2 errors** — `...infrastructure.ffmpeg`; over the full in-play
    scope, 874 passed / 31 deselected / 2 errors (the only run all three seams' tests were
    in, since each seam went GREEN before the next began). The partial shape is by design:
    the tests whose subject still resolved through the legacy `adapters.ffmpeg` package
    stayed green (82 passed) until the source moved.
  GREEN, seam by seam: vision 63 passed / 11 deselected, then safety net 922 / 31;
  llm 41 / 2 (llm tree + `worker` generation wiring), then 1266 / 31 over
  clips+adapters+contract+runtime; ffmpeg 54 in the new dir, 82 in the surviving argv/sendcmd
  dir, 28 across both `render_clip` consumers, then 1318 / 31 over
  clips+adapters+contract+runtime+integration. Final: full default run **2209 passed /
  44 deselected / 0 skipped** — identical to the pre-slice baseline; `mypy src tests` clean
  over **372** source files; `tests/test_architecture.py` 37 passed; markers unchanged
  before and after (**localmodel 34/2253, paid 10/2253**); zero references to
  `adapters.{llm,vision,ffmpeg}` survive outside `tests/test_architecture.py`'s
  AST-written plant strings, which were deliberately not touched.

  Home decisions for the ffmpeg pieces, each disclosed as the task asked (decide by what the
  module's imports name): `subtitles.py` → `shared.domain.errors` +
  `clips.domain.rendering.{RenderProfile,SubtitleCue}` → clips infrastructure;
  `video_render.py` → clips framing/capabilities/interfaces → clips infrastructure, keeping
  its `argv`/`sendcmd` imports from `systems/pipeline/transcripts/infrastructure/ffmpeg/`
  (the cross-module edge 3d established; no rule yet guards `systems/*/infrastructure`).
  `adapters/ffmpeg/` is now empty and was deleted as drained. `tests/unit/adapters/ffmpeg/`
  keeps `test_argv_composition`, `test_render_argv` and `test_sendcmd` — 3d's "until 4d"
  note resolves to those being transcripts-owned helpers, not this seam's subject.

  Disclosed deviations from "bodies unchanged": `test_torchvision_tracker.py` needed three
  non-import edits forced by the move — `parents[4]` → `parents[6]`, `VISION_DIR` retargeted
  to the new `src` path, and one prose line saying `adapters/vision` now saying "the vision
  package". No assertion changed. The clips-side ffmpeg test dir also carries a
  byte-identical copy of `tests/unit/adapters/ffmpeg/conftest.py`
  (`assume_binaries_present`) plus a zero-byte `__init__.py`, mirroring what 3d did on the
  transcripts side; the copied docstring still names `test_availability.py`, which lives in
  the *other* dir and has no counterpart here. `runtime/{tracker_resolver,worker,app,
  render_worker}.py` changed on **import lines only**, bodies frozen.

  One failure observed and then ruled out of scope: running
  `tests/unit/runtime/test_env_file_loading.py` immediately before
  `tests/integration/test_worker_entrypoint.py` makes
  `test_a_build_with_no_engine_configured_says_so` return `EXIT_FAILED` instead of
  `EXIT_UNUSABLE`, because the real resolver then loads model `small` and this machine has
  no `cublas64_12.dll`. Pre-existing — reproduced with `adfb024`'s `worker.py` substituted,
  and `worker.py` is the only slice-4d code either of those two files executes. The full
  suite is immune because it collects `tests/integration` before `tests/unit`; only the
  ad-hoc subset inverted that order.


---

## Slice 4e: clips presentation + `clips_module_api` + `adapters/web` drained (~400 lines)

Closes: clip operations under module presentation (paths still `/api` until 5a); AUTH-06/
owner-only 403 generated checks green; AB-09 (presentation constructs nothing).

- [x] 4e.1 RED-by-move: relocate the three clip route/schema tests to
      `tests/systems/pipeline/clips/presentation/` — import-fail RED; bodies unchanged
      (202 admission, profile-scoped 404 vs known-clip 404, per-profile GET). `[unit 4e]`
- [x] 4e.2 GREEN: create `systems/pipeline/clips/presentation/{routes,controllers}/v1/` +
      clip schemas; `clips_module_api.py`; `main.py` registers the clips router (relative
      paths, same `/api/jobs/{id}/clips...` shape as today). `[unit 4e]`
- [x] 4e.3 GREEN: drain the last content out of `adapters/web/` — package left **empty but
      present**; deletion is Phase 5 (design: package deleted at Phase 5). Verify no live
      module imports it. `[unit 4e]`
- [x] 4e.4 Verify: suite + mypy; all eight operations served through module presentation at
      the unversioned prefix; commit
      `refactor(fca): move clip HTTP surface into module presentation, drain adapters/web`.
      `[unit 4e]`

  Landed as the single commit named above, `3313264`, **935 changed lines across 45 files**
  against a 400-line policy and `review.budget_lines: 800` — **`size:exception` human-approved
  2026-09-29**. Nothing minified: 287 of
  the lines are the drained adapter's own deletions, and the three new presentation modules
  (controller 165, routes 106, module API 60) are the authored half of the same seam. A split
  at 4e.2/4e.3 would have produced ~490 and ~445 — each still over policy — so the split would
  have bought two smaller exceptions rather than none.

  * **4e.1 RED-by-move** — the "three clip route/schema tests" named above are the three
    clip *operations*, not three files: `test_clip_routes.py` and
    `test_clip_route_authorization_parity.py` are the whole set (grep of the tree disproved a
    third). Both `git mv`'d with a new `presentation/__init__.py`; import re-points only
    (`WebDependencies` → `onevoicecut.main`, clip schemas → `...clips.presentation.schemas.v1.clip_schemas`).
    Verbatim tally **18 passed, 1 error** — `ModuleNotFoundError: No module named
    'onevoicecut.systems.pipeline.clips.presentation'`. Honest caveat: `test_clip_routes.py`
    alone passed, because `main.py` already re-exported `WebDependencies`; only the parity
    test bit.
  * **4e.2 GREEN** — `clip_schemas.py` is `adapters/web/schemas.py` renamed (91% similar,
    docstring adapted); `clip_controller.py` preserves the route's order exactly (validate →
    load → `require_owner` → state 409), reuses `validated_job_id` from the jobs controller
    rather than restating it, and deliberately does **not** catch `JobNotOwned` — `main`'s
    table maps it to the same 403, so there is one spelling of that refusal.
    `clips_module_api.py` sits at the module root, not under `presentation/`, because it
    constructs a handler (AB-09).
  * **4e.3 GREEN** — `WebDependencies` + `MediaSourceFactory` + `ExtractorFactory` +
    `filesystem_media_source` + `ffmpeg_extractor` moved into `main.py` (a composition root
    may construct adapters; `shared/` may not, AB-08, and `runtime/` is the wrong direction
    per design.md:144). The old `Authenticator` alias was a duplicate of
    `shared/application/principal.py` and was dropped rather than moved. `adapters/web/`
    retains only `__init__.py`, with a why-drained docstring. 25 test files re-pointed;
    adjacent duplicate `from onevoicecut.main import` lines merged.
  * **4e.4 verified** — full default run **2211 passed, 44 deselected, 0 skipped**; `mypy src
    tests` clean over **380 source files**; `tests/test_architecture.py` green. No live module
    imports `onevoicecut.adapters.web` (only its own `__init__.py`, docstrings, and the
    deliberate `web_package` structural walk).

  **Disclosed deviations**:
  1. Both module APIs take `WebDependencies` under `from __future__ import annotations` +
     `if TYPE_CHECKING:` — mypy forced the sequencing (`attr-defined` under
     `no_implicit_reexport` while the class still lived in `adapters.web.app`), and it keeps
     the module→root edge out of the runtime graph; the real edge is `main` calling the
     module APIs, at call time.
  2. **AB-09 plants**: the two existing plants are byte-unchanged (the 4d precedent for a
     rule outliving the module it named) and **two new live plants** were added, one per
     module, for `from onevoicecut.main import WebDependencies`; `"onevoicecut.main"` joined
     both `JOBS_PRESENTATION` and `CLIPS_PRESENTATION` `forbidden_prefixes`. A bulk
     re-point script hit the two plant *source strings* first and was reverted — recorded
     here because otherwise the diff would look like a frozen plant was edited.
  3. `test_upload_media_route.py`'s multipart walk gains a third root
     (`clips/presentation`), the same structural-scan class disclosed as 2e's deviation 3;
     its `import onevoicecut.adapters.web as web_package` stays on purpose, to keep walking
     the drained package.
  4. `runtime/` untouched (4f). Paths remain unversioned `/api/...` (5a). `adapters/web/`
     package itself not deleted (5c.4).

---

## Slice 4f: `runtime/` import rewiring — bodies frozen (~200 lines) — HIGHEST-RISK UNIT

Closes: design Deviation 3 (parallel composition roots import module wiring, never the
reverse); no new spec scenario — this is the behavior-freeze slice. **Explicit review note:**
a "pure import change" in `worker.py`/`render_worker.py` is where a behavior freeze is easiest
to break accidentally. The reviewer must read this diff hardest: reject the slice if any line
outside an `import`/module-header changes in `runtime/worker.py`, `runtime/render_worker.py`,
`runtime/supervisor.py`, `runtime/engine_resolver.py`, `runtime/tracker_resolver.py`, or
`runtime/app.py`.

- [ ] 4f.1 Pre-check: capture the expected diff surface — the six runtime modules plus their
      test files' import lines if any; record `git diff --stat` intent in the commit message.
      No test bodies may change in `tests/unit/runtime/`. `[unit 4f]`
- [ ] 4f.2 Rewire `runtime/worker.py` — import lines only: construct `core` + `JobStore` +
      `TranscriptStore` facades at the entrypoint (its own composition root), consume
      jobs/transcripts module APIs for commands (`transcribe_job`, `resume_job` path,
      heartbeat/cancellation through `JobStore`). Bodies (single-writer, heartbeat cadence,
      chunk loop, timeout handling) frozen byte-for-byte. `[unit 4f]`
- [ ] 4f.3 Rewire `runtime/render_worker.py` — import lines only: `ClipStore` facade +
      clips module API (`request_clip_export`/`render_clip` claim path). Bodies (one-shot
      claim, `render_timeout_for` import usage, cap counting) frozen. `[unit 4f]`
- [ ] 4f.4 Rewire `runtime/supervisor.py`, `engine_resolver.py`, `tracker_resolver.py`,
      `app.py` import lines to the new homes (loops, drain cadences, watchdog, reconcile,
      reap classification all frozen; `app.py` keeps only supervisor-loop symbols — `get_app`
      re-export still stands until 5a). `[unit 4f]`
- [ ] 4f.5 GREEN/verify (relocation honesty — **no new test written**): full default suite
      green **with runtime test bodies unchanged**; mypy strict clean; explicit body-freeze
      evidence: `git diff` over the six runtime files contains only import-line changes —
      recorded in the review receipt. Delete the drained `adapters/storage/` monolith and any
      now-empty legacy adapter packages whose consumers all moved (deletion only — nothing
      else in `adapters/`). Commit
      `refactor(fca): rewire runtime composition roots to module APIs (bodies frozen)`.
      `[unit 4f]`

---

## Slice 5a: Atomic `/api` to `/api/v1` migration (~600 lines) — EXCEEDS 400; unsplittable by mandate

Closes: `api-versioning` AV-01, AV-02, AV-03, AV-04, AV-08; `operator-authentication` AUTH-02
enumeration now true of the versioned paths; `job-cancellation` CXL-01 route path is
`POST /api/v1/jobs/{id}/cancel`. The one externally visible break — route registration, every
test path literal (~100), and documented examples change **together** in one commit; the slice
is not done until the default suite is green with no unversioned path in shipped code, tests, or
documentation. Route-table-generated 401/403 tests follow `app.routes` automatically — verify,
never hand-edit their path lists.

- [ ] 5a.1 RED: AV-02 — enumerate `app.routes`: every path begins `/api/v1/`, none under bare
      `/api`. Fails pre-migration. `[unit 5a]`
- [ ] 5a.2 RED: AV-03 — unauthenticated `POST /api/jobs` (and spot-checks of former
      unversioned paths) responds 404 with **no side effects**: no job admitted, no file
      written, no process spawned. Fails pre-migration (currently 201/401). `[unit 5a]`
- [ ] 5a.3 RED: AV-04 — version prefixes in the route table equal the active versions in
      `fca_config.yaml` (both `{v1}`). Fails pre-migration (routes unversioned). `[unit 5a]`
- [ ] 5a.4 RED: AV-08 — plant a route registered without authentication handling on the app;
      the generated check (derived from `app.routes`, not a literal list) fails the default run
      naming it. Proves AUTH-06's machinery survives the migration by construction. `[unit 5a]`
- [ ] 5a.5 GREEN (atomic, one commit): flip router registration in `main.py` to prefix
      `/api/v1/jobs`; flip every `/api/` path literal across `tests/` (~100 occurrences) and
      every documented example in `README.md` / `CLAUDE.md` HTTP snippets; drop the
      `runtime/app.py:get_app` re-export and flip the documented uvicorn entrypoint to
      `onevoicecut.main:get_app` (design: re-export and docs die in the path-migration slice);
      generated auth tests follow automatically (verify only). AV-01 proven by the unchanged
      per-operation status-code tests running against the new literals. `[unit 5a]`
- [ ] 5a.6 Verify: default suite green with no unversioned path remaining in shipped code,
      tests, or documentation (grep the tree for `"/api/` not followed by `v1`); mypy clean;
      commit `feat(api)!: serve all operations under /api/v1 atomically (no unversioned alias)`.
      **Budget note: ~600 estimated lines vs the 400 PR policy — flagged for the ask-on-risk
      decision; after one honest slicing pass this unit cannot split (atomicity is the
      requirement), so it carries a `size:exception` recommendation.** `[unit 5a]`

---

## Slice 5b: Bounded pagination + allow-list listing responses (~350 lines)

Closes: `job-visibility` VIS-09, VIS-10, VIS-11, VIS-12, VIS-13, VIS-14, and the restated
page-union forms of VIS-03, VIS-04, VIS-05. Bounds per design decision (settled): `limit`
default 20, `ge=1, le=100`; `offset` default 0, `ge=0, le=10_000`; pagination applies **after**
the server-side `mine` filter.

- [ ] 5b.1 RED: VIS-10 — authenticated `GET /api/v1/jobs?limit=101` responds 422 and no
      listing is computed. `[unit 5b]`
- [ ] 5b.2 RED: VIS-11 — non-integer `limit`, `limit=0`, negative `limit`, negative or
      non-integer `offset`, and `offset` above 10000 each respond 422 with no listing work.
      `[unit 5b]`
- [ ] 5b.3 RED: VIS-09 — 5 jobs on disk, `limit=2&offset=0` returns at most 2 items with
      owner attribution identical to an unpaginated listing; **omitted `limit` returns a
      bounded default page of 20** (design decision — never the full listing). `[unit 5b]`
- [ ] 5b.4 RED: VIS-12 — `mine` filter composes with pagination: with 3 jobs of "a" and
      others of "b", `mine=true&limit=2` pages contain only "a"'s jobs and the union equals
      exactly "a"'s jobs; filter-then-slice order pinned (handler slices the already-filtered
      tuple; the unscoped listing underneath is never re-scoped). `[unit 5b]`
- [ ] 5b.5 RED: VIS-13/VIS-14 — every JSON key in the wrapper and items is declared on the
      response schema; a planted undeclared field fails the default run naming it.
      `[unit 5b]`
- [ ] 5b.6 GREEN: bind route query parameters (`limit: Query(20, ge=1, le=100)`,
      `offset: Query(0, ge=0, le=10_000)`, existing `mine`); extend `ListJobsQuery` with
      limit/offset slicing after the mine filter; make `JobListResponse` an explicit allow-list
      (no `total`/echo fields). Update the restated VIS-03/04/05 tests to page through and
      assert completeness across the union. `[unit 5b]`
- [ ] 5b.7 Verify: suite + mypy; commit
      `feat(jobs): bounded pagination and allow-list listing on GET /api/v1/jobs`. `[unit 5b]`

---

## Slice 5c: `extra="forbid"` gate + `adapters/web` deletion (~250 lines)

Closes: `api-versioning` AV-06, AV-07. Contract tightening landed explicitly, never smuggled
into a relocation slice.

- [ ] 5c.1 RED: AV-06 — authenticated `POST /api/v1/jobs` whose JSON body carries an unknown
      key responds 422 before any handler runs; no job created. `[unit 5c]`
- [ ] 5c.2 RED: AV-07 — authenticated `POST /api/v1/jobs/{id}/clips` with an unknown JSON key
      responds 422; no clip export written. `[unit 5c]`
- [ ] 5c.3 GREEN: declare `extra="forbid"` on every JSON request body schema in both modules'
      `presentation/schemas/v1/` (the raw-body media upload carries no JSON schema — out of
      scope); update any existing test that posted extra keys so the suite stays green.
      `[unit 5c]`
- [ ] 5c.4 GREEN: delete the drained `src/onevoicecut/adapters/web/` package; confirm no
      shipped module imports it (AST/grep + suite). `[unit 5c]`
- [ ] 5c.5 Verify: suite + mypy; route-table 401/403 checks green on the final surface; commit
      `feat(api)!: forbid unknown JSON keys and remove drained adapters/web`. `[unit 5c]`

---

## Slice 6a: `test_architecture.py` final form — AB-01 through AB-12 all live (~300 lines)

Closes: `architecture-boundary` complete — every scenario AB-01…AB-12 enforced in the default
run; AB-12 final planted-violation sweep; AB-11's end-state clause (no present code
unenforced).

- [ ] 6a.1 Verify: legacy `domain/`, `usecases/`, `ports/`, and `adapters/` no longer exist
      (only `shared/`, `systems/`, `runtime/`, `main.py` remain) — the precondition for
      retiring legacy rules **without** reducing coverage of any existing code (AB-11).
      `[unit 6a]`
- [ ] 6a.2 RED: full AB-12 sweep — plant one violation per rule group across the migrated
      tree (AB-01 presentation-to-infrastructure, AB-02 application-to-presentation,
      AB-03/AB-04 domain layering and framework imports, AB-05 adapters/runtime imports,
      AB-06 cross-module infrastructure, AB-07 cross-module domain, AB-08 shared-imports-
      systems, AB-09 presentation adapter construction, AB-10 application-imports-runtime);
      each plant fails naming its file; removing all plants returns the run to green.
      `[unit 6a]`
- [ ] 6a.3 GREEN: finalize `tests/test_architecture.py` — all twelve AB rules live against
      the shipped tree; legacy hexagonal rules retired only now that their packages are gone;
      composition-root allow-list = `main.py`, `*_module_api.py`, `runtime/`; wiring rule
      (AB-09/AB-10) explicit. `[unit 6a]`
- [ ] 6a.4 Verify: suite + mypy; commit
      `test(architecture): enforce full FCA rule set (AB-01 through AB-12)`. `[unit 6a]`

---

## Slice 6b: Documentation closure (~400 lines)

Closes: proposal success criteria — CLAUDE.md + `openspec/config.yaml` context describe the new
layout and HTTP surface; no stale hexagonal claims remain; `fca_config.yaml` matches the
shipped tree.

- [ ] 6b.1 Rewrite the CLAUDE.md architecture sections: FCA tree (`shared/`, `systems/pipeline/
      {jobs,transcripts,clips}/`, four layers, `main.py` composition root, parallel `runtime/`
      roots), the seven-ports table restated as per-module `domain/interfaces` Protocols with
      the OQ3 split, module map, entrypoint `onevoicecut.main:get_app`, `/api/v1` HTTP surface
      with pagination bounds, SecretStr note, guard description (AB rules), and updated load-
      bearing-decision file paths. Keep every binding non-goal and non-reversible decision
      intact. Verification: grep CLAUDE.md for stale paths (`usecases/`, `ports/`,
      `adapters/web`, unversioned `/api/` examples) — only historical/archive references may
      remain. `[unit 6b]`
- [ ] 6b.2 Update the `openspec/config.yaml` `context` block: architecture description (FCA,
      module-aware AST guard, `main.py` entrypoint), style notes pointing at shared domain
      errors path; keep test commands, markers, and domain constraints unchanged. `[unit 6b]`
- [ ] 6b.3 Verify README end-to-end: entrypoint command, `/api/v1` examples, and env-var
      notes consistent with the shipped tree (path literals already flipped in 5a — this task
      checks prose around them). `[unit 6b]`
- [ ] 6b.4 Cross-check `fca_config.yaml` against the shipped tree (modules, versions, security,
      tooling) — AV-04 test already enforces the registry/route equality; this is the human
      read for fields the test does not cover. `[unit 6b]`
- [ ] 6b.5 Verify: suite + mypy (config context is tooling-read); commit
      `docs: rewrite architecture, HTTP surface, and config context for FCA layout`.
      `[unit 6b]`

---

## Slice 6c: Closure verification + archive preparation (~100 lines)

Closes: proposal success-criteria sweep; prepares `sdd-archive` (delta specs are already
written — five capabilities updated in this change — promotion happens at archive time).

- [ ] 6c.1 Run and record every proposal success criterion: default suite green (no paid/local
      default-run invocations), mypy strict clean, architecture guard fails on planted
      violation (6a.2 evidence), all eight operations on `/api/v1` with auth semantics
      byte-equivalent, pagination bounds proven, `extra="forbid"` proven, runtime bodies
      unchanged except imports (4f receipt + chain-wide `git diff` over `runtime/`), load-bearing
      decisions untouched, `fca_config.yaml` matches tree, docs current. `[unit 6c]`
- [ ] 6c.2 Archive-prep notes for `sdd-archive`: confirm the five delta specs in
      `specs/*/spec.md` reflect the shipped state (AV/AUTH/VIS/CXL/AB as implemented), list any
      drift found; record that OQ1 (archive order vs `video-transcription-pipeline`) remains an
      orchestrator sequencing choice, not a code dependency. `[unit 6c]`
- [ ] 6c.3 Verify + commit (notes/config only, no product code): `chore(fca): closure
      verification and archive preparation`. `[unit 6c]`

---

## Open questions

None. Every input conflict was resolved from the design without invention:

- `job-visibility`'s two open questions (default page size; offset bound) are closed by the
  design decision (default `limit=20`, `offset` `le=10_000`) — reflected in slice 5b, not
  re-opened.
- `resume_job` landing order: the design footnote (pending-chunk derivation belongs to
  transcripts; supervisor reaches it through the transcripts module API) places its conversion
  in Phase 3 (slice 3c) rather than the module map's Phase 2 listing — destination unchanged
  (jobs command), seam declared jobs-owned to satisfy AB-06/07. A sequencing refinement, not a
  reopened decision.
- OQ1 (archive order) and OQ2 (incremental guard) are process notes already resolved elsewhere
  (OQ2 accepted in the architecture-boundary spec purpose).

## Review Workload Forecast

Changed-line accounting is `additions + deletions`, tests included, using the repo's measured
composition ratio (tests ~56% / src ~36% / config ~8%). Git rename detection softens pure
relocations; handler wrapping, presentation decomposition, the ~100 literal flips, new RED
tests, and rewritten docs dominate. Units are sized at seams (each half green alone), not at
line counts; the historic x4 overrun multiplier is history, not a planning rule — but the
measured precedent that first-time structural units overrun (slice 1: 3.35x) informs the
uncertainty band below.

| Field | Value |
|-------|-------|
| Total estimated changed lines | **~8,700** (range 7,500–10,000; tests ~56% ≈ 4,870 / src ~36% ≈ 3,130 / config ~8% ≈ 700) |
| Units / slices | 28 work units, PR 1 → PR 28, stacked-to-main |
| Units estimated over the 400-line PR policy | **2** — 5a (~600, atomic, unsplittable → `size:exception` recommendation) and 2e (~450, split at delivery if measured over) |
| Units at the 800-line config budget | None over; nearest are 5a (~600) and the 400–450 cluster (2e, 3c, 3d, 4b, 4c, 4e, 6b) |
| Per-unit 800-line budget risk | Low |
| Aggregate 400-line budget risk | **High** — by construction: this change is ~22x the per-PR policy and must ship as a long stacked chain |
| Chained PRs recommended | Yes |
| Delivery strategy | ask-on-risk (session) — orchestrator stops to ask before apply |
| Chain strategy | stacked-to-main (repo `openspec/config.yaml`) |
| Decision needed before apply | **Yes** — forecast exceeds 400 lines and risk is High |

### Per-slice estimates

| Slice | Est. lines | Slice | Est. lines |
|-------|-----------:|-------|-----------:|
| 0a baseline + fca_config | ~80 | 3d adapters to transcripts | ~400 |
| 0b guard first landing | ~150 | 4a clips domain + guard | ~250 |
| 1a shared/domain + guard | ~300 | 4b ClipStore facade | ~400 |
| 1b settings + SecretStr | ~350 | 4c clips CQRS | ~400 |
| 1c principal/security | ~300 | 4d clips adapters | ~350 |
| 1d main.py + error map | ~350 | 4e clips presentation | ~400 |
| 2a jobs domain + guard | ~250 | 4f runtime rewire (frozen) | ~200 |
| 2b-i storage core | ~230 | 5a atomic /api/v1 | **~600** |
| 2b-ii JobStore facade | ~220 | 5b pagination + allow-list | ~350 |
| 2c jobs CQRS commands | ~350 | 5c forbid + delete adapters/web | ~250 |
| 2d GetJob/ListJobs | ~300 | 6a architecture final | ~300 |
| 2e jobs presentation | ~450 | 6b docs closure | ~400 |
| 3a transcripts domain + guard | ~250 | 6c closure + archive prep | ~100 |
| 3b TranscriptStore facade | ~350 | | |
| 3c transcripts CQRS + resume | ~400 | **Total** | **~8,700** |

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|----------------------|-----------------|-------------------|
| 0a | Green baseline + `fca_config.yaml` (AV-05) | PR 1 | `.venv\Scripts\python.exe -m pytest tests/test_fca_config.py -m "not paid and not localmodel"` | N/A — config file + parser test only | `fca_config.yaml`, `tests/test_fca_config.py` |
| 0b | Incremental guard rule-registry first landing (AB-11/AB-12) | PR 2 | `.venv\Scripts\python.exe -m pytest tests/test_architecture.py -m "not paid and not localmodel"` | N/A — static AST walker, no runtime surface | `tests/test_architecture.py` only (revert restores hexagonal guard) |
| 1a | Kernel vocabulary to `shared/domain` + shared guard rules (AB-03/08) | PR 3 | `.venv\Scripts\python.exe -m pytest tests/shared tests/test_architecture.py -m "not paid and not localmodel"` | N/A — pure relocation | `shared/domain/{errors,ids,capabilities}.py` + import-line edits; revert restores old paths |
| 1b | Settings split + `SecretStr` (AUTH-16/17/18) | PR 4 | `.venv\Scripts\python.exe -m pytest tests/shared/infrastructure -m "not paid and not localmodel"` | N/A — env parsing only | `shared/infrastructure/settings.py`, preflight extraction, composition-root `get_secret_value` call sites |
| 1c | `Principal` + `CurrentPrincipal` (AUTH-10/11, AUTH-02–06 preserved) | PR 5 | `.venv\Scripts\python.exe -m pytest tests -k "auth or principal" -m "not paid and not localmodel"` | In-suite HTTP client against `create_app` (401 paths) | `shared/{application/principal,presentation/security}.py` + route dependency wiring |
| 1d | `main.py` composition root + central `DomainError` handler | PR 6 | `.venv\Scripts\python.exe -m pytest tests/integration tests -k "error or create_app or app" -m "not paid and not localmodel"` | In-suite HTTP client: status-code matrix on `main.get_app` | `src/onevoicecut/main.py`, handler installation, `runtime/app.py` re-export line |
| 2a | jobs domain + interfaces + jobs guard rules | PR 7 | `.venv\Scripts\python.exe -m pytest tests/systems/pipeline/jobs tests/test_architecture.py -m "not paid and not localmodel"` | N/A — pure relocation | `systems/pipeline/jobs/domain/**`, import-line edits, guard registration |
| 2b-i | Storage core extraction + monolith delegation | PR 8 | `.venv\Scripts\python.exe -m pytest tests/shared/infrastructure/storage tests/unit/adapters/storage -m "not paid and not localmodel"` | `pytest -m integration` — crash-simulated atomic write (real filesystem) | `shared/infrastructure/storage/core.py` + monolith delegation edits |
| 2b-ii | `JobStore` facade over core | PR 9 | `.venv\Scripts\python.exe -m pytest tests/systems/pipeline/jobs/infrastructure -m "not paid and not localmodel"` | N/A — filesystem primitives behind fakes/tmp_path in default suite | `systems/pipeline/jobs/infrastructure/storage/job_store.py` + facade test moves |
| 2c | jobs commands CQRS (admit/ingest/cancel) | PR 10 | `.venv\Scripts\python.exe -m pytest tests -k "admit_job or ingest_media or cancel_job" -m "not paid and not localmodel"` | In-suite HTTP client: admit/upload/cancel paths on `create_app` | `systems/pipeline/jobs/application/use_cases/commands/*.py` + caller rewires |
| 2d | `GetJob`/`ListJobs` queries | PR 11 | `.venv\Scripts\python.exe -m pytest tests -k "get_job or list_jobs" -m "not paid and not localmodel"` | N/A — query handlers over fake store | `systems/pipeline/jobs/application/use_cases/queries/{get_job,list_jobs}.py` |
| 2e | jobs presentation + `jobs_module_api` | PR 12 | `.venv\Scripts\python.exe -m pytest tests/systems/pipeline/jobs -m "not paid and not localmodel"` | In-suite HTTP client: five job operations, precedence matrix | `systems/pipeline/jobs/presentation/**`, `jobs_module_api.py`, `main.py` router registration, drained `adapters/web` jobs half |
| 3a | transcripts domain + interfaces + guard | PR 13 | `.venv\Scripts\python.exe -m pytest tests/systems/pipeline/transcripts tests/test_architecture.py -m "not paid and not localmodel"` | N/A — pure relocation | `systems/pipeline/transcripts/domain/**`, import-line edits, guard registration |
| 3b | `TranscriptStore` facade (atomicity preserved) | PR 14 | `.venv\Scripts\python.exe -m pytest tests/systems/pipeline/transcripts/infrastructure -m "not paid and not localmodel"` | `pytest -m integration` — atomic `save_chunk_result` crash simulation | `systems/pipeline/transcripts/infrastructure/storage/transcript_store.py` + moved storage tests |
| 3c | transcripts commands + `resume_job` via module API | PR 15 | `.venv\Scripts\python.exe -m pytest tests -k "plan_chunks or transcribe_job or stitch or resume" -m "not paid and not localmodel"` | `pytest tests/unit/runtime` — fake-driven worker resume path | transcripts command handlers, `transcripts_module_api.py`, jobs-owned resume seam, supervisor import lines |
| 3d | ASR + extractor adapters to transcripts infrastructure | PR 16 | `.venv\Scripts\python.exe -m pytest tests/systems/pipeline/transcripts/infrastructure -m "not paid and not localmodel"` | `pytest -m integration` (real ffmpeg extract) | relocated `asr/` + extractor packages, `engine_resolver` import lines |
| 4a | clips domain + interfaces + guard | PR 17 | `.venv\Scripts\python.exe -m pytest tests/systems/pipeline/clips tests/test_architecture.py -m "not paid and not localmodel"` | N/A — pure relocation | `systems/pipeline/clips/domain/**`, import-line edits, guard registration |
| 4b | `ClipStore` facade | PR 18 | `.venv\Scripts\python.exe -m pytest tests/systems/pipeline/clips/infrastructure -m "not paid and not localmodel"` | N/A — filesystem primitives, tmp_path | `systems/pipeline/clips/infrastructure/storage/clip_store.py` + moved tests |
| 4c | clips CQRS (4 commands + 3 queries) | PR 19 | `.venv\Scripts\python.exe -m pytest tests -k "generate or clip_export or render_clip or purge or trajectory or subtitle or render_profiles" -m "not paid and not localmodel"` | N/A — handlers over fakes | `systems/pipeline/clips/application/use_cases/**` + caller rewires |
| 4d | clips adapters (vision/llm/render/subtitles) | PR 20 | `.venv\Scripts\python.exe -m pytest tests/systems/pipeline/clips/infrastructure -m "not paid and not localmodel"` | `pytest -m integration` — real ffmpeg render path | relocated adapter packages, `tracker_resolver` import lines |
| 4e | clips presentation + drain `adapters/web` | PR 21 | `.venv\Scripts\python.exe -m pytest tests/systems/pipeline/clips tests/systems/pipeline/jobs -m "not paid and not localmodel"` | In-suite HTTP client: three clip operations | `systems/pipeline/clips/presentation/**`, `clips_module_api.py`, emptied `adapters/web` content |
| 4f | **runtime import rewire, bodies frozen** | PR 22 | `.venv\Scripts\python.exe -m pytest tests/unit/runtime tests -m "not paid and not localmodel"` | `pytest tests/unit/runtime` — fake-driven worker/render-worker/supervisor loops (no real job in default suite) | import lines in the six `runtime/` modules + deletion of drained `adapters/storage` monolith — **reject on any body diff** |
| 5a | Atomic `/api` → `/api/v1` (+ re-export death, docs flip) | PR 23 | `.venv\Scripts\python.exe -m pytest tests/integration tests/test_architecture.py -m "not paid and not localmodel"` | In-suite HTTP client: all 8 operations on `/api/v1` + unversioned 404 no-side-effect probe | route prefixes in `main.py`, ~100 test path literals, doc path examples, `runtime/app.py` re-export removal — **one commit; `size:exception` candidate (~600)** |
| 5b | Pagination bounds + allow-list responses | PR 24 | `.venv\Scripts\python.exe -m pytest tests -k "list_jobs or pagination or visibility" -m "not paid and not localmodel"` | In-suite HTTP client: `limit`/`offset`/`mine` matrix | route `Query` bounds, `ListJobsQuery` slicing, `JobListResponse` allow-list |
| 5c | `extra="forbid"` + delete `adapters/web` | PR 25 | `.venv\Scripts\python.exe -m pytest tests/integration tests -k "forbid or unknown" -m "not paid and not localmodel"` | In-suite HTTP client: unknown-key 422 on admission + clips | request schemas' `extra` flags + `adapters/web/` package deletion |
| 6a | `test_architecture.py` final form (AB-01…AB-12) | PR 26 | `.venv\Scripts\python.exe -m pytest tests/test_architecture.py -m "not paid and not localmodel"` | N/A — static AST walker | `tests/test_architecture.py` final rule set |
| 6b | Docs closure (CLAUDE.md, config context, README) | PR 27 | `.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"` (docs do not shift tests; config context is tooling-read) | N/A — documentation only | `CLAUDE.md`, `openspec/config.yaml` context, README prose |
| 6c | Closure verification + archive preparation | PR 28 | Full default suite + `.venv\Scripts\python.exe -m mypy src tests` (the success-criteria sweep) | N/A — verification and notes only | closure notes / archive-prep artifact only |

```text
total estimated changed lines: ~8,700 (range 7,500–10,000; tests ~56% / src ~36% / config ~8%; additions+deletions incl. tests)
per-slice estimates: 0a~80; 0b~150; 1a~300; 1b~350; 1c~300; 1d~350; 2a~250; 2b-i~230; 2b-ii~220; 2c~350; 2d~300; 2e~450; 3a~250; 3b~350; 3c~400; 3d~400; 4a~250; 4b~400; 4c~400; 4d~350; 4e~400; 4f~200; 5a~600; 5b~350; 5c~250; 6a~300; 6b~400; 6c~100
Chained PRs recommended: Yes
400-line budget risk: High
Decision needed before apply: Yes
```
