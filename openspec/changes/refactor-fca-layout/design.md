# Design: Refactor to FastAPI Clean Architecture Layout

> Phase: `sdd-design` · Artifact store: hybrid (mirror of Engram `sdd/refactor-fca-layout/design`)
> Inputs: proposal rev (hybrid mirror #156), the five delta specs (mirror #167), the
> `fastapi-clean-architecture` skill (SKILL.md + `references/architecture.md` +
> `assets/module-templates.md` + `assets/security-templates.md` + `assets/fca_config.yaml`),
> and read-only inspection of the current tree. **[BINDING]** = user decision, not re-openable.

## Technical Approach

Behavior-preserving relocation of `src/onevoicecut/` from the flat hexagonal layout
(`domain/`, `ports/`, `usecases/`, `adapters/`, `runtime/`) into the FCA structure: one system
(`pipeline`), three modules (`jobs`, `transcripts`, `clips`), four layers each, plus a `shared/`
kernel — with the named HTTP-surface deltas only (`/api/v1` migration, bounded pagination,
`extra="forbid"` gate, `GetJob`/`ListJobs` queries, `SecretStr` tokens). This maps to the
proposal's Phases 0–6 and implements all five delta specs: `architecture-boundary` (the
incremental AST guard, AB-01…AB-12), `api-versioning` (AV-01…AV-08), `operator-authentication`
(AUTH-02 path flip, AUTH-10…AUTH-15, plus the new SecretStr requirement added in this phase),
`job-cancellation` (CXL-01/CXL-02 on the versioned route), `job-visibility` (VIS-09…VIS-14
pagination and allow-list responses).

Two facts about the current tree drive the hardest decisions in this design and were verified
before writing:

1. **`runtime/settings.py` imports from `adapters.web.app`, `domain.*`, and `usecases.*`**
   (`DEFAULT_MAX_UPLOAD_BYTES`, `RenderProfileInvalid`, `RenderProfile`, `SCRIPT_TARGETS`,
   `ScriptTarget`, `RENDER_PROFILES`). Naively relocating it to `shared/infrastructure/` would
   make the shared kernel import `systems.*` — a planted AB-08 violation on day one. The
   settings split below is designed around breaking that tangle first.
2. **`TranscriptStoragePort` spans domain types of all three modules** (OQ3): `JobRecord`,
   `ChunkPlan`/`ChunkResult`/`Transcript`, `GenerationResult`/`ClipExport`. No single module may
   own that Protocol without either importing another module's domain (AB-06/AB-07) or dumping
   module types into `shared/` (AB-08). The storage split below is the resolution.

No third-party dependency is added. No on-disk format changes. `runtime/` bodies are touched
only for import rewiring (proposal Deviation 3).

## Architecture Decisions

### Decision: OQ3 — Split storage into three narrow per-module interfaces over one shared filesystem core

**Choice**: Delete `ports/transcript_storage.py` as a cross-module Protocol. Each module declares
its own narrow `typing.Protocol` in its own `domain/interfaces/`, holding only methods whose
domain types that module owns:

| Module | Interface (file) | Methods (grouped from the current port) |
|--------|------------------|------------------------------------------|
| `jobs` | `JobStore` (`domain/interfaces/job_store.py`) | `job_dir`, `source_path`, `create_job`, `load_job`, `update_job`, `list_jobs`, `save_media`, `load_media`, `write_heartbeat`, `heartbeat_is_fresh`, `request_cancellation`, `cancellation_requested` |
| `transcripts` | `TranscriptStore` (`domain/interfaces/transcript_store.py`) | `audio_path`, `chunk_path`, `save_chunk_plan`, `load_chunk_plan`, `save_chunk_result`, `load_chunk_results`, `save_transcript`, `load_transcript`, `export_text` |
| `clips` | `ClipStore` (`domain/interfaces/clip_store.py`) | `save_artifacts`, `load_artifacts`, `save_clip_export`, `load_clip_exports`, `list_clip_exports`, `write_render_claim`, `render_claim_is_fresh` |

One domain-agnostic core (`shared/infrastructure/storage/core.py`) owns the on-disk layout
helpers, atomic rename-commit, and key-tolerant JSON primitives — it takes `Path`/`str`/plain
dicts only, never a domain type. Three thin adapter facades (`systems/*/infrastructure/storage/`)
each import **only their own module's domain** plus the core, and together satisfy all three
Protocols structurally. Composition roots (`main.py`, `{module}_module_api.py`, `runtime/`)
construct the core once and build the three facades; a composition root may pass the same
object to any handler because Python Protocols are structural.

