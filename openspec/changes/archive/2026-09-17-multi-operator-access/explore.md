# Exploration: multi-operator-access

Research-only artifact (SDD explore phase). Grounds every claim in the code as of 2026-09-01,
compares options per workstream, and records what the design phase must still decide.

**Goal (maintainer-decided):** several operators against ONE shared server. Backend
Python/FastAPI only. `FilesystemTranscriptStorage` and the local `data_dir` survive — no DB,
no object storage, no distributed machines. The Angular frontend is explicitly out of scope;
API shapes must stay frontend-friendly (stable JSON contracts, clean 401/403 semantics).

---

## 1. Verified current state (code-grounded)

### 1.1 No identity anywhere

- `domain/jobs.py` — `JobRecord` fields: `job_id, media_id, state, speaker_mode, engine,
  created_at, updated_at, worker_pid, error`. No owner, no operator, no tenant concept in
  `domain/` at all.
- `domain/ids.py` — server-minted ULID `NewType`s (`JobId`, `MediaId`) validated by
  `make_job_id`/`make_media_id` against `^[0-9A-HJKMNP-TV-Z]{26}$` before ever touching a
  path. ULIDs sort by creation time; `list_jobs` relies on that.
- `domain/errors.py` — all errors derive from `DomainError`. Precedents worth reusing:
  `JobNotFound` is also raised for malformed ULIDs so responses never reveal which ids exist;
  `EngineUnavailable` refuses rather than substitutes; `JobAlreadyExists` separates
  create/update on the port.

### 1.2 HTTP surface (zero authentication today)

`adapters/web/routers/jobs.py` has exactly three routes:

| Route | Behavior today |
| --- | --- |
| `POST /api/jobs` | `admit_job(...)` mints both ids server-side, records `PENDING`. No caller identity captured. |
| `PUT /api/jobs/{id}/media` | `_load` → size check → raw-body stream write → `ffprobe` verify → `save_media` → `deps.start_job(job_id)` spawns a worker. |
| `GET /api/jobs/{id}` | `_load` + `derive_progress` over plan/results. Read-only by construction. |

No list endpoint. No cancel endpoint (grep for `cancel` in `adapters/web` returns nothing),
even though `TranscriptStoragePort.request_cancellation` / `cancellation_requested` exist and
`transcribe_job` polls `cancellation_requested` at every chunk boundary. Cancellation is a
port with no route — relevant because the maintainer's scope list expects cancel/purge to
verify ownership.

Handler style is load-bearing for the auth design: `build_jobs_router(deps)` is "a closure
over the dependencies rather than FastAPI's `Depends`" — deliberate, to keep handlers free of
framework injection. Any auth mechanism should respect that precedent (see 2b).

`adapters/web/app.py` — `WebDependencies` (frozen dataclass): `storage, max_upload_bytes,
now, new_job_id, new_media_id, media_source_for, extractor_for, start_job, capabilities`.
`start_job` defaults to `no_job_starter`, which raises on purpose.

### 1.3 Runtime: unbounded spawning, pid-based reconcile

`runtime/app.py`:

- `spawn_worker` builds `[sys.executable, "-m", WORKER_MODULE, "--job-id", <ulid>,
  "--data-dir", <dir>]` and fires `subprocess.Popen` — **one unbounded process per upload, no
  cap, no queue**. Triggered from inside the upload route after `save_media`.
- `process_is_alive(pid)` = `os.kill(pid, 0)`. Docstring explicitly accepts the pid-reuse
  risk "on a single-operator machine".
- `reconcile_interrupted_jobs(storage, now, is_alive)` runs in `build_app`'s lifespan before
  the first request: iterates `storage.list_jobs()`, and for jobs in `TRANSCRIBING` whose
  `worker_pid` is dead, writes `INTERRUPTED`. It is the only non-worker writer of `job.json`,
  justified by the liveness check. **It only looks at `TRANSCRIBING`** — a job that died in
  `EXTRACTING`, `PLANNED`, `STITCHING`, or `GENERATING` is orphaned forever (pre-existing gap,
  see risks).
