# Design: multi-operator-access

SDD design phase — 2026-09-01. Answers every unknown the proposal deferred (U2, U3, U4, U6)
and every risk the spec phase surfaced. Grounded in `proposal.md`, the seven `specs/*/spec.md`
(68 scenarios: AUTH, OWN, VIS, CXL, CAP, LEG, HARD), `explore.md` §3, and the code on `main`
at write time. RFC 2119 keywords are normative. Technical artifacts in English per the
language domain contract.

**How to read this:** §1 is the answer key — one line per decision. §2 is each decision with
rationale, rejected alternatives, and the scenarios it enables. §3–§6 are the structural
views implementers and reviewers check against. §7 traces all 68 scenarios to decisions.

---

## 1. Decision summary (answers first)

| # | Decision | One line |
| --- | --- | --- |
| D1 | Legacy owner semantics (U2) | `owner=None` jobs are visible to all and mutable by **nobody**; the uniform owner rule already denies them (403). No backfill, no custodian. |
| D2 | Capacity cap (U3) | `ONEVOICECUT_MAX_CONCURRENT_JOBS`, one global integer, default **1**, validated `>= 1` at boot; FIFO drain in ULID (creation) order. |
| D3 | Token map + rotation (U4) | `ONEVOICECUT_OPERATOR_TOKENS` = `name:token;name:token`; fail-closed boot validation; constant-time full-scan comparison; rotation = edit config + restart, and that is enough at this scale. |
| D4 | Error/HTTP mapping (U6) | All credential failures → one indistinguishable 401; non-owner mutation → 403; malformed/unknown id → 404; no machine error-code field now; worker exit codes unchanged. |
| D5 | Heartbeat freshness | `jobs/{ulid}/heartbeat` holds one epoch float, written only by the worker at start and at every chunk boundary; staleness bound **7200 s**; nobody removes it. |
| D6 | Reconcile extension (R5) | Reconcile covers all five worker-bound states; alive ⇔ pid alive **and** heartbeat fresh; a stale heartbeat vetoes a live pid; QUEUED excluded. |
| D7 | Drain supervisor | Lifespan task sweeping every **5 s**, the **single spawn decision point**; upload always persists QUEUED; re-read before spawn; in-memory spawned-awaiting-claim dedup set (not a counter). |
| D8 | Auth wiring | Authenticator is a **required** `WebDependencies` field (deny-by-default construction); every handler starts with one shared helper; a route-table-generated 401 test enforces it for future routes; use cases receive `OperatorId` as an argument. |
| D9 | Cancel mechanics | Per-state: unbound jobs (PENDING/QUEUED/INTERRUPTED) → web writes CANCELLED; worker-bound jobs → control file only; terminal jobs → 200 idempotent no-op with zero writes. |
| D10 | Serialization/migration | `owner` decodes absent-tolerant (`absent → None`, `null → None`, string → validated); encoding always writes the key; responses gain `owner` additively; listing is a wrapper object with a server-side `mine` boolean. |

---

## 2. Decisions

### D1 — Legacy owner semantics (closes U2; enables LEG-01..07, VIS-04, OWN-05)

**Decision.** A record with `owner=None` (any pre-change job) is readable by every
authenticated operator (V2, confirmed) and mutable by **nobody**. There is no special case
in the authorization code for this: the single rule `require_owner(job, operator)` raises
`JobNotOwned` whenever `job.owner != operator`, and `None` is never equal to an
`OperatorId`. A mutation attempt on a legacy job therefore answers **403**, exactly like a
foreign owned job.

**Decode rule (exact).** `owner` is the one key-tolerant read in `decode_job`:

- key **absent** → `None` (pre-change records);
- key present and `null` → `None`;
- key present, a string passing `make_operator_id` → that `OperatorId`;
- key present, anything else (non-string, or a string failing validation) →
  `CorruptedRecord` — fail closed, never coerced to `None` or an invented identity (LEG-04).

No boot-time backfill, no rewrite: legacy records keep `owner=None` until a real lifecycle
transition re-saves them, at which point encoding writes `"owner": null` (D10). LEG-01..05
and LEG-07 close on this rule; LEG-06 closes on reconcile continuing to see every record.

**Rationale.** V2's core is "read is shared, mutation is owner-only." Legacy jobs have no
owner, so the owner-only rule degenerates to "no mutation" with zero extra code. The policy
stays uniform and auditable: one ownership rule, one domain error, one HTTP mapping.

**Rejected alternatives.**

- *Any authenticated operator may mutate legacy jobs.* Contradicts the central invariant of
  this change (owner-only mutation) on exactly the records whose provenance is unknown, and
  creates a first-come race where two operators "claim" by uploading. The first operator to
  touch a legacy job would silently become its owner — inventing the fictional ownership
  LEG-07 forbids, by side effect.
- *A configured "legacy custodian" operator.* Introduces a privilege level: one operator
  who may do what peers may not. That is the first rung of the roles/admin hierarchy the
  proposal's non-goals explicitly exclude. Rejected on scope grounds, not implementation cost.

**Consequence (accepted).** Legacy jobs are read-only orphans until a retention policy
exists (the `purge_job_artifacts` seam is deliberately unanswered). They occupy disk and a
line in the shared list with `owner: null`; an operator may still move a job directory out
of `data_dir` by hand, exactly as today. That honesty beats inventing an owner.

### D2 — Capacity cap shape and default (closes U3; enables CAP-01..08)

**Decision.** `Settings.max_concurrent_jobs: int`, env var
`ONEVOICECUT_MAX_CONCURRENT_JOBS`, **default 1**, validated `>= 1` (pydantic field
constraint). A value below 1, or a non-integer, fails `Settings` construction at the
composition root — the server refuses to boot, same fail-closed posture as D3 and
`require_binaries()`. The cap is **one global integer**: not per-engine, not per-operator.

**Drain ordering: FIFO by creation.** `list_jobs()` returns records sorted by ULID
directory name, which is creation order by construction of the id scheme. The drain
therefore selects queued jobs oldest-first with no extra sort key and no timestamp
comparison (CAP-07).

**Startup with more runnable jobs than slots.** At boot, "runnable" splits two ways:

- *Worker-bound records with a live worker* (pid alive and heartbeat fresh) survived the
  web restart — workers are separate processes. They are not spawned again; they simply
  occupy derived slots (D6/D7).