Placement rationale for the method groups: heartbeats and `control.json` are job-lifecycle
liveness (written by the job worker, read by the web-process drain) → `jobs`; chunk plan/results
and the transcript are the transcription artifacts → `transcripts`; artifacts, exports, and
render claims are clip-production state → `clips`. `job_dir`/`source_path` sit with `jobs`
because admission and upload are the operations that first create the directory, and storage —
not the caller — owns the layout (existing port docstring, preserved).

**Alternatives considered**:
- *(b) One port kept in `jobs`, reached through wiring*: forces `transcripts`/`clips` application
  code to type against a `jobs` interface (cross-module contact through a back door), and leaves
  one interface importing all three domains' types — the same AB-06 problem at declaration time.
- *(c) A justified `shared/` exception*: `shared` MUST NOT import `systems.*` (AB-08) and holds
  only domain-agnostic code; `TranscriptStoragePort` is nothing but domain types. Rejected.
- *Monolithic adapter class implementing all three Protocols*: such a class imports all three
  domains, so it could live only in a composition root — `worker.py` and `render_worker.py`
  would import app-level code to get storage. The three-facade split keeps each import legal
  and each module's storage tests independent.

**Rationale**: this is the only option that satisfies AB-06/AB-07/AB-08 simultaneously while
keeping `save_chunk_result` atomicity, derived progress, and legacy decode behavior byte-frozen
inside their owning facades. Splitting at the Protocol boundary — not at the filesystem — means
one `jobs/{ulid}/` directory still has exactly one layout owner (the core).

### Decision: OQ3-adjacent — `ids.py` and `capabilities.py` move to `shared/domain`

**Choice**: `domain/ids.py` (ULID types + regex validation for `JobId`, `ClipId`, `MediaId`,
`OperatorId`) and `ports/capabilities.py` (`DeclaredSupport`, `DetectionSupport`) relocate to
`shared/domain/`.

**Alternatives considered**: proposal's module map put `ids.py` under `jobs` and did not assign
`capabilities.py`. JobId alone is referenced by all three modules' storage facades, the worker,
the render worker, and the supervisor; keeping it in `jobs` would make every other module's
domain and infrastructure import `jobs.domain` for a pure identifier — AB-07 on the most
common import in the tree. Capability declarations are consumed by `jobs` (admission refusal),
ASR adapters (declaring), and the render path (detection) — same multi-module pressure.

**Rationale**: identifiers and capability enums are kernel vocabulary, not business rules; the
spec forbids *module-specific business rules* in `shared`, and `DomainError` already sets the
precedent of shared domain-shaped vocabulary. This is a deliberate, documented refinement of the
proposal's map, made because the map as written cannot satisfy the boundary spec it also
mandates.

### Decision: CQRS handler shape — skill `Command`/`Query` dataclasses + `Handler` classes

