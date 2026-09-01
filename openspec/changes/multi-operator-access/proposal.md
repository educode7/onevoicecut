# Proposal: multi-operator-access

SDD propose phase — 2026-09-01. Decision-dense artifact feeding `specs/` and `design.md`.
Grounded in `explore.md` (same directory) and code-verified against `main` at write time.
RFC 2119 keywords are normative.

---

## 1. Why

OneVoiceCut's documented premise is "single operator, runs locally" (README.md, CLAUDE.md).
The maintainer has settled a different deployment: **several operators against ONE shared
server**. Today the HTTP surface has zero authentication: anyone who can reach the port can
admit jobs, upload multi-hour media into *any* job, and spawn unbounded worker processes on
the shared machine. Two consequences follow directly:

1. **Security.** Every route is an OWASP API1/API5 surface: there is no identity, so there
   can be no ownership check. The moment a second operator exists, `PUT /api/jobs/{id}/media`
   lets operator B overwrite operator A's upload and consume A's machine slot.
2. **Capacity.** `PUT .../media` spawns one unbounded `Popen` per upload. Several operators
   on one machine means several concurrent multi-hour transcription processes with no cap and
   no queue — a fairness and stability failure, not a theoretical one.

This change makes the shared server honest: authenticated operators, owner-attributed jobs,
owner-only mutation, shared read visibility, and a persisted capacity gate.

## 2. What (scope)

Backend-only work in the existing hexagonal structure:

- **Identity**: `OperatorId` in the domain; `owner` on `JobRecord`, written once at admission.
- **Authentication**: static per-operator token map in `Settings`; fail-closed boot; 401 on
  every route.
- **Authorization**: ownership checks in the use cases; `JobNotOwned` domain error; 403 for
  non-owner mutation.
- **Shared listing**: `GET /api/jobs` returning all jobs with owner attribution; additive
  `owner` field on status responses.
- **Cancellation**: `POST /api/jobs/{id}/cancel`, owner-only, backed by the existing
  `request_cancellation`/`cancellation_requested` port seam.
- **Capacity gate**: persisted `JobState.QUEUED`, `max_concurrent_jobs` setting, lifespan
  drain supervisor whose active count is *derived*, never counted.
- **Hardening**: worker heartbeat file; reconcile extended beyond `TRANSCRIBING`.
- **Docs**: every documented premise this change falsifies MUST be updated (§11).

## 3. Non-goals

- **Angular frontend** — comes later; this change only keeps the API frontend-friendly
  (stable JSON shapes, clean 401/403 semantics).
- **Distributed deployment** — one shared server, settled. No multi-machine coordination.
- **Storage replacement** — `FilesystemTranscriptStorage` and the local `data_dir` survive.
  No DB, no object storage, no new storage adapter.
- **Roles / admin hierarchy** — no RBAC, no admin endpoints, no privilege levels. Operators
  are peers; the only policy is ownership.
- **Token self-service UI** — no signup, no token issuance endpoint, no password flows.
  Tokens are operator-managed configuration.

## 4. Confirmed decisions (maintainer handoff — closed)

1. **Deployment**: several operators against ONE shared server. Not distributed. Settled.
2. **Backend-only** Python/FastAPI for this change. The Angular frontend comes later and is
   OUT OF SCOPE; API contracts MUST stay frontend-friendly (stable JSON, clean 401/403).
3. **Visibility model (U1 resolved): V2 — shared visibility with owner-only mutation.**
   Every authenticated operator sees every job read-only: the list returns all jobs with
   owner attribution, and `GET` of any job returns it. Mutations (media upload, cancel,
   purge) are owner-only. Non-owner mutation → authorization error (403, §5c).
4. **Cancel route (U5 resolved): IN SCOPE.** This change adds `POST /api/jobs/{id}/cancel`
   backed by the existing port methods (`request_cancellation` / `cancellation_requested`,
   polled by the worker at chunk boundaries) with ownership verification.