- *QUEUED records* wait for a free slot. The drain supervisor sweeps them FIFO, spawning
  one per free slot per sweep, until the queue empties. Nobody bursts past the cap; the
  queue is the burst absorber (CAP-05, CAP-08).

Startup order is fixed: `require_binaries()` → extended reconcile (which converts dead
worker-bound records to INTERRUPTED, freeing their derived slots) → drain supervisor starts.
Reconcile-before-drain means a restart after a crash hands reclaimed slots to queued work
on the first sweep.

**Rationale for default 1.** The premise is multi-hour, CPU-bound local ASR on ONE shared
server; local transcription saturates a machine by itself. Two concurrent local jobs mostly
time-slice and make both slower, and a default of 2 would bake an unmeasured assumption
into configuration. Default 1 is the fairness-stable choice; operators with headroom raise
one env var. A global cap (rather than per-engine slots) follows the proposal: per-engine
drain logic is scope creep before a single measurement asks for it.

**Rejected alternatives.**

- *Default 2.* No measurement supports two simultaneous local-ASR jobs on the target
  machine; the default should not encode optimism. Trivially reversible by configuration.
- *Per-engine caps.* Requires engine-aware drain bookkeeping for a benefit nobody has
  demonstrated; the proposal names this scope creep and this design agrees.
- *Unbounded (status quo).* The failure this change exists to fix.
- *Admit-time rejection (429/503).* Already rejected in explore (C1): with multi-hour jobs
  a rejection is a possibly day-long "come back." The queue exists precisely so admission
  never refuses for capacity reasons (CAP-01).

### D3 — Token-map format, boot validation, comparison, rotation (closes U4; enables AUTH-01, 03, 05, 07, 08, 09)

**Decision — configuration surface.** One env var read by `Settings` (the only env reader):

```
ONEVOICECUT_OPERATOR_TOKENS=maria:oVc9kQ…;jose:7fTb2w…
```

Grammar: zero or more `name:token` pairs joined by `;`. Each pair splits at its **first**
`:`; surrounding whitespace per pair is stripped. Tokens therefore may contain `:` and any
character except `;`. Recommended token generation (documented, not enforced):
`secrets.token_urlsafe(32)`. The format is chosen for Windows PowerShell ergonomics (risk
R9): one quoted string, no nested JSON quoting, works identically in `$env:`, `set`, and a
`.env` file (`;` is not a comment character in dotenv parsing).

**Decision — boot validation (fail closed).** Parsing happens once, at the composition
root, in a pure function `parse_operator_tokens(raw: str | None) -> Mapping[OperatorId,
str]` that raises on any of:

| Malformed form | Outcome |
| --- | --- |
| Env var absent or empty → zero operators | Refuse boot (AUTH-07) |
| Pair with no `:` | Refuse boot (AUTH-08) |
| Empty name, or name failing `make_operator_id` (`[a-z0-9_-]{1,64}`) | Refuse boot (AUTH-08) |
| Empty token | Refuse boot (AUTH-08) |
| Duplicate operator name | Refuse boot (AUTH-08) |
| Duplicate token value | Refuse boot — two operators with one token make identity ambiguous (AUTH-08) |

Refusal propagates from app construction, mirroring the `require_binaries()` /
engine-resolver fail-fast precedent: no request-serving process comes up. Validation error
messages MAY name the offending operator name or pair position and MUST NOT contain any
token value (AUTH-09 discipline starts at boot).

**Decision — comparison discipline.** The authenticator parses the `Authorization` header
(scheme `Bearer`, case-insensitive; anything else is a credential failure), then scans
**every** configured pair with `hmac.compare_digest` on UTF-8 bytes and **never exits
early** on a match. Early exit would leak, by timing, how far the scan got — which pairs
the candidate token resembled. Full scan + per-comparison constant time removes the timing
channel over both token values and operator positions. Exactly one match resolves the
caller; zero matches fail. (Boot rejects duplicate tokens, so two matches cannot occur.)

**Decision — loggable identity.** The operator **name** is the identity: it is what is
persisted as `owner`, and the only thing that may appear in logs or responses. Token values
never enter `JobRecord`, logs, worker argv, or any response (AUTH-09) — identical to the
engine-secret precedent.

**Decision — rotation.** Rotation procedure is normatively: **edit the env value, restart
the web process.** At this scale — a handful of operators on one shared server — this is
sufficient and nothing heavier is warranted: the restart costs seconds, costs nothing
in-flight (workers are separate processes and keep running; QUEUED records persist on disk
and the drain resumes; reconciliation leaves live workers untouched), and a two-slot
current/next scheme would add parsing surface, state, and tests for a threat model that does
not exist here. If rotation frequency ever makes restarts painful, that is a new design
input, not today's.

**Rejected alternatives.** *JSON env value* (nested quoting is precisely risk R9); *a
secrets file path setting* (a second secret store the repo does not have — explore A2);
*JWT/OIDC* (out of proportion; the authenticator seam keeps that swappable later).

### D4 — Error and HTTP mapping (closes U6; enables AUTH-02..05, OWN-04, 08, 09, CXL-08)

**Decision — exact mapping table.**

| Situation | Status | Response body | Header |
| --- | --- | --- | --- |
| Missing `Authorization` header | 401 | `{"detail": "not authenticated"}` | `WWW-Authenticate: Bearer` |
| Malformed credential (unparsable header, wrong scheme) | 401 | **identical body** | `WWW-Authenticate: Bearer` |
| Token matching no operator | 401 | **identical body** | `WWW-Authenticate: Bearer` |
| Valid token, non-owner mutation (upload/cancel/purge) | 403 | `{"detail": "not the owner of this job"}` | — |
| Malformed or unknown job id on any job-naming route | 404 | `{"detail": "no such job"}` (existing wording) | — |
| Capability refusal at admission (existing) | 422 | unchanged | — |
| Oversized upload / unsupported container (existing) | 413 / 415 | unchanged | — |
| Cancel of a terminal job | 200 | no-op response (D9) | — |

Design decisions inside the table:

1. **Malformed-vs-missing credentials are indistinguishable to the client** (resolving
   AUTH-04's design dependency). One body for all three 401 causes: any distinction is an
   enumeration channel (AUTH-05), and the client needs exactly one bit — "authenticate."
2. **403 bodies leak nothing beyond what V2 already exposes.** Under V2 the existence of
   every job is public via the shared list, so a 403 on a named foreign id reveals nothing
   new; the detail therefore stays generic and does not name the owner. The id-enumeration
   posture (malformed = unknown = 404) survives intact, and the rule "never reveal which
   ids exist" now applies only where V2 has not already made existence public — i.e., it no
   longer justifies 404 for foreign-but-real ids. OWN-08 is explicit: denial comes from the
   ownership check, not identifier secrecy.
3. **Check precedence on job-naming mutating routes:** authenticate (401) → validate id
   shape (404) → load record (404) → ownership (403) → effect. Authentication precedes
   everything; no work happens without an identity (AUTH-02).
4. **No machine error-code field now** (resolving U6). The frontend contract is the status
   codes themselves: 401 (re-authenticate), 403 (wrong owner), 404 (no such job), plus the
   stable `detail` strings above. A `code` enum would have exactly the same discriminating
   power as the statuses today; adding it later is an additive response change, so nothing
   is foreclosed.
5. **Worker exit codes are unchanged** (0 ok / 1 failed / 2 cancelled / 3 unusable). The
   capacity gate acts before spawn, so no new worker outcome exists; cancellation keeps its
   existing exit path (CXL spec's U6 pointer closes here).

**Translation point.** The web adapter translates domain errors to HTTP, extending the
existing precedent in `routers/jobs.py` (`JobNotFound` → 404, `DiarizationUnsupported` →
422): `JobNotOwned` → 403, `InvalidCredential` (web-adapter error, D8) → 401. The domain
raises; the adapter maps; the mapping never leaks into use cases.

### D5 — Heartbeat file and freshness bound (enables HARD-01..06, CAP-06, LEG-09)

**Decision — file, location, content.** `jobs/{ulid}/heartbeat`, a text file containing a
single float: the epoch seconds of the last write, taken from the worker's injected clock.
The name joins the layout constants owned by `FilesystemTranscriptStorage`. Writing goes
through the same atomic discipline (`.tmp` + fsync + `os.replace`) as every other persisted
write: a torn heartbeat must not be readable as a fresh one.

**Decision — writers and cadence.** The worker is the **sole writer** (HARD-03): one write
in `run_job` immediately after claiming the job (HARD-01), and one write at every chunk
boundary — in `transcribe_job`'s loop, adjacent to the existing cancellation poll (HARD-02).
No timer thread: chunk-boundary granularity is what the spec mandates and what the guarded
case needs. The web process, the capacity gate, and reconcile never write the file.

**Decision — freshness bound: 7200 s (2 hours), as a runtime constant.** Alive-at-boundary
writes mean the longest gap between heartbeats is the longest stretch between boundaries:
one chunk's transcription — target 600 s, bounded by the 30-minute per-chunk timeout, up to
three attempts where slowness rather than timeout burns the budget (worst case ≈ 90 min) —
or the extraction phase preceding the first boundary (comparable magnitude on multi-hour
input). 7200 s covers the worst case with margin while keeping the hung-worker window
bounded. The bound is a named constant (`HEARTBEAT_STALE_AFTER_S`), not configuration: it
is a design property of the liveness rule, not an operator preference.

**Decision — who removes it: nobody.** The file stays after completion, cancellation, or
crash. No consumer checks liveness outside worker-bound states, so a leftover heartbeat is
inert; absence or unparseable content reads as *not fresh* (fail closed). Removal would add
a writer-and-cleaner pair for zero correctness gain, and LEG-09 requires the file to be
harmless to pre-change builds — files inside a job directory are never enumerated as jobs.

**Decision — liveness rule (single definition).** A job's worker is alive iff:
`worker_pid is not None` **and** `is_alive(worker_pid)` **and**
`heartbeat_is_fresh(job_id, now, HEARTBEAT_STALE_AFTER_S)`. One shared helper
(`worker_is_alive`) serves both reconcile and the capacity derivation, exactly as
`_validate_compatibility` is the single definition of engine compatibility. Freshness
checks against the injected clock keep tests deterministic (no wall-clock sleeps in the
default run).

### D6 — Reconcile extension (closes R5; enables HARD-07..09, CAP-06, 12, LEG-06)

**Decision — scope.** Reconcile covers the state set `WORKER_BOUND_STATES` =
{EXTRACTING, PLANNED, TRANSCRIBING, STITCHING, GENERATING}, defined **once in
`domain/jobs.py`** and consumed by reconcile, the capacity derivation, and the cancel
classification (D9) so the three cannot drift. The current TRANSCRIBING-only reconcile
orphans jobs that died mid-extraction or mid-stitch (pre-existing risk R5); under a shared
list those orphans are visible to every operator, so the gap stops being private.

**Decision — exclusions.** PENDING (no worker expected — the job awaits upload), QUEUED
(no worker pid by definition; the drain owns it — CAP-12 honored verbatim), and terminal
states (COMPLETED, FAILED, CANCELLED) are untouched (HARD-09). INTERRUPTED is not re-processed.

**Decision — liveness precedence.** The combined rule of D5 applies with explicit
precedence: a **stale heartbeat vetoes a live pid** (the pid-reuse case — HARD-05: the pid
belongs to somebody else's process, so the job is marked INTERRUPTED even though `os.kill
(pid, 0)` succeeds), and a **dead pid vetoes any heartbeat freshness** (HARD-06). Missing
pid on a worker-bound record reads as not alive. Reconcile remains startup-only and remains
the one non-worker writer of `job.json`, justified exactly as today: there is no live worker
behind the record it rewrites.

**Legacy records.** Reconcile processes owned and legacy (`owner=None`) worker-bound jobs
alike (LEG-06); it never reads `owner`.

### D7 — Drain supervisor placement and mechanics (enables CAP-01..12; mitigates R4)

**Decision — placement: a lifespan background task.** `build_app`'s lifespan starts one
asyncio supervisor after reconcile; it sweeps every `DRAIN_SWEEP_INTERVAL_S = 5.0` seconds
until shutdown cancels it. An exception inside one sweep is caught, reported to stderr, and
the loop continues: a dead supervisor would silently strand the queue, and the
crash-is-normal posture says the queue on disk is the truth the next sweep retries against.

**Decision — single spawn decision point.** Upload **never spawns**. On a successful
upload the web process persists `QUEUED` (its one gate write, before any spawn — CAP-11)
and returns 204; the supervisor is the only code that ever calls the launcher. Consequences:

- CAP-04 ("spawning never exceeds the cap") holds by construction: one decision point, one
  derivation, serialized in the event loop. (CAP-04's "at upload completion" decision point
  becomes the decision to queue; the only *spawn* decisions happen in sweeps.)
- The upload-vs-drain check-then-act race class disappears entirely — there are not two
  spawn paths to race each other.
- Cost: up to ~5 s between upload completion and worker start on an idle machine. Against
  multi-hour jobs this is noise, and QUEUED is an honest status to show in the shared list
  during that window.

**Decision — sweep mechanics (`drain_once`, the testable unit).** Each sweep:

1. `records = storage.list_jobs()` — the same unscoped listing reconcile uses.
2. **Derive active** = records in `WORKER_BOUND_STATES` passing `worker_is_alive` (D5/D6).
   Never a counter (CAP-05); dead workers free their slots immediately (CAP-06).
3. **Prune the dedup set** (below): drop ids whose record is now worker-bound or no longer
   QUEUED.
4. While `active < max_concurrent_jobs` and queued candidates remain (FIFO, D2): re-read
   the candidate with `load_job` — **re-read before spawn** (CAP-09). Skip it if it is no
   longer QUEUED (e.g., cancelled while queued — D9 wrote CANCELLED) or is in the dedup
   set. Otherwise launch `[sys.executable, -m, WORKER_MODULE, --job-id, …, --data-dir, …]`
   via the injectable launcher and add the id to the dedup set.

**Decision — the spawned-awaiting-claim dedup set.** Between the launcher call and the
worker's pid-claim write, the record still says QUEUED; the next sweep would otherwise
spawn it again — two workers on one job, a single-writer violation. The supervisor keeps an
in-memory `set[JobId]` of issued spawns not yet observed worker-bound, and never spawns an
id in it. This is **not** a worker counter: the active count stays fully derived (the
invariant is intact); the set only idempotizes spawn issuance within one web lifetime. On
web restart the set is empty and the records are the truth: claimed jobs read as
worker-bound, and a job whose worker died before claiming is correctly re-spawned.

**Decision — single-writer legitimacy argument (made explicit, per the invariant).** The
web process writes `job.json` for jobs with **no live worker**, and only for those. That is
precisely reconcile's existing legitimacy: a record asserting a worker-bound state with no
process behind it is a lie left by a crash, and the liveness check is what keeps the write
safe. The gate's QUEUED write and the cancel path's QUEUED→CANCELLED write (D9) stand on
the same ground — QUEUED has no `worker_pid` by construction. Once a worker starts, the
gate writes nothing more (CAP-11): the QUEUED→EXTRACTING transition is the worker's own
first claim-and-advance, exactly as PENDING→EXTRACTING is today.

**Decision — spawn-vs-cancel race resolution.** CAP-09 (cancel wins): the sweep's re-read
sees CANCELLED and skips. CAP-10 (spawn wins the microsecond race): the worker starts, and
two mechanisms contain it — `run_job` exits immediately if the record is already in a
terminal state (no claim, no work), and `transcribe_job` polls `cancellation_requested`
before the first chunk, so zero chunks are transcribed. QUEUED-cancellation therefore writes
the control file **as well as** the CANCELLED record (D9), so the containment holds by
either path. Residual, documented: extraction may run inside the microsecond race window;
the spec's guarantee is zero transcribed chunks, and that holds.

**Rejected alternatives.**

- *Sweep-on-request only.* The queue would drain only while humans send requests. A slot
  freed at 2 a.m. (normal, for multi-hour jobs) would sit cold until morning. Rejected.
- *Hybrid (lifespan task + request piggyback).* Two code paths for one decision, doubling
  the race surface for no latency benefit beyond the piggyback's. Rejected.
- *Upload spawns directly when a slot is free; drain only backfills.* Two spawn decision
  points: concurrent uploads could both derive "slot free" and both spawn (cap exceeded),
  and upload-vs-drain check-then-act returns. The ~5 s it saves is not worth a race class.
  Rejected.
- *In-memory queue or active counter.* Dies with the web process; violates "derived, never
  counted." Rejected by invariant 11.

### D8 — Auth wiring shape (enables AUTH-01, 02, 06; OWN-01, 07)

**Decision — authenticator as injected dependency, closure style.** The composition root
builds the authenticator from the parsed token map (D3) and passes it into
`WebDependencies` as a **required field with no default** (`authenticate: Callable[[str |
None], OperatorId]`, raising `InvalidCredential` on any failure). Deny-by-default is
structural at construction: `create_app` cannot be wired without an authenticator, and
there is no permissive default to fall back to (contrast `start_job`, whose
`no_job_starter` default refuses loudly — here, absence is a construction-time error).
FastAPI `Depends` remains unused; the router keeps its deliberate closure style.

**Decision — one shared resolution helper, first statement of every handler.**
`_authorized(request, deps) -> OperatorId` reads `request.headers["authorization"]`, calls
`deps.authenticate`, and translates `InvalidCredential` to the uniform 401 (D4). Every
route handler — existing and new — begins with it. A handler that skips it is caught by
the test below, not by review discipline.

**Decision — enforcement by test, not document (AUTH-06, risk R2).** The suite contains a
parametrized authentication check generated from the **registered route table**
(`app.routes` at test time), never from a hand-maintained list: for every route, an
unauthenticated well-formed request MUST answer 401. A future route registered without
`_authorized` fails the default run the day it is created. Known residual, documented:
FastAPI validates request bodies before the handler runs, so a syntactically invalid body
can draw a 422 ahead of the 401; no state changes in that case (nothing is admitted,
written, or spawned — the normative core of AUTH-02 holds), and the parametrized check uses
well-formed requests.

**Decision — identity resolution in the web adapter; use cases receive `OperatorId` as an
argument.** The web adapter is the only place a token or header exists. Use cases take the
resolved value: `admit_job(..., operator=operator)`, `cancel_job(..., operator=operator)`,
and the shared `require_owner(job, operator)` gate. The core never sees tokens, headers, or
strings-with-meaning — only the domain type. `OperatorId` and `JobNotOwned` are domain
symbols (`domain/ids.py`, `domain/errors.py`); the authenticator and `InvalidCredential`
are web-adapter symbols (`adapters/web/auth.py`). Import direction stays green:
`test_architecture.py` unchanged.

**Decision — client-supplied identity is discarded, not rejected.** `AdmitJobRequest` keeps
`extra="forbid"` for ordinary typos, plus a `model_validator(mode="before")` that removes
any client-supplied `operator` key before validation (OWN-07: admitting with
`"operator": "a"` in the body MUST record owner "b", the token-resolved caller — a 422
rejection would fail that scenario). The listing's `mine` filter is a boolean resolved
server-side; no route declares an operator-identity parameter anywhere (VIS-08, OWN-07).

### D9 — Cancel route mechanics (enables CXL-01..08, CAP-09, 10; mitigates R4)

**Decision — route and order.** `POST /api/jobs/{id}/cancel`. Handling order mirrors D4's
precedence: authenticate (401) → validate id / load record (404) → `require_owner` (403) →
state branch. The use case `cancel_job(job_id, *, operator, storage, now)` lives in
`usecases/`; the response is `200` with `CancelJobResponse(job_id, state)` where `state`
is the record's state at response time. One status for all branches keeps the frontend
contract flat; the worker-bound branch returns 200 immediately — the recording takes effect
without waiting for any boundary (CXL-01).

**Decision — per-state classification.** With `WORKER_BOUND_STATES` and `TERMINAL_STATES`
({COMPLETED, FAILED, CANCELLED}) defined once in the domain (D6):

| Record state | Action | Who writes | Why |
| --- | --- | --- | --- |
| EXTRACTING, PLANNED, TRANSCRIBING, STITCHING, GENERATING | `request_cancellation` (control file) only; **no record write** | worker (later, at a boundary) | CXL-03 verbatim; single-writer rule — a worker lives |
| PENDING | record → CANCELLED, **and** control file | web | No worker exists; web write stands on reconcile's legitimacy (D7) |
| QUEUED | record → CANCELLED, **and** control file | web | Same; the control file is CAP-10 containment for the spawn race |
| INTERRUPTED | record → CANCELLED, **and** control file | web | No live worker by definition of INTERRUPTED; lets operators retire dead jobs visible in the shared list; the control file is honored if the job is ever re-run |
| COMPLETED, FAILED, CANCELLED | **no-op**: zero writes, 200 | nobody | CXL-06 verbatim — record, artifacts, and control files stay exactly as they were; 409 would force clients to special-case "already done" for no gain |

For worker-bound jobs the worker records the terminal CANCELLED state through the existing
chunk-boundary poll and exit path (CXL-04/05; exit code unchanged — D4). If the worker is
already dead at cancel time, the control file simply persists: the next startup reconcile
marks the record INTERRUPTED, and any later re-run observes the control file before the
first chunk and cancels then. The cancel route performs **no liveness check** — one code
path for all worker-bound cancellations, and both outcomes are coherent.

**QUEUED-cancel interaction with the gate (CAP-09):** the drain's re-read sees the record
is no longer QUEUED and never spawns; the pruned dedup set and FIFO order are unaffected.

**Decision — upload-path guard entailed by PENDING-cancellation.** Cancelling a job while
its owner's upload is streaming becomes possible, so the upload handler enforces the state
contract: an early check rejects non-PENDING records with 409 before any byte is accepted,
and a late re-read just before `save_media` repeats it — if the record left PENDING (was
cancelled) mid-stream, the stored bytes are discarded through the existing `discard` seam
and the request answers 409. This also closes the pre-existing hazard of re-uploading into
a job that is already extracting. No new port methods are needed.

### D10 — Serialization, migration, and response shapes (enables LEG-01..09, VIS-03..08, OWN-02)

**Decision — decode.** `decode_job` gains exactly one new read, the only key-tolerant one
in the codec (D1's rule): absent → `None`, `null` → `None`, string → `make_operator_id`
(failure → `CorruptedRecord`). Every other field keeps its existing fail-closed read; the
asymmetry is deliberate and load-bearing: `owner` is the only field an older build could
legitimately omit, so only `owner` is read tolerantly. The existing `_optional_*` helpers
(which require the key present) are unchanged.

**Decision — encode and round-trip invariant.** `encode_job` stays `asdict`: post-change
writes always include the `owner` key (a name, or `null` when `None`). A legacy record
re-saved by any later transition (worker advance, reconcile) thereby gains the key with
`null` — no special migration pass, no boot rewrite (LEG-07). Round-trip invariant: **every
post-change record decodes under pre-change rules**, because the old field-explicit decode
reads exactly its known fields and ignores unknown keys (verified in `serialization.py`) —
the rollback invariant of proposal §10 and LEG-08.

**Decision — domain shape.** `JobRecord.owner: OperatorId | None`, added to the frozen
slotted dataclass. Immutability is structural: `dataclasses.replace` carries `owner`
through every transition and no code path sets it after `admit_job` (OWN-02). `AdmitJobRequest`
gains no field; `admit_job` gains `operator` and records it (OWN-01).

**Decision — response shapes, additive only (VIS-06).** `JobStatusResponse` gains
`owner: str | None`. The new listing route returns `JobListResponse(jobs: [JobListItem…])`
— a wrapper object, so future additions (pagination, totals) stay additive where a bare
array would not. `JobListItem`: `job_id, state, owner, engine, speaker_mode, created_at,
updated_at` — record-derived only: the list performs no per-job plan/results scans, so a
poll of the shared board costs one directory listing, and progress remains the per-job
status read. Legacy items carry `owner: null` (VIS-04). The `mine` filter is
`?mine=true|false` (default false), resolved against the authenticated caller and nothing
else (VIS-07/08).

**Decision — ports.** `list_jobs()` stays unscoped (reconcile must see every job, legacy
included — VIS-05). No new listing method is needed under V2. The port gains exactly two
methods, both structural (`typing.Protocol`, no ABC): `write_heartbeat(job_id, *, at_s)`
and `heartbeat_is_fresh(job_id, *, now_s, stale_after_s) -> bool` (D5). The filesystem
adapter implements them over the layout it owns; the fake storage implements both for the
default run.

---

## 3. Hexagonal placement table

Every new or changed symbol, its layer, and the import-direction check against
`tests/test_architecture.py` (domain/ports/usecases MUST NOT import adapters/runtime; the
test stays unchanged and green).

| Symbol | Layer / file | Imports | Direction check |
| --- | --- | --- | --- |
| `OperatorId`, `make_operator_id`, operator-name pattern | `domain/ids.py` | stdlib only | ✅ domain |
| `JobNotOwned(DomainError)` | `domain/errors.py` | — | ✅ domain |
| `JobRecord.owner: OperatorId \| None` | `domain/jobs.py` | domain | ✅ domain |
| `JobState.QUEUED` | `domain/jobs.py` | — | ✅ domain |
| `WORKER_BOUND_STATES`, `TERMINAL_STATES` (frozensets) | `domain/jobs.py` | domain | ✅ domain |
| `require_owner(job, operator)` | `usecases/ownership.py` (new) | domain only | ✅ core |
| `admit_job(..., operator)` | `usecases/admit_job.py` | domain + ports | ✅ core |
| `cancel_job(job_id, *, operator, storage, now)` | `usecases/cancel_job.py` (new) | domain + ports | ✅ core |
| `PurgeJobArtifacts.operator: OperatorId` (required, no default) | `usecases/purge_job_artifacts.py` | domain only | ✅ core |
| `transcribe_job` heartbeat touch at boundaries | `usecases/transcribe_job.py` | ports (storage method) + injected clock | ✅ core |
| `write_heartbeat`, `heartbeat_is_fresh` | `ports/transcript_storage.py` | domain only; `typing.Protocol` — structural typing, no ABC | ✅ core |
| owner decode helper, `asdict` encode | `adapters/storage/serialization.py` | domain | ✅ adapter |
| heartbeat file implementation (`HEARTBEAT` layout constant, atomic write) | `adapters/storage/filesystem_transcript_storage.py` | domain + serialization | ✅ adapter |
| `InvalidCredential`, `parse_operator_tokens`, `build_authenticator` | `adapters/web/auth.py` (new) | stdlib (`hmac`), domain | ✅ adapter |
| `_authorized` helper; 401/403 translation; list + cancel handlers; upload owner check and state guards | `adapters/web/routers/jobs.py` | domain + usecases + web auth | ✅ adapter |
| `WebDependencies.authenticate` (required field, no default) | `adapters/web/app.py` | domain + web auth | ✅ adapter |
| `JobStatusResponse.owner`, `JobListResponse`, `JobListItem`, `CancelJobResponse`, `AdmitJobRequest` identity-discard validator | `adapters/web/schemas.py` | domain | ✅ adapter |
| `Settings.operator_tokens`, `Settings.max_concurrent_jobs` | `runtime/settings.py` | pydantic-settings | ✅ composition root |
| `worker_is_alive`, `HEARTBEAT_STALE_AFTER_S`, extended `reconcile_interrupted_jobs`, `drain_once`, `drain_supervisor`, `DRAIN_SWEEP_INTERVAL_S`, dedup set | `runtime/app.py` | domain + ports + web app | ✅ composition root |
| worker start heartbeat; terminal-state exit guard in `run_job` | `runtime/worker.py` | adapters + usecases (already) | ✅ composition root |

Port changes are additive and structural: the fake and filesystem adapters satisfy the two
new methods without inheritance, per the repo's Protocol discipline.

---

## 4. Sequence diagrams

### (a) Authenticated request → identity resolution → use case (non-owner upload shown)

```mermaid
sequenceDiagram
    participant Op as Operator client
    participant H as Route handler (adapters/web)
    participant A as deps.authenticate (adapters/web/auth)
    participant UC as require_owner (usecases)
    participant S as TranscriptStoragePort

    Op->>H: PUT /api/jobs/{id}/media with Authorization: Bearer token
    H->>A: authenticate(raw authorization header)
    A->>A: parse Bearer; constant-time full-map scan (no early exit)
    alt no configured token matches
        A-->>H: raise InvalidCredential
        H-->>Op: 401 {"detail":"not authenticated"} (identical for every cause)
    else token resolves
        A-->>H: OperatorId
        H->>H: validate id shape (malformed -> 404, before filesystem)
        H->>S: load_job(id)
        S-->>H: JobRecord (unknown id -> 404)
        H->>UC: require_owner(job, operator)
        alt job.owner != operator
            UC-->>H: raise JobNotOwned
            H-->>Op: 403 {"detail":"not the owner of this job"} — nothing written
        else owner
            UC-->>H: proceed
            H->>S: stream bytes, probe, save_media, persist QUEUED
            H-->>Op: 204
        end
    end
```

### (b) Upload at full capacity → QUEUED → drain → spawn

```mermaid
sequenceDiagram
    participant Op as Owner client
    participant Up as Upload handler
    participant S as Storage (job.json)
    participant D as Drain supervisor (lifespan task)
    participant W as Worker process

    Op->>Up: PUT /api/jobs/{id}/media (authenticated, owner)
    Up->>S: stream + probe + save_media
    Up->>S: update_job(state=QUEUED) — web write: no live worker exists
    Up-->>Op: 204

    Note over D: sweeps every DRAIN_SWEEP_INTERVAL_S (5 s)
    D->>S: list_jobs()
    D->>D: derive active = worker-bound records with pid alive AND heartbeat fresh
    alt active >= max_concurrent_jobs
        Note over D: no free slot — job stays QUEUED until a worker dies or finishes
    else slot free
        D->>D: prune spawned-awaiting-claim set; pick oldest QUEUED (FIFO)
        D->>S: load_job(id) — re-read before spawn (CAP-09)
        alt record no longer QUEUED (cancelled while queued)
            D->>D: skip — never spawn
        else still QUEUED and not already spawn-issued
            D->>W: Popen worker — argv carries only job id and data dir
            D->>D: add id to spawned-awaiting-claim set
            W->>S: claim update_job(worker_pid), then advance to EXTRACTING — worker writes
        end
    end
```

### (c) Startup reconcile + drain interaction

```mermaid
sequenceDiagram
    participant Boot as build_app lifespan
    participant R as reconcile_interrupted_jobs
    participant S as Storage
    participant D as Drain supervisor

    Boot->>Boot: require_binaries()
    Boot->>R: run before first request
    R->>S: list_jobs() — every record, legacy owner=null included
    loop each record in WORKER_BOUND_STATES
        R->>R: alive = pid alive AND heartbeat fresh (stale heartbeat vetoes live pid)
        alt not alive
            R->>S: update_job(state=INTERRUPTED) — web write: no live worker
        end
    end
    Note over R: PENDING, QUEUED, and terminal states untouched (CAP-12, HARD-09)

    Boot->>D: start supervisor task
    loop every 5 s
        D->>S: derive active via combined liveness
        Note over D: surviving workers keep their slots; dead ones freed by reconcile
        D->>S: spawn oldest QUEUED while active < max_concurrent_jobs (FIFO)
    end
```

---

## 5. Cancel state classification (D9 reference table)

| State | Class | Cancel action | Writes | Response |
| --- | --- | --- | --- | --- |
| PENDING | unbound | record → CANCELLED + control file | web (no worker exists) | 200 |
| QUEUED | unbound | record → CANCELLED + control file | web (no worker exists) | 200 |
| INTERRUPTED | unbound | record → CANCELLED + control file | web (no worker exists) | 200 |
| EXTRACTING / PLANNED / TRANSCRIBING / STITCHING / GENERATING | worker-bound | control file only | worker, at next boundary | 200 |
| COMPLETED / FAILED / CANCELLED | terminal | no-op | none | 200 |

---

## 6. What this design does NOT change

Surviving invariants (proposal §7, explore §3 — restated briefly, all still load-bearing):

1. **Single-writer rule** — the worker is sole writer of `job.json` while alive; the only
   other writer is the web process for jobs with *no live worker* (reconcile precedent,
   extended explicitly in D7 to the gate's QUEUED write and D9's unbound cancellations).
   The gate never writes after a worker starts (CAP-11).
2. **Atomic `save_chunk_result`** (`.tmp` + fsync + `os.replace`) — and now every persisted
   write including the heartbeat uses the same discipline.
3. **Chunk-local transcription times** — identity never enters the transcription boundary;
   worker argv unchanged (`--job-id`, `--data-dir` only).
4. **Capability refusal ordering** — `DiarizationUnsupported` still precedes any storage
   touch in `admit_job`; the new `operator` parameter does not move it (OWN-11).
5. **ULID validation before filesystem** on every job-naming route; malformed = unknown = 404.
6. **List-form ffmpeg argv** — never `shell=True`.
7. **Path containment** before any spawn.
8. **Percent-encoded `X-Filename`** as metadata only.
9. **Commit-by-rename upload** from a sibling `.part`.
10. **Extensionless source**; content type from probe; no `UploadFile`/`File`/`Form` in
    `adapters/web` (structural test untouched).
11. **Progress derived on read, never a counter** — the active-worker count follows suit:
    derived from records + liveness; the dedup set (D7) idempotizes spawn issuance and is
    explicitly not a counter.
12. **Hexagonal boundary** — `tests/test_architecture.py` unchanged and green; placement
    per §3. Secrets at the composition root only; operator names persisted, never tokens.
13. **`FilesystemTranscriptStorage` and `data_dir`** survive; no DB, no object storage.
14. **No new third-party dependencies** — stdlib (`hmac`, `secrets`) plus already-present
    `fastapi`/`pydantic-settings`. No slice owes a `pip install`.

---

## 7. Scenario → decision traceability (all 68)

| Scenario | Enabled by |
| --- | --- |
| AUTH-01 valid token resolves operator | D3 (map + full-scan resolution), D8 (wiring) |
| AUTH-02 missing token rejected on every route | D8 (required authenticator + `_authorized` first in every handler), D4 (401) |
| AUTH-03 unknown token rejected | D3 (full-scan compare, zero matches fail), D4 |
| AUTH-04 malformed credentials rejected | D4 (decision: malformed ≡ missing — identical 401, no state change) |
| AUTH-05 failures do not enumerate operators | D3 (constant-time full scan, no early exit), D4 (one body for all 401 causes) |
| AUTH-06 every route proven authenticated by tests | D8 (route-table-generated parametrized 401 check) |
| AUTH-07 zero operators refuses boot | D3 (absent/empty map → refuse) |
| AUTH-08 malformed map refuses boot | D3 (validation table: no-colon pair, bad name, empty token, duplicate name/token) |
| AUTH-09 names persisted, never tokens | D3 (secret discipline), D10 (encode carries only owner name), worker argv unchanged |
| OWN-01 admission records caller | D8 (operator argument into `admit_job`), D10 (owner field) |
| OWN-02 owner immutable across lifecycle | D10 (frozen record; `replace` carries owner; no transition sets it) |
| OWN-03 owner uploads successfully | D9/D8 (owner check passes; upload mechanics unchanged) |
| OWN-04 non-owner upload denied, nothing touched | D8 (`require_owner` before writer construction), D4 (403) |
| OWN-05 every mutation class denied | D8 (`require_owner` shared by upload, cancel, purge paths) |
| OWN-06 purge seam carries ownership | D8 (`PurgeJobArtifacts.operator` required; `require_owner` as the seam's gate at use-case level) |
| OWN-07 client-supplied identity has no effect | D8 (identity-discard validator on `AdmitJobRequest`; identity only from token) |
| OWN-08 known foreign id grants no mutation | D4 (403 under V2 — existence public; denial by ownership, not secrecy) |
| OWN-09 malformed id on mutating route | D4 (precedence: 401 → id validation 404 → filesystem) |
| OWN-10 upload mechanics preserved | Spec-level only (D9 wraps without restructuring; invariant §6.2–10) |
| OWN-11 capability refusal ordering preserved | Spec-level only (D8 notes `operator` added after the capability guard) |
| VIS-01 foreign job readable | D8 (auth-only on GET; no owner check on reads), D10 (owner attribution) |
| VIS-02 reading writes nothing | Spec-level only (status route stays read-only by construction) |
| VIS-03 list returns every job attributed | D10 (`JobListResponse` over unscoped `list_jobs`) |
| VIS-04 legacy jobs surface with null owner | D1 (owner=None semantics), D10 (additive nullable field) |
| VIS-05 nothing hidden | D10 (unscoped listing; no caller-identity scoping) |
| VIS-06 status response backward compatible | D10 (additive `owner`; pre-change fields unchanged) |
| VIS-07 mine filter returns caller's jobs | D10 (`mine` boolean resolved server-side) |
| VIS-08 operator identity parameters never honored | D8/D10 (no such parameter declared; filter computed from token identity) |
| CXL-01 owner cancels running job | D9 (control-file branch), D8 (owner check) |
| CXL-02 non-owner cancellation denied | D8 (`require_owner` before any write), D4 (403) |
| CXL-03 web writes only the control file | D9 (worker-bound branch: control file only, no record write) |
| CXL-04 worker stops at next boundary | Spec-level only (existing chunk-boundary poll preserved) |
| CXL-05 cancellation before first chunk, zero work | Spec-level only (existing loop poll precedes first chunk) |
| CXL-06 cancelling terminal job is a no-op | D9 (terminal branch: zero writes, 200) |
| CXL-07 owner cancels queued job | D9 (QUEUED → CANCELLED record + control file), D7 (drain re-read skips) |
| CXL-08 malformed/unknown id on cancel | D4/D9 (404 before filesystem) |
| CAP-01 upload at full capacity queues | D7 (upload always persists QUEUED), D2 |
| CAP-02 awaiting-upload state unchanged | D7 (PENDING until upload completes) |
| CAP-03 the N+1th job queues | D2 (cap N), D7 (single decision point) |
| CAP-04 spawning never exceeds cap | D7 (one spawn decision point; derivation before each spawn) |
| CAP-05 restart derives active from disk | D7 (derived count, no surviving counter) |
| CAP-06 dead workers free slots | D5/D6 (combined liveness excludes dead workers) |
| CAP-07 oldest queued spawns first | D2 (FIFO by ULID/creation order), D7 |
| CAP-08 drain resumes after restart | D7 (persisted QUEUED + lifespan supervisor) |
| CAP-09 cancelled queued job not spawned | D7 (re-read before spawn), D9 (record no longer QUEUED) |
| CAP-10 spawn-vs-cancel race does zero work | D9 (control file + terminal-state worker guard), CXL-05 poll |
| CAP-11 no gate write after worker start | D7 (gate writes only QUEUED, pre-spawn; worker owns the rest) |
| CAP-12 reconcile leaves queued records queued | D6 (QUEUED excluded from reconcile scope) |
| CAP-13 drained queue rolls back cleanly | Spec-level only (operational rule; D10 keeps records rollback-safe) |
| CAP-14 queued record fails old build closed | Spec-level only (documented rollback hazard, proposal §10) |
| LEG-01 pre-change record decodes ownerless | D10 (absent key → None) |
| LEG-02 explicit null decodes as none | D10 |
| LEG-03 present owner validated | D10 (`make_operator_id` on decode) |
| LEG-04 invalid owner fails closed | D10 (`CorruptedRecord`, no coercion) |
| LEG-05 legacy-only boot succeeds | D10 (tolerant decode), D6 (reconcile sees all) |
| LEG-06 mixed populations list and reconcile | D1/D10 (null attribution), D6 (owner-blind reconcile) |
| LEG-07 no fictional owner at boot | D1 (no backfill), D10 (no boot rewrite) |
| LEG-08 owned record round-trips through old decode | D10 (owner additive; old decode ignores unknown keys — verified) |
| LEG-09 old build tolerates new auxiliary files | D5 (heartbeat inert inside job dir), control file unchanged |
| HARD-01 heartbeat created at worker start | D5 (`run_job` write after claim) |
| HARD-02 heartbeat refreshed at chunk boundaries | D5 (`transcribe_job` boundary write) |
| HARD-03 only the worker writes the heartbeat | D5 (web/drain/reconcile never call `write_heartbeat`) |
| HARD-04 live pid + fresh heartbeat is active | D5/D6 (`worker_is_alive`) |
| HARD-05 live pid + stale heartbeat not trusted | D5/D6 (staleness vetoes pid — precedence explicit) |
| HARD-06 dead pid inactive regardless of heartbeat | D5/D6 |
| HARD-07 dead worker in any worker-bound state interrupted | D6 (`WORKER_BOUND_STATES`) |
| HARD-08 live workers not interrupted | D6 (combined liveness passes) |
| HARD-09 non-worker-bound states untouched | D6 (PENDING/QUEUED/terminal excluded) |

---

## 8. Strict TDD note — every decision is reachable by the default run

Default run: `.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"` plus
`.venv\Scripts\python.exe -m mypy src tests`. No decision requires a paid API or model
weights. Seams, all following existing repo precedent:

| Decision | Time/process seam | Injection point |
| --- | --- | --- |
| D1/D10 decode/encode | none — pure codec | direct `serialization` tests (existing style) |
| D2 cap | none | `Settings` construction tests; `max_concurrent` is an argument of `drain_once` (parametrize N) |
| D3 tokens | none — stdlib `hmac` | `parse_operator_tokens` / authenticator built with test maps |
| D4 HTTP mapping | none | in-process ASGI transport tests (existing style) |
| D5 heartbeat | **clock** | injected `now` (`WebDependencies.now` precedent; `write_heartbeat(at_s=…)`); fake storage implements both heartbeat methods — no wall-clock sleeps, no timer thread |
| D6 reconcile | **clock + process** | existing `now=` and `is_alive=` parameters; fake heartbeat freshness via fake storage |
| D7 drain | **process spawn + clock** | injectable `launch` callable (`spawn_worker` precedent — no real `Popen` in unit tests); `drain_once` is synchronous and fully driven; the async supervisor loop is a thin wrapper over it |
| D8 auth wiring | none | conftest supplies a fake `authenticate` (required field forces the wiring into every existing web test) |
| D9 cancel | **clock** | `cancel_job(..., now=…)` with fake storage recording control-file and record writes |

RED-before-GREEN per unit, per `openspec/config.yaml` (`strict_tdd: true`).

---

## 9. Residual risks carried into tasks

| # | Residual | Why accepted |
| --- | --- | --- |
| RR1 | Duplicate spawn across a web crash between `Popen` and the worker's claim: the dedup set is in-memory, so a new web process could re-spawn a job whose first worker is still starting. Window ≈ worker startup time (seconds); consequence is duplicated work on one job, not data loss. | Same accepted class as the documented pid-reuse residual; a durable claim would need a locking mechanism this filesystem store does not have. The worker's terminal-state guard (D9) contains the cancellation interaction. |
| RR2 | A live-but-hung worker past the 7200 s bound is excluded from the derived active count, so the drain may spawn one worker beyond the true load for each such job until the next restart reconcile interrupts it. | Bounded by the number of hung jobs (rare); the alternative — trusting the pid alone — reopens the pid-reuse hole the heartbeat exists to close (HARD-05). |
| RR3 | A syntactically invalid request body can draw a 422 from FastAPI validation before the handler's authentication check runs. | No state changes in that case (the normative core of AUTH-02 holds); the parametrized route check uses well-formed requests. Moving auth ahead of validation would require middleware or `Depends` — both rejected (D8, proposal §5b). |

No spec conflict was found that required stopping: every requirement in the seven specs is
satisfiable as written under these decisions.