**Choice**: Each of the 15 use cases converts to the skill template shape — a frozen
`@dataclass` request object (`{Name}Command` / `{Name}Query`, carrying `principal: Principal`
where the caller's identity matters) plus a `{Name}Handler` class whose `__init__` takes its
dependencies and whose `handle()` method contains the **verbatim moved body** of today's
function. One handler per file under `application/use_cases/{commands,queries}/`.

```python
# systems/pipeline/jobs/application/use_cases/commands/admit_job.py
@dataclass(frozen=True, slots=True)
class AdmitJobCommand:
    principal: Principal
    engine: EngineChoice
    speaker_mode: SpeakerMode

class AdmitJobHandler:
    def __init__(self, store: JobStore, *, now: Callable[[], float],
                 new_job_id: Callable[[], JobId], new_media_id: Callable[[], MediaId],
                 capabilities: Callable[[EngineChoice], DeclaredSupport] | None) -> None: ...
    def handle(self, command: AdmitJobCommand) -> Admission: ...
```

**Alternatives considered**: keeping module-level functions (still "one per file") — rejected
because full skill adoption is the binding intent, and the handler class is what the skill's
controllers, test templates, and wiring examples are written against; mixing shapes across
modules would make the layout teach the wrong pattern.

**Rationale**: the conversion is a mechanical wrap-and-rename; the function bodies do not change,
so relocation slices stay proven by the unchanged suite. CQRS classification follows the
proposal: writes → `commands/`, reads/derivation → `queries/` (`GetJob`, `ListJobs`,
`render_profiles`, `plan_trajectory`, `build_subtitle_cues`). `ownership.require_owner` is a
pure domain rule → `jobs/domain/ownership.py`, not a handler.

### Decision: Composition roots — `src/onevoicecut/main.py` web root; `runtime/` kept parallel

**Choice**: `src/onevoicecut/main.py` is the web composition root: builds `Settings`, parses the
token map (the one `get_secret_value()` call), runs the profile-membership preflight, constructs
storage facades, builds `CurrentPrincipal`, assembles module controllers via
`{module}_module_api.py`, registers routers under `/api/v1`, installs the `DomainError` handler.
Uvicorn entrypoint becomes `onevoicecut.main:get_app`. `runtime/{worker,render_worker,supervisor,
engine_resolver,tracker_resolver}` remain separate-process composition roots (Deviation 3) and
import `{module}_module_api` wiring — never the reverse. During Phases 1–4,
`runtime/app.py:get_app` is a one-line re-export of `main.get_app` so existing tests and docs
keep working; the re-export and the old factory die in Phase 5 together with the path migration
(docs flip in the same slice). Supervisor loops (`drain`, watchdog, render drain) stay in
`runtime/` untouched — only their import lines follow relocated types.

**Alternatives considered**: repo-root `main.py` (skill default) — rejected: breaks
`PYTHONPATH=src` and the mypy `src`+`tests` scope for a path-only preference; keeping
`runtime/app.py` as the web root — rejected: the skill contract names `main.py`, and the
proposal already lists it as a new file.

### Decision: Settings tangle — pure env parsing in `shared/infrastructure/settings.py`; preflight moves to composition roots

**Choice**: `Settings` keeps every field and default but loses its imports from `usecases.*`,
`adapters.web.app`, and `domain.rendering`. Specifically:
- `DEFAULT_MAX_UPLOAD_BYTES` inlines as `16 * 1024**3` in `settings.py` (the constant dies in
  `adapters/web/app.py`).
- `DEFAULT_SCRIPT_TARGETS` inlines as its literal string (`"tiktok,instagram,youtube,facebook"`).
- The `_targets_name_defined_profiles` model validator **moves out of `Settings`** into a
  composition-root preflight function (`check_target_profiles` relocated beside `main.py`'s boot
  path, still raising the same `RenderProfileInvalid` naming every dangling row). Boot still
  refuses before serving; the refusal just happens one call later, in a file allowed to import
  `clips`. A cross-module unit test pins `Settings().script_targets ⊆ RENDER_PROFILES` so the
  inlined default cannot drift from the registry unnoticed.
- `CHUNK_TIMEOUT_ENV_NAMES` and `load_env_file()` move verbatim.

**Alternatives considered**: keeping the validator on `Settings` and exempting
`shared/infrastructure` from AB-08 — rejected: weakening the guard to preserve a convenience
validator inverts the point of the change; duplicating `RENDER_PROFILES` into `shared` —
rejected: two registries is exactly the drift `check_target_profiles` exists to catch.

### Decision: Pagination — default `limit=20`, `offset` bounded `0..10_000`

**Choice**: `GET /api/v1/jobs` accepts `mine: bool = False`, `limit: int = Query(20, ge=1, le=100)`,
`offset: int = Query(0, ge=0, le=10_000)`. Omitted `limit` therefore returns a bounded page of
20, never the full listing. Response stays the existing allow-list wrapper
`{"jobs": [JobListItem, ...]}` — no `total`/echo fields (not required by `job-visibility`;
additive later without a break because the wrapper exists for exactly that).

**Alternatives considered**: omitted limit = full listing — rejected: it preserves the OWASP
API4 unbounded-response finding for the most common request shape (no params), which is the
very gap pagination exists to close; default 50/100 — rejected: 20 is the skill template default
and a poll-friendly page for a shared board whose items are record-derived only. Unbounded
non-negative offset — rejected: the skill template bounds it at 10_000 and OWASP asks for a
bounded offset; no single-team sermon archive approaches 10k jobs, so the bound costs nothing
and closes deep-page probing. `job-visibility` requires only rejection of negative/non-integer
offsets and does not require accepting arbitrarily large ones, so no spec delta is needed.

Pagination applies **after** the server-side `mine` filter (VIS-12): handler filters the
unscoped `list_jobs()` tuple, then slices `[offset:offset+limit]` — the same unscoped listing
reconcile uses remains the source.

### Decision: Auth shape — `CurrentPrincipal` factory over the static token map (no JWT)

**Choice**: `shared/application/principal.py` defines

```python
@dataclass(frozen=True, slots=True)
class Principal:
    identity: OperatorId          # never a token value
    roles: frozenset[str] = frozenset()   # empty today; enables require_roles later
```

`shared/presentation/security.py` exposes `make_current_principal(authenticate) -> Annotated[Principal, Depends(...)]`
built at the composition root over the existing constant-time `build_authenticator` scan (which
moves verbatim to `shared/application/principal.py` or stays beside parsing in
`shared/infrastructure/token_map.py` — the comparison loop is behavior-frozen either way).
Routes declare `principal: CurrentPrincipal`; FastAPI resolves it **before** the handler runs,
so 401 still precedes id validation (404) and ownership (403). `parse_operator_tokens` +
`build_authenticator` are called only in composition roots, fed by
`Settings.operator_tokens.get_secret_value()` once — the new spec requirement.

**Alternatives considered**: skill's JWT `verify_access_token` — rejected (binding: bearer map
stays); keeping the closure-over-`WebDependencies` `_authorized` helper — rejected: it is
route-level auth wiring the skill replaces with a dependency, and the route-table-generated 401
test (AUTH-06/AV-08) proves deny-by-default either way.