5. **Research**: no lane selected; explore found no external-evidence gap.
6. **Storage**: `FilesystemTranscriptStorage` and the local `data_dir` survive. No DB, no
   object storage, no new storage adapter.
7. **Dependencies**: venv + pip remains the recorded dependency manager
   (`openspec/config.yaml`). This change requires **NO new third-party dependencies** —
   constant-time comparison and token handling are stdlib (`hmac.compare_digest`,
   `secrets`); configuration stays on the already-present `pydantic-settings`. No `pip
   install` is owed by any slice. Any future deviation from "none" MUST be justified
   explicitly in design.

## 5. Recommended approach (per workstream, grounded in explore §2)

### 5a. Identity — `OperatorId`, owner on `JobRecord`, optional-field decode

- `OperatorId = NewType("OperatorId", str)` in `domain/ids.py` with a `make_operator_id`
  validator (lowercase `[a-z0-9_-]{1,64}` — readable in config and logs, validated next to
  the existing ULID discipline). Operators are *configured principals*, not server-minted
  entities, so ULIDs are rejected for them.
- `JobRecord.owner: OperatorId | None`, set once at admission, immutable (frozen dataclass
  makes "never reassigned" structural).
- **The operator identity MUST be derived from the authenticated token and NEVER accepted as
  a request parameter** — no `operator` field in `AdmitJobRequest`, no `X-Operator` header,
  no `?operator=` query (multi-tenancy hard rule).
- **Migration (M1, verified)**: `owner` MUST decode as **key-tolerant optional** — absent key
  or `null` ⇒ `None`. Two code-verified facts make this non-negotiable:
  1. `serialization.decode_job` is field-explicit: a *required* `owner` turns every
     pre-change `job.json` into `CorruptedRecord`, and `list_jobs` re-raises
     `CorruptedRecord` rather than skipping it — startup reconcile would **brick boot on any
     existing data directory** (risk R1).
  2. The existing `_optional_*` helpers still require the key to be *present*. Legacy
     records lack the key entirely, so decode needs one new absent-tolerant read — design
     MUST specify it (absent ⇒ `None`, `null` ⇒ `None`, string ⇒ validated `OperatorId`).
- No boot-time backfill, no fictional legacy owner. Legacy `owner=None` semantics are
  deferred to design (U2) within the confirmed V2 frame.
- The worker needs nothing identity-related (verified): it loads a record that already
  carries `owner`; its argv stays `--job-id` / `--data-dir`.

### 5b. Authentication — static token map, closure-wired, fail-closed

- Token map lives in `Settings` (`ONEVOICECUT_` env prefix — `runtime/settings.py` stays
  the only env reader). Format and rotation ergonomics are deferred to design (U4).
- The composition root builds an **authenticator** and injects it into `WebDependencies`;
  handlers resolve the caller through a shared helper before any work. This preserves the
  router's deliberate closure style — **the router avoids FastAPI `Depends` today and MUST
  keep avoiding it**. ASGI middleware is the rejected runner-up (weaker at returning typed
  domain errors, breaks the established wiring precedent).
- **Fail-closed at boot**: with zero operators configured the composition root MUST refuse
  to start, mirroring the `require_binaries()` / engine-resolver fail-fast precedent.
  Deny-by-default: a route with no explicit authorization handling is closed, not open.
- Missing/invalid token → **401**. Token comparison in constant time
  (`hmac.compare_digest`); auth failures MUST NOT reveal which operator names exist,
  extending the existing id-enumeration posture.
- **Secret discipline**: tokens are read at the composition root only and MUST never enter
  `JobRecord`, logs, or worker argv. Only operator *names* are persisted — identical to the
  engine-secret precedent.

### 5c. Authorization — ownership in the use cases, `JobNotOwned`, BOLA review

Hexagonal rule (enforced by `tests/test_architecture.py`): **policy in use cases, identity
resolution in the web adapter.**

- `admit_job(..., operator)` records the owner. Mutating use-case paths (upload's
  pre-write/pre-spawn check, cancel, and purge) receive `operator` and raise on mismatch.