- `build_dependencies` constructs `FilesystemTranscriptStorage(settings.data_dir)` and the
  starter; `Settings` (pydantic-settings, `env_prefix="ONEVOICECUT_"`) reads only `data_dir`
  (required, no default) and `max_upload_bytes`. Nothing below `runtime/` reads the
  environment.

### 1.4 Worker

`runtime/worker.py` — loads the job by id, claims it with `update_job(worker_pid=os.getpid())`
before any work, resolves the engine via `EngineResolver`, runs `transcribe_job`, exits
0/1/2/3. **It reads no credentials and receives no operator argument** — it needs nothing
identity-related (see 2b).

### 1.5 Storage and serialization

- `adapters/storage/filesystem_transcript_storage.py` — layout `{data_dir}/jobs/{ulid}/...`;
  `list_jobs()` sorts directory names (ULID = creation order), skips non-job directories, but
  raises `CorruptedRecord` for a job record that does not decode. `save_chunk_result` and all
  writes are atomic (`.tmp` + `fsync` + `os.replace`). `request_cancellation` writes a
  separate `control.json` precisely so the web process never edits `job.json`.
- `adapters/storage/serialization.py` — decoding is **explicit field-by-field**; a missing
  field raises `CorruptedRecord` ("a payload that no longer matches the entity is rejected at
  the boundary"). `worker_pid` and `error` are read via `_optional_*` helpers. This is the
  load-bearing fact for the migration story (2a): adding a *required* field to `JobRecord`
  makes every pre-change `job.json` a `CorruptedRecord`, and since `list_jobs` does not skip
  corrupt records, **startup reconcile would crash the server on any existing data
  directory**.

### 1.6 Verified facts that differ from the launch brief

- **No live job directories exist in this checkout.** Glob for `data/**` finds nothing;
  `data/` is not even in `.gitignore` (which covers `uploads/`, `media/`, `output/`,
  `transcripts/`, media extensions, and model weights). The migration question therefore
  concerns operator deployment machines, not the repo. It also makes the missing `data/`
  ignore entry a cheap, real finding (risk R6).
- `openspec/config.yaml` records `review.budget_lines: 800` (raised from 400 on 2026-08-31),
  `delivery_strategy: auto-chain`, `chain_strategy: stacked-to-main`, `strict_tdd: true`.
  CLAUDE.md still says "400 lines per slice" — doc drift the tasks phase should not inherit.
- README.md line 9 and CLAUDE.md both assert the single-operator premise ("Single operator,
  runs locally."). This change is a documented-premise scope change; doc deltas are in scope.

---

## 2. Workstreams

### 2a. Identity: `OperatorId`, owner on `JobRecord`, migration

**Type options.**

- *O1 — ULID `NewType`, symmetric with `JobId`.* Validation precedent exists. But operators
  are *configured* principals, not server-minted entities: ULIDs in an env/config map are
  unreadable (`"01HQ..."` for "maria"), rotation notes become opaque, and log lines lose
  their human meaning. The property ULIDs buy for jobs (client never controls identity) is
  already guaranteed here another way: the operator id written to a record comes from the
  server-side token→operator resolution, never from the request.
- *O2 — opaque validated string* (e.g. lowercase `[a-z0-9_-]{1,64}`). Readable in config and
  logs, validated in `domain/ids.py` next to the existing id discipline, cheap to type in a
  `.env`.

**Recommendation: O2.** `OperatorId = NewType("OperatorId", str)` in `domain/ids.py` with a
`make_operator_id` validator. The domain owns the shape; the *set* of existing operators is
configuration, not domain knowledge. Multi-tenancy rule applied (skill `multi-tenancy.md`):
the operator identity is **derived from the authenticated token and never accepted as a
request parameter** — no `operator` field in `AdmitJobRequest`, no `X-Operator` header.

**Where owner lives.** `JobRecord.owner: OperatorId | None`, set once at admission, immutable
(frozen dataclass makes "never reassigned" structural). Serialization via `asdict` carries a
`NewType` str without change.

**Migration of existing jobs.** Because `decode_job` is field-explicit and fail-closed:

- *M1 — optional field, legacy `None`.* Decode `owner` with an `_optional_*`-style read;
  pre-change records load with `owner=None`. No rewrite, no boot failure. Semantics of `None`
  are decided per visibility model (2d): invisible in scoped listings, still visible to
  startup reconcile (which must keep seeing *all* jobs).
- *M2 — startup backfill to a configured "legacy" operator.* Rewriting `job.json` at boot is
  only safe under reconcile's "no worker is alive" justification, and it invents a fictional
  owner. Worse honesty for marginal gain.
- *M3 — reject (new required field).* Fail-closed but brutal: yesterday's three-hour
  transcription bricks today's boot.

**Recommendation: M1.** Verified mitigation: no `data/` exists in this checkout, so the cost
is confined to operators' deployment machines and the change is still cheap.

### 2b. Authentication: static per-operator tokens

**Options.**

- *A1 — env-driven token map in `Settings`* (e.g. `ONEVOICECUT_OPERATOR_TOKENS` as JSON, or a
  file path setting pointing at a secrets file). Consistent with `settings.py` being the only
  env reader and with `.gitignore`'s `.env` + `!.env.example` convention. Rotation = edit env
  + restart.
- *A2 — token file at a fixed config path.* Adds a second secret store the repo does not have
  today; no precedent.
- *A3 — JWT/OIDC/sessions.* Out of proportion for a shared local server with a handful of
  operators. The seam should still let this swap in later.

**Recommendation: A1.** Fail-closed: with zero operators configured the composition root
refuses to boot (mirrors `require_binaries()` and `engine_resolver` fail-fast precedent —
"a missing key fails fast before a three-hour run starts").

**Where resolution happens.** The router deliberately avoids FastAPI `Depends`. Two faithful
options: (i) inject an `authenticator` callable into `WebDependencies` and have each handler
call a shared `_authorized(request, deps)` helper first; (ii) ASGI middleware writing
`request.state.operator`. **Recommendation: (i)** — matches the closure style, keeps the
401 path testable through the existing in-process ASGI transport tests, and lets a
parametrized structural test prove every route calls it (skill hard rule: authorization
enforced by a test, not a document). Middleware is the runner-up; it is weaker at returning
typed domain errors.

**Semantics and discipline.**

- Missing/invalid token → **401**; authenticated-but-not-owner → **403 or 404** depending on
  visibility model (2c). The 401/403 distinction is part of the frontend contract.
- Token comparison in constant time (`hmac.compare_digest`). Auth failures must not reveal
  which operator names exist, mirroring `JobNotFound`'s id-enumeration posture.
- Tokens follow the `engine_resolver` secret precedent: read at the composition root, never
  enter `JobRecord`, logs, or worker argv; only the operator *name* is persisted.
- Rotation: single slot per operator first; a two-slot (current/next) scheme is a design-phase
  option, not a launch requirement.

**What the worker needs: nothing (verified).** `worker.py` reads no credentials, takes no
operator argument, and its argv stays `--job-id`/`--data-dir`. Identity adds nothing to the
worker's job: it loads a record that already carries `owner`. The trust boundary this change
defends is HTTP; local-process trust of `data_dir` is unchanged from today.

### 2c. Authorization: where checks live, error types, BOLA surface

**Location.** Hexagonal rules (enforced by `tests/test_architecture.py`) put *policy* in use
cases and *identity resolution* in the web adapter:

- Web adapter: authenticate (token → `OperatorId`), translate domain errors to HTTP. It holds
  no policy.
- Use cases: `admit_job(..., operator)` records owner; the future cancel path and any purge
  caller receive `operator` and raise on mismatch. This mirrors the existing pattern where
  `admit_job` takes `capabilities` and raises `DiarizationUnsupported`.

**New domain error.** `JobNotOwned(DomainError)` in `domain/errors.py`. HTTP mapping is
model-dependent (see 2d): strict isolation maps it to **404** (existence of another
operator's job is itself information), shared visibility maps it to **403** (existence is
already public via the list; mutation is what's denied). One domain type, adapter-level
mapping decided by the configured model — the domain stays model-agnostic.

**BOLA surface (OWASP API1) — every route, current and planned:**

| Route | BOLA exposure | Required check |
| --- | --- | --- |
| `POST /api/jobs` | None once authenticated: ids are server-minted, owner = caller. | 401 only. |
| `PUT /api/jobs/{id}/media` | **Highest**: without a check, operator B uploads bytes into operator A's job and spawns a worker on A's machine slot. | Owner check before write and spawn. |
| `GET /api/jobs/{id}` | Read exposure, model-dependent. | Strict: owner or 404. Shared: any authenticated operator. |
| `GET /api/jobs` (new) | The point of the scoping work. | Scoped by model (2d). |
| Cancel route (new, port exists) | Mutating. | Owner-only under both models. |
| Purge (`purge_job_artifacts` is an uncalled seam today) | Mutating; no route exists yet. | Owner-only when it lands; ownership param should be on the request type now to avoid surgery later. |

**Idempotency/consistency.** Ownership is written once at admission and never mutated, so a
check-at-load is stable — except where the capacity gate (2e) adds new web-side state
transitions; those never rewrite `owner`.

### 2d. Visibility models — OPEN PRODUCT DECISION (analyzed, not resolved)

**V1 — strict per-operator isolation.**

- List route returns only the caller's jobs; `GET` of a foreign job → 404; cancel/purge of a
  foreign job → 404.
- Port impact: `list_jobs()` **must stay unscoped** — `reconcile_interrupted_jobs` needs all
  jobs, including legacy `owner=None` ones. Add a narrow `list_jobs_for(owner: OperatorId)`
  for the web route; the fake and filesystem adapter each gain one method.
- Legacy (`owner=None`) jobs: invisible to every scoped list; reconcile still handles them.
  They become orphans the UI can never reach — acceptable for a handful of pre-change jobs,
  must be documented.

**V2 — shared visibility, owner-only mutation.**

- Every authenticated operator sees every job (list carries `owner`; `JobStatusResponse` and
  list items gain an `owner` field — additive, frontend-friendly). Upload/cancel/purge stay
  owner-only → 403.
- Port impact: the web route can use unscoped `list_jobs()`; an optional "mine only" filter
  must be a boolean resolved server-side to the caller's id (never `?operator=<id>` — tenant
  identity never travels as a request parameter).
- Legacy jobs: visible to all, mutable by none (a "first operator claims it" rule is a
  product decision, flagged, not recommended silently).

**Consequences side by side:**

| Concern | V1 strict | V2 shared |
| --- | --- | --- |
| Port | + `list_jobs_for(owner)` | unchanged (flag-filter at route) |
| `GET /api/jobs/{id}` foreign | 404 | 200 (read-only) |
| Cancel/purge foreign | 404 | 403 |
| Legacy jobs | invisible everywhere | visible, immutable |
| Response shape | `owner` optional in responses | `owner` required in list/status |
| Team workflow | coordination pushed to chat | shared pipeline board falls out naturally |

**Recommendation to carry into propose (maintainer to confirm): V2 as default.** Rationale:
the premise is one ministry team cutting the same church's sermons — collaborators genuinely
need "is Sunday's sermon done?"; strict isolation on a shared server creates N silos while
the sensitive surface (mutation, machine slots) is already owner-protected. V1 remains a
strictly tighter configuration reachable behind a setting later if operators ever span
different churches; both models share the same auth + owner plumbing, so the decision is
cheap to revisit. **This remains a product decision — propose must surface it, not bury it.**

### 2e. Capacity gate: `max_concurrent_jobs` + queueing

Today: `PUT .../media` → `start_job` → unbounded `Popen`. With several operators this means
unbounded multi-hour processes on one machine.

- *C1 — admit-time reject (429/503).* Multi-hour jobs make a rejection a possibly day-long
  "come back"; hostile UX. Rejected.
- *C2 — persisted QUEUED state + derived drain.* Recommended. Details:

**State.** Add `JobState.QUEUED` (admitted + media present, waiting for a worker slot).
`PENDING` keeps its meaning (admitted, media not yet uploaded) — conflating "awaiting upload"
with "awaiting slot" would corrupt progress display and reconcile semantics.

**Where the gate sits.** Capacity is composition/runtime policy, not domain policy — the
domain gains only the state value, mirroring how reconcile policy lives in `runtime/app.py`
while `INTERRUPTED` lives in the domain. The upload route asks the gate; the gate either
spawns (today's behavior) or marks `QUEUED` and returns. `admit_job` stays unaware.

**Drain must be derived, never a counter** (the repo's strongest invariant). "Active workers"
is derived exactly the way reconcile already derives it: scan `list_jobs()` for worker-bound
states and test `process_is_alive(worker_pid)`. Slot free ⇔ derived active < `max_concurrent_jobs`.
No in-memory counter survives a web restart; persisted `QUEUED` plus a derived count does.

**Who drains.** The web process is the only long-lived process (workers die per job), so a
supervisor task in the lifespan (started in `build_app`) sweeps every few seconds: derive
active count, spawn oldest `QUEUED` jobs until slots are full. On web restart, `QUEUED`
survives on disk and the drain resumes — consistent with the crash-is-normal posture.
Piggyback drains on requests can be added later; the supervisor alone is sufficient.

**Race discipline.** The sweep must re-read the record at spawn time (it may have been
cancelled while queued). Worst residual race — spawn wins against a concurrent cancel — is
contained: `cancellation_requested` is polled before the first chunk, so the worker cancels
with zero work done.

**Reconcile interaction.** `QUEUED` has no `worker_pid`, needs no reconciliation, and must be
explicitly excluded from any extended worker-state reconcile (2f). Note reconcile today
inspects only `TRANSCRIBING` (see risks).

**Default cap.** Design-phase question (U3): 1 or 2. Local ASR saturates a machine; cloud
jobs barely use it. A per-engine slot policy is tempting scope creep — recommend a single
global cap first.

### 2f. Pid-reuse hardening

- *P1 — keep `os.kill(pid, 0)` as-is.* Zero cost, documented risk. More processes + longer
  uptime under multi-operator widens the window slightly.
- *P2-lite — worker heartbeat file (recommended).* Worker touches
  `jobs/{ulid}/heartbeat` at start and at every chunk boundary; reconcile and drain sweeps
  treat alive = pid alive **and** heartbeat fresh. Pure filesystem, no dependency, derived
  (a file on disk, not a counter), survives restarts, written only by the worker so the
  single-writer rule is intact (it is the worker's own job directory). Coarse granularity
  (chunk boundaries can be ~10 min apart) is fine for restart reconcile, which is the actual
  pid-reuse case; a timer thread is unnecessary complexity.
- *P3 — creation-time match (psutil or platform APIs).* New dependency for a single-machine
  app that hand-rolls a 40-line ULID to avoid one. Disproportionate.
- *P4 — keep the `Popen` handle in the web process.* Works only while the web process lives;
  the reconcile case is precisely a web restart. Insufficient alone.

**Recommendation: P2-lite**, plus extending reconcile from `TRANSCRIBING`-only to all
worker-bound states (`EXTRACTING`, `PLANNED`, `TRANSCRIBING`, `STITCHING`, `GENERATING`) —
the current gap orphans jobs that died mid-extraction, and a shared job list makes stuck jobs
visible to every operator.

---

## 3. Invariants this change must NOT touch

1. **Single-writer rule** — the worker is sole writer of `job.json` while alive; reconcile is
   the only other writer, justified by liveness. The capacity gate spawns and derives; it must
   not write `job.json` after a worker starts (QUEUED→EXTRACTING stays the worker's write).
2. **Atomic `save_chunk_result`** (`.tmp` + fsync + `os.replace`) — resume is built on it.
3. **Chunk-local ASR times** — untouched; identity never enters the transcription boundary.
4. **Capability refusal** (`DiarizationUnsupported` before any storage touch in `admit_job`) —
   ordering preserved when `operator` is added.
5. **Progress derived on read, never a counter** — the capacity gate must follow suit
   (derived active count, persisted QUEUED; no in-memory counters).
6. **Secrets at the composition root only**; never in `JobRecord`, logs, or worker argv —
   operator tokens follow the identical discipline (only operator *names* are persisted).
7. **Id-enumeration denial** — malformed id = unknown id = 404; extended to foreign jobs
   under strict isolation.
8. **Upload mechanics** — raw-body streaming, percent-encoded `X-Filename`, `.part` rename
   commit, extensionless `source`, no `UploadFile`/`File`/`Form` (structural test enforces).
   Auth wraps the routes; it must not restructure them.
9. **Hexagonal boundary** — `tests/test_architecture.py` unchanged and green: new use-case
   code imports domain+ports only; auth resolution lives in the web adapter/runtime.
10. **`FilesystemTranscriptStorage` and `data_dir`** — surviving by decision; no DB adapter.
11. **List-form, ULID-validated worker argv** — unchanged; no identity added to it.
12. **Docs premise statements** that become false (README "Single operator, runs locally.",
    CLAUDE.md intro, `process_is_alive`'s "single-operator machine" rationale) — the change
    must update them rather than silently contradict them.

---

## 4. Risks

| # | Severity | Risk | Mitigation |
| --- | --- | --- | --- |
| R1 | High | Field-explicit `decode_job`: a *required* `owner` turns every pre-change `job.json` into `CorruptedRecord`, and `list_jobs` raises → boot failure on operator machines with existing data. | Optional-field decode (M1); legacy `None` semantics specified per visibility model. |
| R2 | High | Route-by-route auth invites an unprotected route (BFLA, OWASP API5). | Deny-by-default wiring + parametrized per-endpoint test (no token → 401) generated from the route table, per skill hard rule 20. |
| R3 | High | BOLA window between slices: identity without authorization leaves `PUT .../media` cross-operator mutable. | Land owner recording + ownership checks on existing routes in the same slice as auth; never ship an authenticated-but-unauthorized intermediate. |
| R4 | Medium | Drain check-then-act race (spawn vs cancel of a QUEUED job); supervisor latency after worker exit. | Re-read record at spawn time; accept chunk-boundary cancel as containment; poll interval in seconds. |
| R5 | Medium | Reconcile covers only `TRANSCRIBING`; jobs dead in `EXTRACTING`/`PLANNED`/`STITCHING`/`GENERATING` are pre-existing orphans, now visible to every operator via the list route. | Extend reconcile to worker-bound states in the hardening slice, or explicitly document the gap. |
| R6 | Medium | `.gitignore` does **not** cover `data/` (verified: absent) — default `ONEVOICECUT_DATA_DIR=.\data` puts `job.json` (now carrying operator names) and church transcripts on a committable path. Media extensions are covered; records are not. | One-line `.gitignore` delta (`data/`) in the first slice; pre-existing but adjacent. |
| R7 | Low | Pid-reuse window grows with more processes/restarts. | P2-lite heartbeat (2f). |
| R8 | Low | Response-shape drift for the future frontend. | `owner` added as an additive field; 401/403 semantics fixed early; error codes kept stable. |
| R9 | Low | Token-map env ergonomics on Windows PowerShell (JSON quoting in `setx`/`$env:`). | Design phase picks format (JSON string vs `name:token` pairs vs `ONEVOICECUT_OPERATORS_FILE` path outside `data/`). |

## 5. Unknowns the design phase must resolve

- **U1 (product):** visibility model V1 vs V2 — open by instruction; explore recommends V2 as
  default and requires propose to surface the decision losslessly.
- **U2:** legacy (`owner=None`) job semantics under the chosen model — invisible/orphaned
  (V1) vs visible-immutable or claimable (V2).
- **U3:** `max_concurrent_jobs` default (1 vs 2) and whether the cap is global or per-engine.
- **U4:** token-map configuration format (env JSON vs file path).
- **U5:** whether this change lands the missing HTTP cancel route (port exists, route absent)
  — ownership checks "on cancel" imply yes, but it is scope the maintainer should confirm.
- **U6:** whether responses need stable machine error codes now (frontend contract) beyond
  HTTP status + `detail`.

## 6. Proposed slice decomposition (stacked, ≤800 review lines each)

Repo measurement: tests dominate (61–81% of diffs); estimates overran 3.2x–5.1x historically,
so each unit below is sized to split further if the measurement exceeds budget. Strict TDD:
RED before GREEN in every unit.

1. **S1 — Identity in domain + storage.** `OperatorId` type + validation; `JobRecord.owner`
   (optional); serialization optional-decode with legacy `None` round-trip tests; fake updated;
   `.gitignore` `data/` delta (R6). No behavior change on any route.
2. **S2 — Token authentication (401 layer).** `Settings` operator token map; authenticator
   built at composition root; fail-closed boot when unconfigured; 401 on all three existing
   routes; parametrized no-token/unknown-token tests. No authorization yet — but no
   cross-operator exposure changes either, since nothing is owner-aware.
3. **S3 — Ownership + authorization on existing routes.** `admit_job` records owner;
   `JobNotOwned` domain error; ownership check on `PUT .../media` and on `GET` per the chosen
   model's mapping (403/404); parametrized owner/foreign tests per route. *(R3: S2 and S3 may
   merge if they measure small; never ship S2-without-S3 across a PR boundary.)*
4. **S4 — Scoped job listing.** Port method per chosen model (`list_jobs_for` for V1, or
   flag-filtered unscoped list + `mine` filter for V2); `GET /api/jobs` route + response
   schemas (additive `owner`); parametrized BOLA tests; fake + filesystem implementations.
5. **S5 — Capacity gate.** `JobState.QUEUED`; `Settings.max_concurrent_jobs`; gated starter;
   derived active-count; lifespan drain supervisor; tests with fake launcher + fake liveness
   probe; QUEUED/cancel race coverage.
6. **S6 — Hardening + docs.** Worker heartbeat file; reconcile extended to worker-bound
   states; `process_is_alive` docstring rationale updated; README/CLAUDE.md premise deltas.

## 7. Sources read

`domain/jobs.py`, `domain/ids.py`, `domain/errors.py`, `ports/transcript_storage.py`,
`usecases/admit_job.py`, `usecases/transcribe_job.py`, `usecases/resume_job.py`,
`usecases/purge_job_artifacts.py`, `adapters/web/app.py`, `adapters/web/routers/jobs.py`,
`adapters/web/schemas.py`, `adapters/storage/filesystem_transcript_storage.py`,
`adapters/storage/serialization.py`, `runtime/app.py`, `runtime/settings.py`,
`runtime/worker.py`, `runtime/engine_resolver.py`, `tests/fakes/transcript_storage.py`,
`tests/unit/adapters/web/conftest.py`, `tests/unit/runtime/test_app_composition.py`,
`tests/test_architecture.py` (referenced), `pytest.ini`, `openspec/config.yaml`, `.gitignore`,
`README.md`, `CLAUDE.md`. Skill: `backend-architecture/SKILL.md` + references `security.md`,
`multi-tenancy.md`.