**Rationale**: deny-by-default remains structural in two layers — `make_current_principal` cannot
be constructed without an authenticator (composition root), and the generated route-table test
fails the default run the day a route forgets `CurrentPrincipal`. Ownership stays out of
presentation: controllers translate `JobNotOwned` → 403 and nothing else (AUTH-10).

### Decision: Error mapping — central `DomainError` handler in `main.py`, current codes preserved

**Choice**: one exception handler installed at the composition root:

| Exception | Status | Notes (all match today's route-local mappings) |
|-----------|--------|------------------------------------------------|
| `JobNotFound` (and malformed-id 404 raised in controllers) | 404 | validate-then-load order preserved |
| `JobNotOwned` | 403 | generic detail, never names the owner |
| `JobAlreadyExists`, `ArtifactsNotAvailable` | 409 | |
| `ClipCandidateNotFound` | 404 | |
| `UploadTooLarge` | 413 | |
| `UnsupportedContainer` (+ no-audio-stream refusal raised as 415 in controller) | 415 | |
| `DiarizationUnsupported`, `ClassificationUnsupported`, `ClipTargetsInvalid`, `RenderProfileInvalid` | 422 | admission/clip validation |
| any other `DomainError` | 422 | skill default |
| unhandled `Exception` | 500 | logged server-side without headers or body (AUTH-12) |

Presentation-level `HTTPException`s that already exist for pure HTTP/state concerns
(`Content-Length` pre-check 413, "job not PENDING" 409, "job not COMPLETED" 409, malformed id
404) move verbatim into controllers — documented deviation: they predate the change and their
bodies are already tested; new state refusals should become domain errors instead.

**Alternatives considered**: per-route try/except as today — rejected: it is what the skill's
central handler replaces and it scattered the precedence logic; mapping everything through
`HTTPException` inside handlers — rejected: domain errors must stay domain errors across ports
(CLAUDE.md invariant), with only the edge translating.

### Decision: Routers split across two modules under one HTTP prefix

**Choice**: `jobs` owns five operations (`POST /jobs`, `GET /jobs`, `GET /jobs/{id}`,
`PUT /jobs/{id}/media`, `POST /jobs/{id}/cancel`); `clips` owns three
(`POST /jobs/{id}/clips`, `GET /jobs/{id}/clips/{clip_id}`,
`GET /jobs/{id}/clips/{clip_id}/{profile}`). Both routers are built with **relative** paths and
registered by `main.py` at prefix `/api/v1/jobs` (clips nests under the jobs resource — the
paths are fixed by `api-versioning`, and forcing them to `/api/v1/clips/...` would break AV-01).

**Alternatives considered**: one `jobs` module owning all eight (clips HTTP is job-nested) —
rejected: the proposal's module map assigns the three clip operations to `clips`, and render
state is clips-owned; a `/api/v1/clips` prefix — rejected: contradicts AV-01's named paths.

### Decision: Upload pipeline becomes a jobs command, streaming behavior frozen

**Choice**: the body of today's `PUT /media` handler (ownership → state check → size pre-check →
store → re-read → probe → `save_media` → `update_job(QUEUED)`) moves into
`IngestMediaCommand/Handler` with the same statement order. The command carries an
`AsyncIterator[bytes]` and the client filename (already percent-decoded metadata). `MediaSourcePort`
and a new narrow `MediaProbePort` (probe-only slice of `AudioExtractorPort`) are declared in
`jobs/domain/interfaces/`; the full `AudioExtractorPort` (extract/slice) is transcripts-owned.
`FfmpegAudioExtractor` satisfies both structurally and is bound at composition roots.

**Rationale**: controllers become thin (schema → DTO → handler → schema) as the skill requires,
and the one async port stays web-only, exactly as CLAUDE.md records.

### Decision: Test tree mirrors source; fakes stay shared