- New domain error `JobNotOwned(DomainError)` in `domain/errors.py`. The domain stays
  visibility-model-agnostic; the HTTP mapping is adapter-level. Under the confirmed V2,
  `JobNotOwned` MUST map to **403** (existence of foreign jobs is already public via the
  list; mutation is what is denied).
- `purge_job_artifacts` is an uncalled seam today with no route; its signature SHOULD gain
  the operator parameter now so a future route needs no surgery.
- **BOLA review of every route** (OWASP API1), per explore §2c:

| Route | Required check under V2 |
| --- | --- |
| `POST /api/jobs` | 401 only — ids server-minted, owner = caller. |
| `PUT /api/jobs/{id}/media` | Owner check **before** write and spawn — highest BOLA exposure. |
| `GET /api/jobs/{id}` | Any authenticated operator (V2 shared read). |
| `GET /api/jobs` (new) | Any authenticated operator; full list with owner attribution. |
| `POST /api/jobs/{id}/cancel` (new) | Owner-only → 403 for non-owners. |
| Purge (route-less seam) | Owner-only when it lands. |

### 5d. V2 visibility consequences on listing

- `list_jobs()` **stays unscoped** — `reconcile_interrupted_jobs` depends on seeing *all*
  jobs, including legacy `owner=None` ones. No new port method is needed under V2.
- `GET /api/jobs` uses the unscoped list; an optional "mine only" filter MUST be a boolean
  resolved server-side to the caller's id — never an operator-id request parameter.
- Response shapes gain `owner` **additively** (list items and `JobStatusResponse`) —
  frontend-friendly, no breaking change. Legacy jobs surface with `owner: null`.

### 5e. Capacity gate — persisted `QUEUED` + derived drain

- New `JobState.QUEUED`: admitted **and media present**, waiting for a worker slot.
  `PENDING` keeps its meaning (admitted, media not yet uploaded) — conflating "awaiting
  upload" with "awaiting slot" would corrupt progress display and reconcile semantics.