**Choice**: unit tests move with their code to `tests/systems/pipeline/{jobs,transcripts,clips}/`
and `tests/shared/`; `tests/fakes/` stays put (cross-module test doubles, already outside the
unit tree); `tests/integration/` and `tests/contract/` stay top-level (they span modules);
`tests/test_architecture.py` stays at `tests/` and grows incrementally (AB-11).

### Decision: `fca_config.yaml` at repo root

**Choice**: created in Phase 0 from the skill template with: `project.name: onevoicecut`,
`dependency_manager: pip`, `database: filesystem`, `api_prefix: /api/v1`; one system `pipeline`
with modules `jobs`/`transcripts`/`clips`, each declaring `v1 active`; `security.auth: bearer`
(skill enum extended — Deviation 2), `roles: []`, `rate_limiting: none` (local tool behind
operator tokens; no gateway exists), `docs_in_production: false` (docs already disabled);
`tooling: pytest / mypy`, `security: []` (bandit/pip-audit deferred — Deviation 5).

## Data Flow

HTTP request (authenticated surface):

```
Client ──Bearer──▶ route (presentation/v1)
                     │  CurrentPrincipal resolves ──▶ 401 before anything else
                     ▼
                  controller: schema → DTO, validate ids (404), translate 403
                     ▼
                  {Name}Handler.handle(Command|Query)     [application]
                     ▼
                  domain rule / domain/interfaces Protocol
                     ▼
                  facade over shared storage core          [infrastructure]
                     ▲ bound once by main.py / {module}_module_api
```

Worker process (unchanged logic, rewired imports):

```
runtime/worker.py ──imports──▶ jobs/transcripts {module}_module_api
        │                            │
        │  load_job / heartbeat      │  TranscriptStore facade
        │  (JobStore)                │  save_chunk_result (atomic)
        ▼                            ▼
   shared/infrastructure/storage/core  ──▶  jobs/{ulid}/ on disk
```

Composition (web):

```
main.py: Settings ─▶ token map (get_secret_value once) ─▶ CurrentPrincipal dep
                   ─▶ storage core + 3 facades ─▶ jobs/transcripts/clips module_api
                   ─▶ check_target_profiles preflight ─▶ include_router(…, prefix="/api/v1/jobs")
                   ─▶ DomainError handler ─▶ get_app
```

Sequence — precedence on a mutating route (AUTH-13/14/15, CXL-02):

```
Client        route         CurrentPrincipal   controller      cancel handler    store
  │  POST cancel  │               │                │                │              │
  ├──────────────▶│               │                │                │              │
  │               ├──────────────▶│                │                │              │
  │               │  no/invalid   │                │                │              │
  │◀── 401 ───────┤◀── raise ─────┤                │                │              │
  │  (valid)      │               │                │                │              │
  │               ├───────────────┼─── principal ─▶│                │              │
  │               │               │                ├─ make_job_id   │              │
  │               │               │                │  bad/unknown   │              │
  │               │               │                ├─ load_job ─────┼─────────────▶│
  │               │               │                │  JobNotFound   │              │
  │◀── 404 ───────┤◀──────────────┼────────────────┤◀───────────────┤              │
  │  (owner≠caller)               │                ├─ require_owner │              │
  │               │               │                │  JobNotOwned   │              │
  │◀── 403 ───────┤◀──────────────┼────────────────┤◀───────────────┤              │
  │  (owner)      │               │                ├─ CancelJob ────┼──request────▶│
  │◀── 200 + state┤◀──────────────┼────────────────┤◀───────────────┤              │
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/onevoicecut/main.py` | Create | Web composition root: settings, token map, preflight, facades, routers, error handler, `get_app` |
| `src/onevoicecut/systems/pipeline/{jobs,transcripts,clips}/…` | Create | Four layers each per the module map below |
| `src/onevoicecut/shared/domain/{errors,ids,capabilities}.py` | Move | From `domain/errors.py`, `domain/ids.py`, `ports/capabilities.py` |
| `src/onevoicecut/shared/application/principal.py` | Move+adapt | From `adapters/web/auth.py` (parse/compare loop verbatim) + `Principal` |
| `src/onevoicecut/shared/infrastructure/settings.py` | Move+slim | From `runtime/settings.py`; drop usecase/web imports; preflight extracted; `operator_tokens: SecretStr` |
| `src/onevoicecut/shared/infrastructure/storage/core.py` | Create | Layout helpers, atomic rename, JSON primitives (from `adapters/storage/filesystem_transcript_storage.py` + `serialization.py`, behavior-frozen) |
| `src/onevoicecut/shared/presentation/security.py` | Create | `CurrentPrincipal` factory, `require_roles` (unused) |
| `src/onevoicecut/systems/*/domain/*` | Move | `jobs/{jobs,media,ownership}.py`, `transcripts/{chunking,transcript}.py`, `clips/{generation,rendering,framing}.py` |
| `src/onevoicecut/systems/*/domain/interfaces/*` | Create | Narrow Protocols per OQ3 table + `MediaSourcePort` (jobs), `AudioExtractorPort`/`TranscriptionPort` (transcripts), `TextGenerationPort`/`SubjectTrackerPort`/`VideoRenderPort` (clips) |
| `src/onevoicecut/systems/*/application/use_cases/{commands,queries}/*` | Move+split | 15 use cases → handler files; + `GetJob`, `ListJobs`, (upload already counted in `ingest_media`) |
| `src/onevoicecut/systems/*/infrastructure/storage/*.py` | Create | Three facades over `core.py` (from `adapters/storage/`) |
| `src/onevoicecut/systems/*/infrastructure/{ffmpeg,asr,llm,vision}/…` | Move | Adapters relocate with the domain types they name: extractor+asr → `transcripts`, ollama → `clips`, vision+video_render+subtitles → `clips`; domain-type-free ffmpeg process helpers (`process`, `sendcmd`, `argv` if clean) → `shared/infrastructure/` |
| `src/onevoicecut/systems/jobs/presentation/{schemas,routes,controllers}/v1/*` | Create | From `adapters/web/{schemas,routers/jobs}.py` (jobs operations) |
| `src/onevoicecut/systems/clips/presentation/{routes,controllers}/v1/*` | Create | Three clip operations |
| `src/onevoicecut/systems/*/{module}_module_api.py` | Create | Per-module wiring, skill pattern |
| `src/onevoicecut/adapters/web/*` | Delete (Phase 5) | Drained into presentation + `shared` |
| `src/onevoicecut/adapters/{storage,ffmpeg,asr,llm,vision}/*` | Delete (as drained) | Nothing deleted until its replacement is green |
| `src/onevoicecut/ports/*`, `domain/*`, `usecases/*` | Delete (as drained) | Legacy packages empty at Phase 6 |
| `src/onevoicecut/runtime/settings.py` | Delete (Phase 1) | Replaced by `shared/infrastructure/settings.py`; `runtime/` imports rewire |
| `src/onevoicecut/runtime/app.py` | Modify (imports only) | `get_app` → re-export `main.get_app` (Phases 1–4), removed Phase 5; supervisor loops untouched |
| `src/onevoicecut/runtime/{worker,render_worker,supervisor,engine_resolver,tracker_resolver}.py` | Modify (imports only) | Follow module moves; **bodies frozen** |
| `fca_config.yaml` | Create (Phase 0) | Registry per Decision above |
| `tests/test_architecture.py` | Rewrite incrementally | Legacy hexagonal rules + FCA rules grow per phase (AB-11); full FCA set lands Phase 6 |
| `tests/**` | Relocate + update | Mirror source tree; ~100 `/api/` literals flip in Phase 5; `test_settings_auth`/`test_secret_discipline` extend for `SecretStr` |
| `CLAUDE.md`, `openspec/config.yaml` context, `README.md` | Modify (Phase 6) | Architecture description, HTTP surface, entrypoint |
| `openspec/changes/refactor-fca-layout/specs/operator-authentication/spec.md` | Modify (this phase) | One ADDED requirement: SecretStr + composition-only parsing |

### Module map (final)

| Module | commands/ | queries/ | domain | interfaces | HTTP (v1) |
|--------|-----------|----------|--------|------------|-----------|
| `jobs` | `admit_job`, `ingest_media`, `cancel_job`, `resume_job`* | `get_job`, `list_jobs` | `jobs.py`, `media.py`, `ownership.py` | `JobStore`, `MediaSourcePort`, `MediaProbePort` | 5 job operations |
| `transcripts` | `plan_chunks`, `transcribe_job`, `stitch_transcript` | — | `chunking.py`, `transcript.py` | `TranscriptStore`, `AudioExtractorPort`, `TranscriptionPort` | none (worker-driven) |
| `clips` | `generate_artifacts`, `request_clip_export`, `render_clip`, `purge_job_artifacts` | `render_profiles`, `plan_trajectory`, `build_subtitle_cues` | `generation.py`, `rendering.py`, `framing.py` | `ClipStore`, `TextGenerationPort`, `SubjectTrackerPort`, `VideoRenderPort` | 3 clip operations |
| `shared` | `principal.py` (parse + `Principal`) | — | `errors.py`, `ids.py`, `capabilities.py` | — | `security.py` (`CurrentPrincipal`) |