- Capacity is composition/runtime policy, not domain policy: the domain gains only the state
  value, mirroring how reconcile policy lives in `runtime/app.py` while `INTERRUPTED` lives
  in the domain. The gated starter either spawns (today's behavior) or persists `QUEUED`.
- **The active count MUST be derived, never counted** — the repo's strongest invariant
  ("progress is derived, never a counter"). Derived exactly as reconcile already derives:
  scan `list_jobs()` for worker-bound states and test process liveness. Slot free ⇔ derived
  active < `max_concurrent_jobs`. An in-memory counter dies with the web process; persisted
  `QUEUED` plus a derived count survives restarts.
- The web process is the only long-lived process, so a **lifespan drain supervisor**
  (started in `build_app`) sweeps every few seconds and spawns the oldest `QUEUED` jobs
  until slots are full. On web restart, `QUEUED` survives on disk and the drain resumes.
- Race discipline: the sweep MUST re-read the record at spawn time (it may have been
  cancelled while queued). The worst residual race — spawn wins against a concurrent cancel
  — is contained because `cancellation_requested` is polled before the first chunk, so the
  worker cancels with zero work done.
- `QUEUED` has no `worker_pid`, needs no reconciliation, and MUST be excluded from the
  extended worker-state reconcile (§5f).
- Default cap value (1 vs 2, global vs per-engine) is deferred to design (U3). A single
  global cap is the recommended starting point; per-engine slots are scope creep.

### 5f. Pid hardening — heartbeat + reconcile extension

- **Worker heartbeat file** (`jobs/{ulid}/heartbeat`), touched at worker start and at every
  chunk boundary. Reconcile and drain sweeps treat a worker as alive ⇔ pid alive **and**
  heartbeat fresh. Pure filesystem, no dependency, derived (a file on disk, not a counter),
  survives restarts, written only by the worker — single-writer rule intact. Coarse
  granularity (chunk boundaries can be ~10 min apart) is acceptable: restart reconcile is
  the actual pid-reuse case. No timer thread.
- **Reconcile MUST be extended from `TRANSCRIBING`-only to all worker-bound states**
  (`EXTRACTING`, `PLANNED`, `TRANSCRIBING`, `STITCHING`, `GENERATING`). The current gap
  orphans jobs that died mid-extraction (pre-existing risk R5), and a shared job list makes
  every stuck job visible to every operator — the gap stops being private.

## 6. Open unknowns — deferred to DESIGN (not resolved here)

- **U2 — legacy-job owner semantics.** Records with `owner=None` under V2: visible to all is
  settled by the model; their mutability (immutable forever vs first-operator-claims) is
  open. Reconcile MUST keep seeing them regardless.
- **U3 — `max_concurrent_jobs` default value** (1 vs 2) and whether the cap is global or
  per-engine.
- **U4 — token-map format + rotation ergonomics** (env JSON string vs `name:token` pairs vs
  a secrets-file path outside `data/`; Windows PowerShell quoting is a real ergonomic
  constraint — risk R9; single slot per operator vs two-slot current/next rotation).
- **U6 — machine/HTTP error-code mapping.** Whether responses need stable machine error
  codes now (frontend contract) beyond HTTP status + `detail`.

Resolved by the handoff and therefore closed: U1 (V2) and U5 (cancel in scope).

## 7. Surviving invariants — this change MUST NOT touch

1. **Single-writer rule** — the worker is sole writer of `job.json` while alive; reconcile
   is the only other writer, justified by liveness. The capacity gate spawns and derives; it
   MUST NOT write `job.json` after a worker starts (`QUEUED`→worker-bound stays the worker's
   write).
2. **Atomic `save_chunk_result`** (`.tmp` + fsync + `os.replace`) — resume is built on it.
3. **Chunk-local transcription times** — identity never enters the transcription boundary.
4. **Capability refusal / no silent degradation** — `DiarizationUnsupported` before any
   storage touch in `admit_job`; ordering preserved when `operator` is added.
5. **ULID validation before filesystem** — `make_job_id` at the door on every route naming
   a job; malformed id = unknown id = 404.
6. **List-form ffmpeg argv** — never `shell=True`, never string interpolation.
7. **Path containment** — `Path.resolve()` checked inside the job directory before any spawn.
8. **Percent-encoded `X-Filename`** — client filenames are metadata only, never path
   components.
9. **Commit-by-rename upload** — raw-body stream to a sibling `.part`, committed by rename.
10. **Extensionless source** (`jobs/{ulid}/source`); content type from `ffprobe`, never the
    extension; no `UploadFile`/`File`/`Form` anywhere in `adapters/web` (structural test).
11. **Progress derived on read, never a counter** — the capacity gate follows suit (§5e).
12. **Hexagonal boundary** — `tests/test_architecture.py` unchanged and green; new use-case
    code imports domain+ports only; identity resolution lives in the web adapter/runtime.
13. **Worker argv** — list-form, ULID-validated, no identity added.
14. **`FilesystemTranscriptStorage` and `data_dir`** — surviving by decision.

## 8. Risks (carried from explore §4, with mitigations)

| # | Sev | Risk | Mitigation |
| --- | --- | --- | --- |
| R1 | High | Field-explicit `decode_job`: a *required* `owner` turns every pre-change `job.json` into `CorruptedRecord`, and `list_jobs` re-raises → boot failure on operator machines with existing data. | Optional key-tolerant decode (§5a, M1); legacy `None` semantics specified in design (U2); round-trip tests for ownerless records. |
| **R2** | **High** | **Route-by-route auth invites an unprotected route (BFLA, OWASP API5).** | **Deny-by-default wiring + a parametrized per-endpoint 401 test generated from the route table, so a route added without auth handling fails the build the day it is created (skill hard rule: authorization enforced by a test, not a document). MANDATED, not optional.** |
| **R3** | **High** | **BOLA window between slices: authentication without authorization leaves `PUT .../media` cross-operator mutable.** | **Authentication and ownership checks MUST NEVER be split across a PR boundary. Slice S2 ships both as one review unit (§9). This is the single hardest constraint on slice ordering.** |
| R4 | Medium | Drain check-then-act race (spawn vs cancel of a QUEUED job); supervisor latency after worker exit. | Re-read record at spawn time; chunk-boundary cancel as containment; poll interval in seconds. |
| R5 | Medium | Reconcile covers only `TRANSCRIBING`; jobs dead in `EXTRACTING`/`PLANNED`/`STITCHING`/`GENERATING` are pre-existing orphans, now visible to every operator. | Extend reconcile to all worker-bound states in S6 (§5f). |
| R6 | Medium | `.gitignore` does not cover `data/` — default `data_dir` puts `job.json` (now carrying operator names) and church transcripts on a committable path. | One-line `data/` delta in S1; pre-existing but adjacent. |
| R7 | Low | Pid-reuse window grows with more processes/restarts. | Heartbeat file (§5f). |
| R8 | Low | Response-shape drift for the future frontend. | `owner` additive; 401/403 semantics fixed early; error shape stable pending U6. |
| R9 | Low | Token-map env ergonomics on Windows PowerShell (JSON quoting). | Design decides format (U4). |

## 9. Slice decomposition — 6 ordered slices, stacked-to-main

`delivery_strategy: auto-chain`, `chain_strategy: stacked-to-main`, review budget
**800 lines** (`openspec/config.yaml`; note CLAUDE.md still says 400 — doc drift owed in
S6). Repo measurement: estimates overrun ~4x (mean 4.0x, none under 3.2x) and tests
dominate 61–81% of diffs, so estimates below are planning numbers to be measured **before**
commit, and every slice except S2 MUST split the moment measurement exceeds budget. Strict
TDD: RED before GREEN in every unit.

1. **S1 — Identity in domain + storage.** `OperatorId` type + `make_operator_id`;
   `JobRecord.owner` optional; serialization absent-tolerant decode with legacy round-trip
   tests (ownerless record decodes `owner=None` and stays readable); fake storage updated;
   `.gitignore` `data/` delta (R6). No behavior change on any route.
2. **S2 — Token authentication + ownership authorization on existing routes (one unit, R3).**
   `Settings` operator token map; authenticator built at the composition root and injected
   into `WebDependencies` (closure style, no `Depends`); fail-closed boot when zero
   operators configured; 401 on all three existing routes via the parametrized per-endpoint
   test (R2); `admit_job` records owner; `JobNotOwned` domain error; ownership check on
   `PUT .../media` before write and spawn (non-owner → 403); parametrized owner/foreign
   tests per route. **Authentication and authorization MUST NOT be split across a PR
   boundary — if this slice exceeds budget when measured, reduce adjacent scope around it;
   do not ship 401 without 403.**
3. **S3 — Shared job listing (V2 visibility).** `GET /api/jobs` over the unscoped
   `list_jobs()`; additive `owner` on list items and `JobStatusResponse`; server-side
   `mine` boolean filter; parametrized BOLA tests (foreign jobs visible read-only, owner
   attribution present). No new port method under V2.
4. **S4 — Cancel route.** `POST /api/jobs/{id}/cancel`, owner-only (`JobNotOwned` → 403);
   backed by `request_cancellation`; idempotent on already-cancelled/terminal jobs; worker
   exits 2 via the existing chunk-boundary poll; no single-writer violation (web writes
   `control.json`, never `job.json`).
5. **S5 — Capacity gate.** `JobState.QUEUED`; `Settings.max_concurrent_jobs`; gated starter;
   derived active count (`list_jobs()` + liveness, never a counter); lifespan drain
   supervisor; tests with fake launcher + fake liveness probe; QUEUED/cancel race coverage;
   QUEUED excluded from reconcile.
6. **S6 — Hardening + docs.** Worker heartbeat file; liveness = pid alive ∧ heartbeat
   fresh; reconcile extended to all worker-bound states; `process_is_alive` docstring
   rationale updated (its "single-operator machine" premise becomes false); README/CLAUDE.md
   premise deltas (§11).

Every slice MUST be independently green under
`.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"` **and**
`.venv\Scripts\python.exe -m mypy src tests`.

## 10. Rollback plan

Required by `openspec/config.yaml` for risky changes. Scenario: revert to the pre-change
build after multi-operator has been active and newer records exist on disk.

**Rollback invariant (explicit): reverting the code MUST NOT brick reading records written
while multi-operator was active.** This holds because decoding is field-explicit in both
directions (code-verified):

- Old `decode_job` reads exactly its known fields and **ignores unknown keys** — a
  `job.json` carrying the extra `owner` key decodes cleanly under pre-change code.
- New `decode_job` MUST treat `owner` as absent-tolerant optional — pre-change records
  (no `owner` key) decode with `owner=None` under post-change code. Forward and backward
  reads are both safe; neither direction may ever require the key.
- Persisted operator *names* in old-read records are inert; tokens themselves are never
  persisted, so no secret cleanup is owed by a rollback. Old `Settings` uses
  `extra="ignore"`, so leftover `ONEVOICECUT_OPERATOR_TOKENS` env is silently unread.
- `control.json` files written by the new cancel route remain valid: the worker polls them
  regardless of which code version wrote them.
- Heartbeat files are ignored by pre-change code (files inside a job directory are never
  enumerated by `list_jobs`).

**The one state that does not roll back transparently is `JobState.QUEUED`**: pre-change
`decode_job` maps `state` through the old enum, so `"queued"` is a `CorruptedRecord`, and
`list_jobs` re-raises → boot failure. Rollback procedure therefore:

1. Stop admitting new work; let the drain supervisor empty the queue (QUEUED → spawned →
   terminal states) before reverting.
2. If an urgent rollback cannot wait for multi-hour jobs to finish, move each QUEUED job
   directory out of `data_dir` (they are resumable-by-reupload after re-applying the
   change) — never hand-edit `job.json` state in place.
3. Revert the code, restart. Legacy and owner-attributed records both read cleanly.

## 11. Docs deltas (owed by this change)

README.md and CLAUDE.md both assert the single-operator premise; this change falsifies it
and MUST update rather than silently contradict it (surviving invariant on documented
premises):

1. **README.md** — "Single operator, runs locally." → multi-operator on one shared server.
2. **README.md "Running it"** — operator-token env configuration; the `Authorization`
   header in every example request; the two new routes (`GET /api/jobs`,
   `POST /api/jobs/{id}/cancel`); `max_concurrent_jobs` and QUEUED semantics.
3. **CLAUDE.md intro** — "A single-operator local app" → shared-server multi-operator.
4. **CLAUDE.md HTTP surface** — "Three HTTP routes exist" → five; auth/authz added to the
   security-invariants section (401 on every route, owner-only mutation, deny-by-default
   test).
5. **CLAUDE.md review budget** — still says "400 lines per slice" while
   `openspec/config.yaml` records 800 (raised 2026-08-31); fix the drift in S6.
6. **Code docstring** — `process_is_alive`'s "single-operator machine" rationale
   (S6, with the heartbeat work).

## 12. Dependencies

venv + pip remains the recorded Python dependency manager (`openspec/config.yaml` apply
guidelines). **This change adds NO third-party dependencies** — stdlib (`hmac`, `secrets`)
plus the already-present `fastapi`/`pydantic-settings` covers everything in scope. No slice
owes a `pip install`.

## 13. Success criteria

1. `.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"` green.
2. `.venv\Scripts\python.exe -m mypy src tests` green.
3. No default-run test calls a paid API or loads model weights — this is a success
   criterion, not a preference (pytest markers `paid`/`localmodel` excluded).
4. `tests/test_architecture.py` unchanged and green (hexagonal boundary intact).
5. Parametrized per-endpoint 401 test generated from the route table exists and fails on
   any route lacking authentication (R2 gate).
6. Fail-closed boot verified: zero configured operators → the server refuses to start.
7. Legacy round-trip verified: an ownerless `job.json` decodes with `owner=None`, and a
   record carrying `owner` is readable by the pre-change decode shape (rollback invariant).