\* `resume_job`'s `pending_chunks` is pure over `ChunkResult` → transcripts; the supervisor
(runtime) imports it through the transcripts module API. `PublishPort` does not exist in the
tree today (verified) — this change does not create it; when declared it belongs to
`clips/domain/interfaces/`.

## Interfaces / Contracts

Narrow storage Protocols (signatures abridged; full docstrings move with the methods):

```python
# systems/pipeline/jobs/domain/interfaces/job_store.py
class JobStore(Protocol):
    def job_dir(self, job_id: JobId) -> Path: ...
    def source_path(self, job_id: JobId) -> Path: ...          # extensionless, storage-owned
    def create_job(self, job: JobRecord) -> None: ...
    def load_job(self, job_id: JobId) -> JobRecord: ...
    def update_job(self, job: JobRecord) -> None: ...
    def list_jobs(self) -> tuple[JobRecord, ...]: ...          # sorted by id (FIFO contract)
    def save_media(self, job_id: JobId, media: SourceMedia) -> None: ...
    def load_media(self, job_id: JobId) -> SourceMedia: ...
    def write_heartbeat(self, job_id: JobId, *, at_s: float) -> None: ...
    def heartbeat_is_fresh(self, job_id: JobId, *, now_s: float, stale_after_s: float) -> bool: ...  # fail-closed
    def request_cancellation(self, job_id: JobId, *, requested: bool = True) -> None: ...
    def cancellation_requested(self, job_id: JobId) -> bool: ...

# systems/pipeline/transcripts/domain/interfaces/transcript_store.py
class TranscriptStore(Protocol):
    def audio_path(self, job_id: JobId) -> Path: ...
    def chunk_path(self, job_id: JobId, index: int) -> Path: ...
    def save_chunk_plan(self, job_id: JobId, plan: ChunkPlan) -> None: ...
    def load_chunk_plan(self, job_id: JobId) -> ChunkPlan | None: ...
    def save_chunk_result(self, result: ChunkResult) -> None: ...   # MUST stay atomic
    def load_chunk_results(self, job_id: JobId) -> tuple[ChunkResult, ...]: ...
    def save_transcript(self, transcript: Transcript) -> None: ...
    def load_transcript(self, job_id: JobId) -> Transcript | None: ...
    def export_text(self, job_id: JobId, text: str) -> Path: ...

# systems/pipeline/clips/domain/interfaces/clip_store.py
class ClipStore(Protocol):
    def save_artifacts(self, job_id: JobId, artifacts: GenerationResult) -> None: ...
    def load_artifacts(self, job_id: JobId) -> GenerationResult | None: ...
    def save_clip_export(self, export: ClipExport) -> None: ...      # rename-committed
    def load_clip_exports(self, job_id: JobId, clip_id: ClipId) -> tuple[ClipExport, ...]: ...
    def list_clip_exports(self) -> tuple[ClipExport, ...]: ...       # unfiltered discovery
    def write_render_claim(self, job_id: JobId, clip_id: ClipId, *, at_s: float) -> None: ...
    def render_claim_is_fresh(self, job_id: JobId, clip_id: ClipId, *, now_s: float, stale_after_s: float) -> bool: ...
```

Pagination contract (presentation):

```python
@router.get("", response_model=JobListResponse)
async def list_jobs(
    principal: CurrentPrincipal,
    controller: JobsController,
    mine: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> JobListResponse: ...
```

`fca_config.yaml` (shape):

```yaml
project: { name: onevoicecut, python: "3.12", dependency_manager: pip,
           database: filesystem, api_prefix: /api/v1 }
systems:
  - name: pipeline
    modules:
      - { name: jobs,        api_versions: [{ version: v1, status: active, sunset: null }] }
      - { name: transcripts, api_versions: [{ version: v1, status: active, sunset: null }] }
      - { name: clips,       api_versions: [{ version: v1, status: active, sunset: null }] }
security: { auth: bearer, roles: [], rate_limiting: none, docs_in_production: false }
tooling:  { tests: pytest, type_checker: mypy, linter: ruff, security: [] }
```

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | Each converted handler: same assertions as today's use-case tests, plus `Principal`-carried identity; `GetJob`/`ListJobs` (RED-first: pagination bounds, mine composition, page-union completeness, 422 before work) | Existing fakes in `tests/fakes/`, relocated not rewritten; new query tests written before handlers (strict TDD) |
| Unit | `SecretStr`: `Settings` repr/str redact tokens; composition-only `get_secret_value` (AUTH-16/17 scenarios from the new spec requirement) | Extend `test_settings_auth.py`, `test_secret_discipline.py` |
| Integration | Route behavior on `create_app`: status codes per operation, 401/404/403 precedence, 413/415/409/422 mappings, unknown-key → 422 (AV-06/07), unversioned path → 404 (AV-03) with no side effects | `tmp_path` + fakes, unchanged bodies wherever possible |
| Contract | Route-table-generated 401 (AUTH-06) and owner-only 403 checks — derived from `app.routes`, so they follow the v1 prefix automatically (AV-08) | Existing generators preserved verbatim |
| Architecture | AB-01…AB-12 via AST walker: legacy `domain/usecases/ports` rules stay until packages empty; FCA rules add per migrated module from the slice that migrates it; planted-violation RED proof for each new rule group (AB-12) | Extend `tests/test_architecture.py` in place, never weaken |
| Storage | Atomic `save_chunk_result`, key-tolerant legacy decode, path confinement, extensionless source — unchanged suite against facades | Existing storage tests move with facades |
| E2E (default suite) | Every slice ends with `pytest -m "not paid and not localmodel"` green + `mypy src tests` clean; zero skips as evidence about ffmpeg surface | Config `test_command`/`build_command` |

Strict TDD applies to behavior slices (pagination, forbid-gate test, path migration, guard
rewrites, new queries). Relocation slices prove equivalence by the unchanged suite — no
manufactured RED for a path rename (proposal's honesty rule).

## Threat Matrix

The design changes routing (prefix migration, new query params), so the matrix was loaded from
`sdd-design/references/threat-matrix.md`. Product code introduces no shell, subprocess, VCS, PR,
or executable-classification boundary: ffmpeg argv construction, list-form `subprocess.run`,
`-nostdin`, path confinement, ULID validation, and client-filename-as-metadata all relocate
byte-identically (covered by the existing security invariants, not by new threat rows).

| Boundary | Applicability | Reason |
|----------|---------------|--------|
| Documentation-like paths | **N/A** — no executable docs; `requirements*.txt` never parsed as config or executed by product code |
| Git repository selection | **N/A** — no product code invokes git; delivery (stacked PRs) is process, not a shipped boundary |
| Commit state | **N/A** — no automation reads the index |
| Push state | **N/A** — no automation resolves refs or remotes |
| PR commands | **N/A** — no PR-argument composition exists in the codebase |

No RED tests are planned for `N/A` rows (spec: do not manufacture irrelevant tasks). Existing
security-invariant tests (path traversal via `%2e%2e` → 404, no `UploadFile` import, rename
commit, list-form ffmpeg) continue to guard the relocated code unchanged.

## Migration / Rollout

No data migration: on-disk `job.json` format, chunk files, transcripts, and renders are
untouched; `legacy-job-compatibility` holds in both directions; rollback needs no cleanup
(proposal Rollback Plan stands as written).

Phased code rollout (proposal table, with the [BINDING] incremental guard):

| Phase | Content | Guard state at phase end |
|-------|---------|--------------------------|
| 0 — Prep | Green baseline commit; `fca_config.yaml`; module map frozen (this design); guard rewrite *plan* | Legacy guard only |
| 1 — Shared + main | `shared/**`, `main.py`, settings split, principal/security, error handler; `runtime` import rewire | Legacy guard **+** `shared/` rules (AB-03/08) |
| 2 — jobs | jobs four layers, CQRS, `GetJob`/`ListJobs`, job presentation (still `/api`) | + `jobs` rules (AB-01/02/04–07/09/10 for jobs) |
| 3 — transcripts | transcripts layers; ASR/extractor → infrastructure | + `transcripts` rules |
| 4 — clips | clips layers; render/vision/llm → infrastructure; `adapters/web` drains | + `clips` rules |
| 5 — HTTP surface | Atomic `/api` → `/api/v1`; pagination tests; forbid-gate test; delete `adapters/web`; drop `get_app` re-export | Full path coverage; route-table tests on v1 |
| 6 — Closure | Remaining FCA rules (AB-12 RED proof), docs (`CLAUDE.md`, config context, README), archive prep | Complete; legacy packages empty |

Every slice: default suite green + mypy strict clean + diff measured against the 800-line
config budget (`delivery_strategy: auto-chain`, `chain_strategy: stacked-to-main`; split at the
first seam where both halves are green alone). Feature flags: none — there is no runtime
toggle for a layout. Phase 5 is the only externally visible break and ships atomically with its
tests and docs (AV-01…AV-03).

## Open Questions

- None blocking design. Process notes carried forward, not design decisions:
  - **OQ1** (archive `video-transcription-pipeline` before Phase 1) remains an orchestrator
    sequencing preference; it does not change any interface in this document.
  - Spec-phase: `operator-authentication` gains exactly one ADDED requirement in this phase
    (SecretStr / composition-only parsing) — applied to the delta file alongside this design.
