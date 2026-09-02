# Tasks: multi-operator-access

> Phase: `sdd-tasks` · Artifact store: hybrid (mirror of Engram `sdd/multi-operator-access/tasks`)
> Inputs: `proposal.md` (six-slice decomposition §9, risks §8, docs §11), `design.md` (D1–D10, cancel-state
> table §5, traceability §7, residuals §9, strict-TDD seams §8), the seven `specs/*/spec.md` (68 scenarios),
> `openspec/config.yaml` (budget 800, auto-chain, stacked-to-main), and the code on `main` at write time.
> Style follows `openspec/changes/video-transcription-pipeline/tasks.md`: hierarchical numbering, checkboxes,
> RED-before-GREEN pairs, every task naming the spec scenario(s) it closes.
> Path convention: `domain/...`, `usecases/...`, `adapters/...`, `runtime/...` are relative to
> `src/onevoicecut/`; `tests/...` is relative to the repo root.

**Decisions D1–D10 are closed.** Tasks implement them as written; no task reopens them. Residuals RR1–RR3
(design §9) are accepted and carry **no tasks**. The two dependency-forced adjustments to the proposal's S1–S6
contents (slice ORDER unchanged) are marked inline where they occur:

1. `JobState.QUEUED`, `WORKER_BOUND_STATES`, `TERMINAL_STATES` land in **S4**, not S5 — D9's cancel
   classification is ONE table over every state; a classifier with a QUEUED hole would be a partial table the
   full-output discipline forbids, and the enum value alone is behavior-neutral (nothing writes QUEUED until S5).
2. All README/CLAUDE.md premise deltas stay in **S6** per proposal §9/§11, although the premise is already
   falsified at S2. Accepted consequence: intermediate stacked commits carry a stale README premise line;
   work-unit doc-colocation yields to the proposal's explicit slice schedule. Flagged, not silently changed.

**Budget discipline (repo-empirical).** Nine measured slices overran estimates 3.2x–5.1x (mean ≈ 4.0x; tests
dominate 61–81% of diffs). Every unit below is sized as `src estimate × ~4` and MUST be measured
(`git diff --stat`) **before** committing. Any unit whose measurement exceeds 800 lines splits at its
pre-declared seam at apply time — except unit S2a, which shrinks adjacent scope instead (R3: never split
authentication from ownership authorization). Budget is never met by deleting tests, comments, or docs.

**Definition of done — every unit, without exception:**

```
.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"   (green)
.venv\Scripts\python.exe -m mypy src tests                            (green)
```

The default run never calls a paid API or loads model weights (pytest markers `paid`/`localmodel` excluded).
`tests/test_architecture.py` stays unchanged and green in every unit. Strict TDD: RED before GREEN in every
behavior-bearing pair; the only non-TDD tasks are the two pure-config/doc deltas (1.7, 6.9–6.12), each with its
own verification named. Where a RED is expected to pass immediately (existing behavior being locked), the task
says so and the GREEN records it as characterization — honesty over ceremony, per the reference change's precedent.

---

## Review Workload Forecast

Sizing basis: `expected review size = src estimate × ~4` (repo mean overrun 4.0x, tests 61–81% of diffs).
Docs-only unit S6b is sized at ~×1.2 (docs lines are nearly all diff).

| Unit | Contents | src est. | Expected review size |
| --- | --- | --- | --- |
| S1 | Operator identity in domain + key-tolerant decode + legacy round-trips + `.gitignore` | ~95 | ~380 |
| **S2a (R3-fused)** | Token map, authenticator, deny-by-default wiring, 401 on all routes + route-table gate, admission owner, `require_owner`, upload 403, identity discard, fail-closed boot | ~185 | ~660–740 |
| S2b | Secret-discipline assertions, owner-immutability lifecycle, purge seam ownership, capability-order regression | ~60 | ~240 |
| S3 | `GET /api/jobs`, additive `owner` on responses, server-side `mine` filter | ~80 | ~320 |
| S4 | State-set constants + QUEUED enum, `cancel_job`, cancel route, boundary characterization, upload state guard | ~140 | ~560 |
| S5a | `max_concurrent_jobs` setting, upload persists QUEUED, `drain_once` (derived count, FIFO, re-read, dedup), worker terminal-state guard | ~105 | ~420 |
| S5b | Lifespan drain supervisor, restart semantics, rollback codec locks | ~60 | ~240 |
| S6a | Heartbeat port/adapter/worker writes, `worker_is_alive`, reconcile extended to all worker-bound states | ~135 | ~540 |
| S6b | README/CLAUDE.md premise + env-var + route + budget-drift deltas, rollback procedure | ~150 (docs) | ~180 |

| Field | Value |
| --- | --- |
| **Total estimated changed lines** | **~3,600** across 9 work units / 74 tasks |
| Per-slice estimates | S1 ~380 · S2 ~900–980 (as two units: 2a ~660–740, 2b ~240) · S3 ~320 · S4 ~560 · S5 ~660 (5a ~420, 5b ~240) · S6 ~720 (6a ~540, 6b ~180) |
| Largest single unit | S2a ~660–740 (R3-fused; contingency below) |
| Per-unit 800-line budget risk | **Low** for all units except S2a (**Medium** — closest to ceiling, contingency pre-declared) |
| `400-line budget risk` | **High** in aggregate by construction (~3,600 lines ≫ any single-review budget; every unit exceeds 400 in expectation except S6b). Per-unit against the session budget of 800: Low (S2a Medium). Chained delivery absorbs it. |
| **Chained PRs recommended** | **Yes** — delivery_strategy `auto-chain`, chain_strategy `stacked-to-main` (both cached in `openspec/config.yaml` review block) |
| Suggested split | 9 stacked PRs: PR 1 = S1 → PR 2 = S2a → PR 3 = S2b → PR 4 = S3 → PR 5 = S4 → PR 6 = S5a → PR 7 = S5b → PR 8 = S6a → PR 9 = S6b, each to `main` in order |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
400-line budget risk: High (aggregate; per-unit risk Low except S2a Medium under the 800 session budget)
```

`Decision needed before apply: No` because delivery_strategy is `auto-chain` with chain_strategy already
cached and every unit pre-split to ≤ 800 expected lines in THIS document — apply implements one stacked unit
per PR, measuring before each commit.

**S2a contingency (R3-safe).** If S2a's pre-commit measurement exceeds 800, eject ONLY these two test
matrices into an immediate same-slice follow-up unit landing before S3: (a) the extra 401-cause response-shape
equivalence permutations of AUTH-05, and (b) the full 401→404→403 precedence permutations of OWN-09 (the
three load-bearing orderings stay). The fused core — 401 machinery AND 403 ownership checks — never splits.
Never ship 401 without 403.

**Rollback boundaries** follow work-unit-commits: each unit's table row names the files removable without
reverting unrelated work; every unit ends independently green on both definition-of-done commands, which is
what makes it independently revertible in the stack.

---

## Slice 1 — Identity in domain + storage (Unit S1, PR 1, expected ~380)

Closes: LEG-01..08 (compatibility core). No behavior change on any route.
Rollback boundary: `domain/ids.py`, `domain/jobs.py`, `adapters/storage/serialization.py`,
`usecases/admit_job.py` (owner=None only), `tests/fakes/transcript_storage.py`, `.gitignore`, new tests.
Runtime harness: N/A — pure codec/domain over fakes and `tmp_path`.

- [x] 1.1 RED: `tests/unit/domain/test_ids.py` — `make_operator_id` accepts exactly
      `[a-z0-9_-]{1,64}` (boundary lengths 1 and 64); rejects empty, 65 chars, uppercase, `.`, `:`, `;`,
      whitespace; `OperatorId` is a `NewType` over `str`; failures raise `InvalidIdError`
      (validation used by decode — LEG-03's foundation).
- [x] 1.2 GREEN: `domain/ids.py` — `OperatorId` + `make_operator_id` next to the existing ULID discipline.
- [x] 1.3 RED: `tests/unit/domain/test_jobs.py` + `tests/unit/adapters/storage/test_serialization.py` —
      `JobRecord.owner: OperatorId | None` is a **required** constructor argument (no default: no
      construction site may silently omit it); frozen; `dataclasses.replace` carries it. Codec matrix (D1/D10):
      key **absent** → decode `owner=None`, not corrupt (LEG-01); key `null` → `None` (LEG-02); valid string →
      validated `OperatorId` (LEG-03); invalid string (uppercase, `:`) or non-string → `CorruptedRecord`,
      never coerced (LEG-04); `encode_job` always writes the key (`null` when None, name when set); an encoded
      owned record re-decodes with owner intact (LEG-08 encode half).
- [x] 1.4 GREEN: `domain/jobs.py` field; `adapters/storage/serialization.py` — exactly ONE new key-tolerant
      read (absent→None / null→None / string→`make_operator_id` else `CorruptedRecord`); every existing
      construction site updated: `decode_job`, `usecases/admit_job.py` (records `owner=None` — behavior-neutral
      until S2), `tests/fakes/transcript_storage.py`, and every test fixture building a `JobRecord`.
- [x] 1.5 RED: `tests/unit/adapters/storage/test_legacy_records.py` (new) against the real
      `FilesystemTranscriptStorage` on `tmp_path` — a hand-written pre-change `job.json` (no owner key) loads
      and lists with `owner=None` (LEG-01 adapter level); a legacy-only data directory lists completely
      (LEG-05); a mixed legacy+owned directory lists every record with correct attribution (LEG-06 listing
      half); listing + startup `reconcile_interrupted_jobs` over legacy and owned dead TRANSCRIBING records
      marks both kinds INTERRUPTED and changes **no record bytes** except the reconciled state rewrite — no
      owner backfill anywhere (LEG-07, LEG-06 reconcile half); a post-change record carrying `owner` decodes
      through a simulated pre-change field-explicit read that ignores the unknown key (LEG-08 decode half —
      the rollback invariant).
- [x] 1.6 GREEN: expected green from 1.4's codec work — confirm and record as characterization (existing
      `reconcile_interrupted_jobs` never reads `owner`, so owner-blindness holds by construction); any failure
      is a real gap in 1.4 and gets fixed there, not patched here.
- [x] 1.7 CONFIG (no behavior to test-drive): add `data/` to `.gitignore` if absent (R6 — the default
      `data_dir` puts `job.json`, which will carry operator names from S2, on a committable path). Verify:
      `git check-ignore data/probe` succeeds.
- [x] 1.8 DONE-UNIT: both definition-of-done commands green; measure `git diff --stat`. Pre-declared split
      seam if measurement exceeds 800 (not expected): unit A = tasks 1.1–1.4 (domain + codec), unit B =
      1.5–1.7 (adapter-level legacy proofs + gitignore).

## Slice 2 — Token authentication + ownership authorization, ONE R3-fused unit + adjacent scope (PR 2 + PR 3)

> **R3 BINDING (the hardest constraint on this change).** Unit S2a is ONE review unit: authentication (401)
> and ownership authorization (403) land together and MUST NEVER be split across a PR boundary. Shipping 401
> without 403 is exactly the BOLA window R3 names — an authenticated operator B could overwrite operator A's
> upload. If S2a exceeds budget when measured, shrink ADJACENT scope per the contingency above; never split
> the fused core. Unit S2b (same slice, lands immediately after, still before S3) contains only
> non-fused adjacent scope: secret-discipline proofs, lifecycle immutability, the purge seam, an ordering
> regression — no 401/403 machinery.

### Unit S2a (PR 2, expected ~660–740)

Closes: AUTH-01..09 (09 shared with 2b), OWN-01, 03, 04, 07, 08, 09, 10.
Rollback boundary: `adapters/web/auth.py` (new), `adapters/web/app.py`, `adapters/web/routers/jobs.py`,
`adapters/web/schemas.py`, `domain/errors.py`, `usecases/ownership.py` (new), `usecases/admit_job.py`,
`runtime/settings.py`, `runtime/app.py` (wiring only), web tests.
Runtime harness: in-process ASGI transport (the existing web-test style) — the HTTP matrix IS the harness.

- [x] 2.1 RED: `tests/unit/adapters/web/test_operator_tokens.py` (new) — `parse_operator_tokens` matrix
      (D3): valid `maria:tok;jose:tok2` → mapping of `OperatorId → token`; whitespace per pair stripped;
      split at FIRST `:` (tokens may contain `:`); rejects with boot-refusing errors: absent/empty raw
      (zero operators — AUTH-07 parse level), pair with no `:`, empty name, name failing `make_operator_id`,
      empty token, duplicate operator name, duplicate token value (AUTH-08); every error message MAY name the
      offending name/position and MUST NOT contain any token value (AUTH-09 boot half).
- [x] 2.2 GREEN: `adapters/web/auth.py` (new) — `InvalidCredential` (web-adapter error),
      `parse_operator_tokens` pure function per the D3 table.
- [x] 2.3 RED: same file — `build_authenticator(map)` returns `Callable[[str | None], OperatorId]`:
      a configured token resolves its operator (AUTH-01); a token matching no operator raises
      `InvalidCredential` (AUTH-03); missing header (`None`, `""`), wrong scheme (`Basic x`), bare `Bearer`,
      and unparsable forms all raise the SAME `InvalidCredential` (AUTH-04); scheme is case-insensitive;
      a token equal to a later pair's token still resolves correctly when an earlier pair shares its prefix —
      full scan, never early exit; wrong-token-for-existing-operator vs token-matching-no-operator raise
      indistinguishably (AUTH-05 authenticator half).
- [x] 2.4 GREEN: `adapters/web/auth.py` — `build_authenticator`: parse `Bearer` (case-insensitive), scan
      EVERY pair with `hmac.compare_digest` on UTF-8 bytes, no early exit; exactly one match → `OperatorId`,
      zero → `InvalidCredential`.
- [x] 2.5 RED: `WebDependencies.authenticate` is REQUIRED — construction without it fails (no permissive
      default: deny-by-default at construction, D8); `tests/unit/adapters/web/conftest.py` supplies a fake
      authenticator resolving a default operator and a header helper, and every existing web test authenticates
      through it (suite-wide regression stays green).
- [x] 2.6 GREEN: `adapters/web/app.py` — `authenticate: Callable[[str | None], OperatorId]` field with no
      default; conftest + affected wiring updated.
- [x] 2.7 RED: `tests/unit/adapters/web/test_auth_gate.py` (new) — the R2 gate: a parametrized 401 check
      GENERATED from the registered route table (`app.routes` APIRoutes, never a hand-maintained list) — for
      every route a well-formed unauthenticated request answers 401 with the identical body
      `{"detail": "not authenticated"}` and `WWW-Authenticate: Bearer`, and causes NO state change (no job
      admitted, no upload bytes written, recording fake starter never called, no cancellation recorded)
      (AUTH-02, AUTH-06); all 401 causes (missing/malformed/unknown) produce byte-identical response bodies
      (AUTH-04/05 HTTP half). New routes registered later join this test automatically — deny-by-default
      enforced by a test, not a document. (Applied: FastAPI 0.141 wraps an included router in a lazy
      `_IncludedRouter`, so the walk starts from `app.routes` and descends anything carrying an
      `original_router` — still the registered route table, never a hand-maintained list.)
- [x] 2.8 GREEN: `adapters/web/routers/jobs.py` — `_authorized(request, deps) -> OperatorId` shared helper,
      the FIRST statement of every existing handler (admit, status, upload); `InvalidCredential` → uniform 401
      translation; route-table test green.
- [x] 2.9 RED: `tests/unit/usecases/test_ownership.py` (new) + admission tests — `JobNotOwned(DomainError)`
      exists; `require_owner(job, operator)` raises `JobNotOwned` on mismatch INCLUDING `owner=None` (D1's
      uniform rule) and passes on match; authenticated admission by operator "a" persists owner "a" (OWN-01);
      the persisted record bytes contain the name and NOT the token value (AUTH-09 record half).
- [x] 2.10 GREEN: `domain/errors.py` — `JobNotOwned`; `usecases/ownership.py` (new) — `require_owner`
      (imports domain only); `usecases/admit_job.py` gains keyword-only `operator` and records it AFTER the
      capability guard; router passes the token-resolved operator; `JobNotOwned` → 403
      `{"detail": "not the owner of this job"}` translation (D4: generic — V2 already made existence public).
- [x] 2.11 RED: `tests/unit/adapters/web/test_upload_ownership.py` (new) — owner upload succeeds and the
      mechanics are unchanged under the authorized path: commit-by-rename from the sibling `.part`,
      extensionless stored source, content type from probe, percent-encoded filename as metadata only
      (OWN-03, OWN-10 — authenticated re-runs of the existing mechanics proofs); non-owner upload → 403 with
      NOTHING touched: no `.part` remains, prior media intact, record unchanged, no starter call (OWN-04);
      a known foreign id (as observed from the shared listing) is denied by the OWNERSHIP check, not id
      secrecy — well-formed id, real job, still 403 (OWN-08); malformed id on the mutating route → 404 before
      any filesystem access, authenticated (OWN-09); the load-bearing precedence holds: 401 beats 404 beats
      403 (unauthenticated+malformed → 401; authenticated+malformed → 404; authenticated+foreign → 403).
- [x] 2.12 GREEN: `adapters/web/routers/jobs.py` upload handler — `_authorized` → `_load` → `require_owner`
      BEFORE the writer is constructed (before any byte is accepted); reuse the single 403 translation.
- [x] 2.13 RED: `tests/unit/adapters/web/test_identity_discard.py` (new) — operator "b" admits with a body
      containing `"operator": "a"`: the recorded owner is "b", the token-resolved caller (OWN-07); structural
      assertion over `app.routes`: no route declares an operator-identity parameter in body, header, or query
      (VIS-08 structural half begins; the listing filter half closes in 3.5).
- [x] 2.14 GREEN: `adapters/web/schemas.py` — `AdmitJobRequest` keeps `extra="forbid"` for ordinary typos and
      gains a `model_validator(mode="before")` removing any client-supplied `operator` key before validation
      (D8: discarded, not rejected — a 422 would fail OWN-07).
- [x] 2.15 RED: `tests/unit/runtime/test_settings_auth.py` (new) — fail-closed boot (D3): with
      `ONEVOICECUT_OPERATOR_TOKENS` absent/empty the composition root (`build_dependencies`/app construction
      against `Settings(data_dir=tmp, operator_tokens="")`) refuses with an error naming the failure class,
      before any route can serve (AUTH-07); every malformed map form of 2.1 refuses at the same boundary
      (AUTH-08 boot level); refusal messages contain no token values. **Note: green at write time —
      characterization.** The composition-root wiring landed with 2.6 (a required `authenticate` field cannot
      compile unless the root supplies one), so these tests lock behavior that already exists rather than
      driving new code. Honesty over ceremony, per the 1.6/2.18 precedent.
- [x] 2.16 GREEN: `runtime/settings.py` — `operator_tokens: str = ""` (absence reaches the parser, whose
      specific refusal beats a bare pydantic ValidationError); `runtime/app.py` — `build_dependencies` parses
      the map, builds the authenticator, injects it into `WebDependencies`; error paths verified.

### Unit S2b (PR 3, expected ~240) — same slice, adjacent scope only, NO 401/403 machinery

Closes: AUTH-09 (logs/argv halves), OWN-02, OWN-05 (upload + purge arms), OWN-06, OWN-11.
Rollback boundary: the test files below + `usecases/purge_job_artifacts.py` (one field).

- [x] 2.17 RED: AUTH-09 completion — capturing stdout/stderr/logging during an authenticated
      admit+upload cycle: no emitted line contains the token value; the worker argv recorded by a fake
      launcher contains only `--job-id`/`--data-dir` — no token, no operator identity (worker argv unchanged
      by this change). (Applied: `tests/unit/adapters/web/test_secret_discipline.py`; argv driven through
      the real authenticated upload into `spawn_worker` with a fake launch sink, asserted byte-exact.)
- [x] 2.18 GREEN: expected green (the design already forbids both channels) — confirm and record as
      characterization; any leak found is fixed here with the minimal wiring change. (Applied: green at
      write time — characterization. The web path emits nothing and argv is fixed by construction; no
      leak found, no wiring change needed.)
- [x] 2.19 RED: OWN-02 — owner immutability across every transition that exists at this point: admission →
      `save_media` → worker-claim-shaped `update_job(replace(...))` → reconcile rewrite: owner "a" at every
      point; structural assertion that `dataclasses.replace` is the only record-mutation vehicle and carries
      `owner` (later slices lock the rest: 5.5 asserts gate transitions preserve owner, 6.7 asserts extended
      reconcile does). (Applied: `tests/unit/usecases/test_owner_immutability.py`; lifecycle over the real
      filesystem storage with disk re-loads at every point, AST scan proving all four `update_job` call
      sites in src pass records built by `replace`, frozen-record reassignment refused.)
- [x] 2.20 GREEN: expected green (`replace` carries the field structurally) — characterization note; any
      owner-dropping transition found is a defect fixed here. (Applied: green at write time —
      characterization. No owner-dropping transition exists; none fixed.)
- [x] 2.21 RED: OWN-06 + OWN-05 arms — `PurgeJobArtifacts.operator: OperatorId` is REQUIRED (no default;
      construction without it fails) so a future route needs no signature surgery; the seam's gate is the
      shared `require_owner`: a non-owner purge request raises `JobNotOwned` and removes nothing; an owner
      request proceeds (use-case level — the seam has no route); with the upload arm of 2.11 this establishes
      two of OWN-05's three mutation classes (the cancel arm lands in 4.7, which closes OWN-05).
      (Applied: `tests/unit/usecases/test_purge_job_artifacts.py`; genuine RED — 5 failures: construction
      without operator DID NOT RAISE, construction with it got an unexpected keyword. Legacy `owner=None`
      refusal triangulates D1's uniform rule at the seam.)
- [x] 2.22 GREEN: `usecases/purge_job_artifacts.py` — add the required `operator` field; gate test green.
      (Applied: required no-default `operator: OperatorId` between `job_id` and `keep`; 5/5 green.)
- [x] 2.23 RED: OWN-11 — authenticated admission requesting a speaker mode the supplied capabilities cannot
      satisfy refuses 422 BEFORE any storage touch: zero records created (storage empty after), and the new
      `operator` argument has not moved the capability guard's position. (Applied:
      `tests/unit/adapters/web/test_admission_capability_order.py`; HTTP refusal with zero storage calls,
      positive control recording the owner on the satisfiable path, use-case proof that the guard precedes
      even id minting via recording id factories.)
- [x] 2.24 GREEN: expected green (`admit_job` validates compatibility first; 2.10 added `operator` after the
      guard) — characterization lock. (Applied: green at write time — characterization. The guard stands
      where it stood before this change.)
- [x] 2.25 DONE-UNIT (S2a+S2b): both definition-of-done commands green; measure S2a's diff. If S2a exceeded
      800, apply the R3-safe contingency from the forecast (eject ONLY the AUTH-05 permutation matrix and the
      non-load-bearing OWN-09 precedence permutations into a same-slice unit landing before S3). The fused
      401+403 core is never split. (Applied: S2a measured 1294 changed lines and landed as commit 09cc1f0
      under accepted exception — the fused core was never split; the contingency was not the relief used.
      S2b measured within its cap on branch `feat/multi-operator-access-03-secret-discipline-purge-ownership`;
      full suite 652 passed, mypy clean at 125 source files.)

## Slice 3 — Shared job listing, V2 visibility (Unit S3, PR 4, expected ~320)

Closes: VIS-01..08. `list_jobs()` stays unscoped — no new port method under V2.
Rollback boundary: `adapters/web/schemas.py`, `adapters/web/routers/jobs.py` (list handler + status owner
field), new tests. Runtime harness: in-process ASGI transport.

- [x] 3.1 RED: `tests/unit/adapters/web/test_job_list_shapes.py` (new) — response shapes (D10): listing is a
      WRAPPER object `JobListResponse(jobs=[JobListItem...])` (future pagination stays additive);
      `JobListItem` = job_id, state, owner, engine, speaker_mode, created_at, updated_at (record-derived only —
      no per-job plan/results scans); `JobStatusResponse` gains `owner: str | None` ADDITIVELY — every
      pre-change field present with unchanged meaning (VIS-06). (Applied: genuine RED — ImportError on
      `JobListItem`/`JobListResponse`; 4 shape tests pin the wrapper, the exact item field set, legacy
      `owner: null` serialization, and the additive status field set.)
- [x] 3.2 GREEN: `adapters/web/schemas.py` — `JobListResponse`, `JobListItem`, `owner` on
      `JobStatusResponse`; status handler passes `job.owner` through. (Applied: 4/4 shapes green, all 11
      pre-existing status-route tests stay green — additive change confirmed.)
- [x] 3.3 RED: `tests/unit/adapters/web/test_job_list_route.py` (new) — listing behavior: jobs admitted by
      operators "a" and "b" both appear, each attributed (VIS-03); legacy jobs surface with `owner: null`
      (VIS-04); a data directory with N mixed records lists exactly N — no caller-identity scoping removes
      items (VIS-05); operator "b" reads operator "a"'s single job → 200 with owner attribution (VIS-01);
      reading writes NOTHING — the fake storage call log shows zero write methods during GET status and GET
      list (VIS-02). The route-table 401 gate of 2.7 covers `GET /api/jobs` automatically (AUTH-02
      reinforcement). (Applied: genuine RED — 4 failures, `GET /api/jobs` answered 405 with no list route
      registered. The VIS-01/VIS-06 status halves were green at write time — characterization: foreign reads
      already worked and the status `owner` field landed with 3.2. Verified by collection: the gate now
      parametrizes `[GET /api/jobs]`, so the unauthenticated listing dies with the one 401 shape
      automatically.)
- [x] 3.4 GREEN: `adapters/web/routers/jobs.py` — `GET ""` handler over the unscoped `list_jobs()`,
      `_authorized` first. (Applied: handler registered before `GET /{job_id}`; items built record-derived
      only — no plan/results scans. The 4 RED tests green; whole web suite green.)
- [x] 3.5 RED: same test file — `?mine=true` by operator "a" returns exactly "a"'s jobs, no foreign job
      (VIS-07); operator "b" requesting `?mine=true&operator=a` gets "b"'s jobs computed solely from the
      token identity — the supplied identity is never honored (VIS-08); structural assertion from 2.13
      re-run: still no route declares an operator-identity parameter anywhere. (Applied: genuine RED — both
      filter tests failed with the unfiltered board, `mine` not yet a parameter. The 2.13 structural
      assertion re-ran green over the new route table: the listing declares only `mine`, never an operator.)
- [x] 3.6 GREEN: `mine: bool = False` query parameter on the list handler, resolved server-side against the
      authenticated `OperatorId` and nothing else. (Applied: a client-supplied `operator` query parameter
      has nowhere to arrive — FastAPI never binds undeclared parameters — so the filter can only see the
      token identity; a legacy record (owner None) matches nobody.)
- [x] 3.7 DONE-UNIT: both definition-of-done commands green; measure; pre-declared split seam if exceeded
      (not expected): shapes+status-owner vs list route+filter. (Applied: full suite 665 passed,
      5 deselected — baseline 652 + 13 new (12 tests + 1 auto-added gate case); mypy clean at 127 source
      files; measured diff within the 800-line cap, no split needed.)

## Slice 4 — Cancel route (Unit S4, PR 5, expected ~560)

Closes: CXL-01, 02, 03, 04, 05, 06, 08; OWN-05 (cancel arm completes the matrix); CXL-07 cancel-side half.
Rollback boundary: `domain/jobs.py` (enum value + frozensets), `usecases/cancel_job.py` (new),
`adapters/web/routers/jobs.py` (cancel handler + upload state guard), `adapters/web/schemas.py`, new tests.
Runtime harness: in-process ASGI transport + fake-storage call logs.

- [x] 4.1 RED: `tests/unit/domain/test_jobs.py` — `JobState.QUEUED` exists with value `"queued"`;
      `WORKER_BOUND_STATES == frozenset({EXTRACTING, PLANNED, TRANSCRIBING, STITCHING, GENERATING})`;
      `TERMINAL_STATES == frozenset({COMPLETED, FAILED, CANCELLED})`. **Dependency-forced pull-forward from
      S5 (slice order unchanged):** D9's cancel classification is one table over every state; the constants
      and enum value land here so `cancel_job` is written complete from the start. They are behavior-neutral —
      nothing writes QUEUED until S5.
- [x] 4.2 GREEN: `domain/jobs.py` — the enum value and both frozensets, defined ONCE for reconcile, capacity
      derivation, and cancel classification to consume (D6).
- [x] 4.3 RED: `tests/unit/usecases/test_cancel_job.py` (new) — `cancel_job(job_id, *, operator, storage,
      now)` per-state matrix (design §5 table): worker-bound states (parametrized over all five
      `WORKER_BOUND_STATES`) → `request_cancellation` recorded, NO record write by the use case (CXL-03);
      PENDING / QUEUED / INTERRUPTED → record → CANCELLED AND control file written (web legitimacy per D7 —
      no live worker; the QUEUED case is CXL-07's cancellation-recorded half); terminal states (parametrized
      over all three) → ZERO writes, control files untouched (CXL-06); non-owner → `JobNotOwned` raised before
      any write (CXL-02 use-case level); unknown id → `JobNotFound` (CXL-08 use-case level); owner proceeds
      (CXL-01 use-case level). Fake storage records every write for the zero-write assertions.
- [x] 4.4 GREEN: `usecases/cancel_job.py` (new) — classification via the domain frozensets; `require_owner`
      first; injected `now` (clock seam, `WebDependencies.now` precedent).
- [x] 4.5 RED: `tests/unit/adapters/web/test_cancel_route.py` (new) — `POST /api/jobs/{id}/cancel`: owner
      cancels a worker-bound job → 200 `CancelJobResponse(job_id, state)` immediately — the recording takes
      effect without waiting for any boundary (CXL-01); non-owner → 403, control file NOT created or modified,
      record unchanged (CXL-02); malformed AND unknown id → 404 (CXL-08); unauthenticated → 401 via the
      route-table gate automatically (AUTH-02 reinforcement); precedence 401 → 404 → 403.
- [x] 4.6 GREEN: `adapters/web/routers/jobs.py` cancel handler (`_authorized` → `_load` → `require_owner` →
      `cancel_job`); `adapters/web/schemas.py` — `CancelJobResponse`; `JobNotOwned` → 403 translation reused.
- [x] 4.7 RED: `tests/unit/usecases/test_cancel_boundary.py` (new) — boundary behavior of the EXISTING seam,
      now spec'd (characterization expected): a job cancelled mid-run stops transcribing after the current
      chunk boundary and the terminal CANCELLED state is recorded by the WORKER path (single-writer intact)
      (CXL-04); a cancellation recorded before the first chunk yields zero completed chunks and zero
      transcriber calls (CXL-05); the OWN-05 cancel arm: non-owner cancellation → 403 with nothing touched —
      completing the parametrized mutation-class matrix (upload 2.11, purge 2.21, cancel here). **OWN-05
      closes here.**
- [x] 4.8 GREEN: expected green — the chunk-boundary poll exists (`transcribe_job`); any failure is a real
      seam gap fixed minimally. Record as characterization where applicable.
- [x] 4.9 RED: `tests/unit/adapters/web/test_upload_state_guard.py` (new) — the upload-path guard D9's
      PENDING-cancellation entails: upload to a record that is not PENDING (cancelled, extracting) → 409
      BEFORE any byte is accepted (early check); a record cancelled mid-stream → the late re-read just before
      `save_media` discards the stored bytes through the existing `discard` seam and answers 409.
- [x] 4.10 GREEN: `adapters/web/routers/jobs.py` upload handler — early state check after ownership, late
      re-read + discard before persisting.
- [x] 4.11 DONE-UNIT: both definition-of-done commands green; measure; pre-declared split seam if exceeded
      (not expected): use case + route (4.1–4.8) vs upload state guard (4.9–4.10).

### Split taken, and why the pre-declared seam was not enough

Split into **four** units, not the two 4.11 pre-declared. The two-way seam would have put 4.1–4.8 in one
unit: measured after the fact, that is 1,107 lines — the same 3–4x overrun every prior slice hit, and it
would have needed an exception like slice 1's rather than a split.

Measured, in delivery order:

| Unit | Tasks | Lines | vs 400 budget |
| --- | --- | --- | --- |
| S4a — domain classification + `cancel_job` | 4.1–4.4 | 412 | +12 |
| S4b — route + `CancelJobResponse` | 4.5–4.6 | 272 | −128 |
| S4c — boundary characterization + OWN-05 matrix | 4.7–4.8 | 423 | +23 |
| S4d — upload state guard | 4.9–4.10 | 270 | −130 |
| **Total** | | **1,377** | **2.5x the ~560 estimate** |

Two things worth carrying forward:

- **The 4x multiplier held at the slice level, not the unit level.** Estimating `560 × 2.5` and dividing by
  400 predicts four units, which is what it took. The prior slices' "overrun" was never a estimation error
  about the work — it was a splitting error.
- **Test share was 79%** (1,092 of 1,377), at the top of the 61–81% band and again nowhere near the plan's
  56%. The `src` half of this whole slice is 285 lines.

**Deviation from the task text.** 4.7 names the OWN-05 matrix as living in `test_cancel_boundary.py`. It
was written instead as `tests/unit/adapters/web/test_mutation_ownership_matrix.py`, generated from the
registered route table the way the 401 gate is. Listing the mutating routes by hand would have proven the
two that exist and nothing about the next one; derived, a future mutating route that forgets `_owned`
fails the default run the day it is written.

## Slice 5 — Capacity gate (Units S5a + S5b, PR 6 + PR 7)

Closes: CAP-01..14 (13/14 shared with S6b docs), CXL-07 gate half.
Liveness in this slice is the injected pid probe (existing `LivenessProbe` precedent); S6 upgrades the
single liveness definition to pid ∧ heartbeat and rewires both reconcile and drain to it.

### Unit S5a (PR 6, expected ~420) — gated starter + drain_once

Rollback boundary: `runtime/settings.py` (cap), `adapters/web/routers/jobs.py` (upload persists QUEUED),
`runtime/app.py` (`drain_once`), `runtime/worker.py` (terminal-state guard), `tests/unit/runtime/`,
web tests rewritten for the new upload contract. Runtime harness: N/A — fakes + injected launcher/clock/liveness.

- [x] 5.1 RED: `tests/unit/runtime/test_settings_capacity.py` (new) — `Settings.max_concurrent_jobs: int`,
      env `ONEVOICECUT_MAX_CONCURRENT_JOBS`, default 1 (D2); a value below 1 or non-integer fails
      construction at the composition root — fail-closed like D3 and `require_binaries()`.
- [x] 5.2 GREEN: `runtime/settings.py` — the field with the `>= 1` constraint.
- [x] 5.3 RED: `tests/unit/adapters/web/test_upload_queues.py` (new) + rewrite of `test_job_start.py` —
      upload NEVER spawns: owner upload completes validation, `save_media` runs, the record is persisted
      `QUEUED` (the gate's one write, before any spawn — CAP-01, CAP-11 write side), response 204, recording
      fake launcher never called; an admitted job without media still reads PENDING, never QUEUED (CAP-02);
      the old "upload calls the starter" expectations in `test_job_start.py` are replaced by the queue
      contract (the supervisor becomes the only spawn decision point — D7).
- [x] 5.4 GREEN: `adapters/web/routers/jobs.py` — upload persists QUEUED instead of calling `start_job`;
      `WebDependencies.start_job` retires from the upload path (field removal lands with the supervisor
      wiring in 5.10).
- [ ] 5.5 RED: `tests/unit/runtime/test_drain_once.py` (new) — `drain_once(storage, *, max_concurrent_jobs,
      launch, is_alive, spawned, now)` over fakes: active is DERIVED each sweep from `list_jobs()` ∩
      `WORKER_BOUND_STATES` ∩ `is_alive` — never a counter (two sweeps over identical records re-derive the
      same count from nothing persisted in between); cap arithmetic parametrized over N — with N active, an
      (N+1)th completed upload stays QUEUED and derived active remains N (CAP-03); no sweep ever launches past
      N (CAP-04); FIFO by ULID/creation order — older QUEUED spawns before newer (CAP-07); re-read before
      spawn: a job cancelled while queued is NOT spawned and stays CANCELLED (CAP-09 — closes CXL-07's gate
      half with 4.3); the spawned-awaiting-claim dedup set: a launched-but-unclaimed id is not re-launched on
      the next sweep and is pruned once its record reads worker-bound or no longer QUEUED (D7 — the set
      idempotizes issuance and is NOT a counter); a worker-bound record with a dead pid is not counted — its
      slot frees to queued work (CAP-06 drain half); after a fake launch, the storage write log shows NO gate
      write to that record (CAP-11); startup reconcile over a directory containing QUEUED records leaves them
      queued — no INTERRUPTED written for them (CAP-12, regression lock ahead of S6); gate transitions carry
      `owner` through unchanged (OWN-02 lock).
- [ ] 5.6 GREEN: `runtime/app.py` — `drain_once` per the mechanics above (re-read via `load_job`, FIFO via
      the ULID-sorted `list_jobs()`, dedup `set[JobId]` passed in, injectable launcher/liveness/clock).
- [ ] 5.7 RED: `tests/unit/runtime/test_worker_terminal_guard.py` (new) — CAP-10's containment: `run_job` on
      a record already in a terminal state (the spawn-wins race: cancelled while the worker started) exits
      immediately — no claim write (`worker_pid` NOT written), zero extractor/transcriber calls, returns the
      record unchanged; end-to-end race shape with fakes: spawn wins → worker observes the cancellation before
      the first chunk → zero chunks transcribed (combines the guard with the 4.7 boundary poll; CXL-05
      reinforcement).
- [ ] 5.8 GREEN: `runtime/worker.py` — the terminal-state guard after `load_job`, before the claim.
- [ ] 5.9 DONE-UNIT: both definition-of-done commands green; measure; pre-declared split seam if exceeded
      (not expected): settings+upload-QUEUED (5.1–5.4) vs drain_once+guard (5.5–5.8).

### Unit S5b (PR 7, expected ~240) — supervisor, restart semantics, rollback codec locks

Rollback boundary: `runtime/app.py` (supervisor + lifespan wiring), `runtime/settings.py` field consumption,
new tests. Runtime harness: N/A — asyncio supervisor driven by injected interval/clock; no real `Popen`.

- [ ] 5.10 RED: `tests/unit/runtime/test_drain_supervisor.py` (new) — the lifespan supervisor (D7):
      `build_app`'s lifespan runs `require_binaries()` → reconcile → supervisor start, in that order
      (reconcile-before-drain: a restart after a crash hands reclaimed slots to queued work on the first
      sweep); sweeps at `DRAIN_SWEEP_INTERVAL_S = 5.0` (test injects a short interval or drives `drain_once`
      directly and asserts the task scheduling); an exception inside one sweep is caught, reported to stderr,
      and the loop CONTINUES (a dead supervisor would silently strand the queue); shutdown cancels the task;
      `WebDependencies.start_job` / `no_job_starter` removal verified (the supervisor is the only code that
      calls the launcher).
- [ ] 5.11 GREEN: `runtime/app.py` — `drain_supervisor` lifespan task; `build_dependencies` wires the real
      `spawn_worker` launcher + `max_concurrent_jobs` from Settings; the retired `start_job` field and its
      refusing default are removed from `adapters/web/app.py`.
- [ ] 5.12 RED: restart semantics — CAP-05: a fresh app+storage instance over the same `tmp_path`
      (simulated web restart) with worker-bound records and live pids derives the active count from disk; no
      counter survives because none exists; CAP-08: persisted QUEUED jobs drain as slots free after the
      restart — none lost, none re-admitted; CAP-06 reinforcement through the supervisor loop (a dead worker's
      slot reaches a queued job within one sweep).
- [ ] 5.13 GREEN: expected largely green from 5.6/5.11 wiring — record as characterization; any real gap
      fixed here.
- [ ] 5.14 RED: `tests/unit/adapters/storage/test_rollback_codec.py` (new) — CAP-13/14 codec halves: a
      simulated pre-change decode (field-explicit pre-change field set, state mapped through the pre-change
      enum set WITHOUT `"queued"`) reads a post-change record carrying `owner` and a terminal state cleanly,
      ignoring the unknown owner key (LEG-08 reinforcement, CAP-13); the same decode of a QUEUED record raises
      `CorruptedRecord` — fail closed (CAP-14: THIS is why the queue must drain, or QUEUED directories move
      out, before any rollback; the operational procedure is documented in 6.12).
- [ ] 5.15 GREEN: expected green from the S1 codec shape — regression lock; note the operational remainder
      lands in S6b.
- [ ] 5.16 DONE-UNIT: both definition-of-done commands green; measure.

## Slice 6 — Hardening + docs (Units S6a + S6b, PR 8 + PR 9)

Closes: HARD-01..09, LEG-09, CAP-13 (procedure half); docs deltas per proposal §11.

### Unit S6a (PR 8, expected ~540) — heartbeat + combined liveness + reconcile extension

Rollback boundary: `ports/transcript_storage.py` (two methods), `adapters/storage/filesystem_transcript_storage.py`
(heartbeat implementation), `tests/fakes/transcript_storage.py`, `runtime/worker.py` + `usecases/transcribe_job.py`
(boundary writes), `runtime/app.py` (`worker_is_alive`, extended reconcile, drain rewire, docstring), new tests.
Runtime harness: N/A — injected clock/liveness per design §8; no wall-clock sleeps in the default run.

- [ ] 6.1 RED: `tests/unit/adapters/storage/test_heartbeat.py` (new) — the two new port methods (structural
      `Protocol`, no ABC): `write_heartbeat(job_id, *, at_s)` and `heartbeat_is_fresh(job_id, *, now_s,
      stale_after_s) -> bool`; filesystem implementation: `HEARTBEAT` layout constant joins
      `FilesystemTranscriptStorage`'s layout, atomic write (`.tmp` + fsync + `os.replace`) — a torn heartbeat
      must not read as fresh; content is one epoch float; fresh ⇔ file exists ∧ parseable ∧
      `now - value <= stale_after_s` (boundary equality is fresh); absent file → not fresh; unparseable
      content → not fresh (fail closed). Fakes implement both methods and record calls.
- [ ] 6.2 GREEN: `ports/transcript_storage.py`, `adapters/storage/filesystem_transcript_storage.py`,
      `tests/fakes/transcript_storage.py`.
- [ ] 6.3 RED: `tests/unit/runtime/test_worker_heartbeat.py` + `tests/unit/usecases/test_transcribe_job.py`
      extensions — HARD-01: `run_job` writes a fresh heartbeat immediately after claiming the job (injected
      clock); HARD-02: `transcribe_job` touches it at EVERY chunk boundary, adjacent to the existing
      cancellation poll, freshness reflecting the latest boundary; HARD-03: across composite scenarios
      (upload, cancel, drain sweeps, reconcile) the fake's call log shows `write_heartbeat` invoked ONLY by
      the worker path — web, gate, and reconcile never write it (single-writer rule intact); owner-bearing
      records keep their owner through every heartbeat-era transition (OWN-02 final lock).
- [ ] 6.4 GREEN: `runtime/worker.py` start write; `usecases/transcribe_job.py` boundary writes through an
      injected `now` callable with `time.time` default (`WebDependencies.now` precedent; design §8 seam).
- [ ] 6.5 RED: `tests/unit/runtime/test_worker_is_alive.py` (new) — the SINGLE liveness definition (D5/D6):
      `worker_is_alive(job, storage, *, is_alive, now)` — live pid ∧ fresh heartbeat → alive (HARD-04); live
      pid ∧ STALE heartbeat → NOT alive (HARD-05: the pid-reuse veto — a stale heartbeat vetoes a live pid);
      dead pid ∧ fresh heartbeat → NOT alive (HARD-06: a dead pid vetoes any freshness); missing
      `worker_pid` → not alive; `HEARTBEAT_STALE_AFTER_S = 7200` is a named constant, not configuration.
- [ ] 6.6 GREEN: `runtime/app.py` — `worker_is_alive` + `HEARTBEAT_STALE_AFTER_S`.
- [ ] 6.7 RED: `tests/unit/runtime/test_reconcile_extended.py` (new) — reconcile covers ALL worker-bound
      states, parametrized over the five `WORKER_BOUND_STATES`: dead worker per combined liveness →
      INTERRUPTED (HARD-07); live workers per combined liveness untouched, records unchanged (HARD-08);
      PENDING/QUEUED/terminal states untouched (HARD-09 — CAP-12 preserved under the extension); stale
      heartbeat + live pid → INTERRUPTED at reconcile (HARD-05 reconcile half); legacy and owned worker-bound
      records processed alike, owner preserved (LEG-06 reinforcement); heartbeat files left in place — nobody
      removes them (D5); a job directory containing heartbeat and control files lists exactly as before —
      files inside a job directory are never enumerated as jobs (LEG-09 listing half).
- [ ] 6.8 GREEN: `runtime/app.py` — `reconcile_interrupted_jobs` scoped to `WORKER_BOUND_STATES` with
      `worker_is_alive` as the liveness rule; `drain_once` rewired from the bare pid probe to the SAME
      `worker_is_alive` helper (single definition — reconcile and capacity derivation cannot drift);
      `process_is_alive` docstring rationale rewritten (its "single-operator machine" premise is now false;
      the heartbeat closes the reuse window it accepted); LEG-09 control-file half: the existing control-file
      poll remains the cancellation signal regardless of which build wrote it (locked by the 4.7 tests,
      re-asserted here).
- [ ] 6.9 DONE-UNIT: both definition-of-done commands green; measure; pre-declared split seam if exceeded
      (not expected): heartbeat port/adapter/worker writes (6.1–6.4) vs liveness rule + reconcile extension
      (6.5–6.8).

### Unit S6b (PR 9, expected ~180) — docs deltas (proposal §11), explicit and named

Docs-only unit: no behavior changes to test-drive; verification is that BOTH definition-of-done commands stay
green with zero source changes in the diff, and that every falsified premise below is updated. Rollback
boundary: `README.md`, `CLAUDE.md` only.

- [ ] 6.10 DOCS: `README.md` premise — line 9 "Single operator, runs locally." → several operators against
      ONE shared server (proposal §11.1).
- [ ] 6.11 DOCS: `README.md` "Running it" — operator-token configuration via `ONEVOICECUT_OPERATOR_TOKENS`
      (`name:token;name:token` grammar, `secrets.token_urlsafe(32)` for generation, rotation = edit +
      restart); `ONEVOICECUT_MAX_CONCURRENT_JOBS` (default 1); the `Authorization: Bearer <token>` header in
      every example request; the two new routes (`GET /api/jobs`, `POST /api/jobs/{id}/cancel`) with 401/403
      semantics; QUEUED semantics — upload queues, the drain supervisor spawns FIFO, and the mandatory
      drain-or-move step before any rollback to a pre-change build (CAP-13/14 operational halves, proposal
      §10) (proposal §11.2).
- [ ] 6.12 DOCS: `CLAUDE.md` — intro "A single-operator local app" → shared-server multi-operator (§11.3);
      HTTP surface "Three HTTP routes exist" → five, with the auth/authz invariants in the security section:
      401 on every route, owner-only mutation (403), deny-by-default enforced by the route-table test (§11.4);
      review-budget drift fixed: "400 lines per slice" → 800 per `openspec/config.yaml` (raised 2026-08-31)
      (§11.5).
- [ ] 6.13 DONE-CHANGE (final): both definition-of-done commands green over the full stack; confirm
      `tests/test_architecture.py` unchanged; confirm no default-run test called a paid API or loaded model
      weights (marker exclusions intact); measure the final cumulative diff; the coverage cross-check below
      shows 68/68 scenarios closed.

---

## Scenario Coverage Cross-Check (68/68)

Every scenario appears in exactly the task(s) that close it. "Closes" names the task where the scenario's
normative content is fully proven; supporting tasks are in parentheses.

| Capability | Scenario → closing task(s) (unit) |
| --- | --- |
| AUTH (9) | AUTH-01 → 2.3/2.4 (S2a) · AUTH-02 → 2.7/2.8 (S2a; auto-extended by 3.4, 4.6) · AUTH-03 → 2.3/2.4 (S2a) · AUTH-04 → 2.3/2.4 + 2.7 (S2a) · AUTH-05 → 2.3/2.4 + 2.7 (S2a) · AUTH-06 → 2.7/2.8 (S2a) · AUTH-07 → 2.15/2.16 (S2a) · AUTH-08 → 2.1/2.2 + 2.15/2.16 (S2a) · AUTH-09 → 2.1 + 2.9 (S2a) + 2.17/2.18 (S2b) |
| OWN (11) | OWN-01 → 2.9/2.10 (S2a) · OWN-02 → 2.19/2.20 (S2b; locked by 5.5, 6.3) · OWN-03 → 2.11/2.12 (S2a) · OWN-04 → 2.11/2.12 (S2a) · OWN-05 → arms 2.11 (S2a) + 2.21/2.22 (S2b); **closes 4.7** (S4) · OWN-06 → 2.21/2.22 (S2b) · OWN-07 → 2.13/2.14 (S2a) · OWN-08 → 2.11/2.12 (S2a) · OWN-09 → 2.11/2.12 (S2a) · OWN-10 → 2.11/2.12 (S2a) · OWN-11 → 2.23/2.24 (S2b) |
| VIS (8) | VIS-01 → 3.3/3.4 · VIS-02 → 3.3/3.4 · VIS-03 → 3.3/3.4 · VIS-04 → 3.3/3.4 · VIS-05 → 3.3/3.4 · VIS-06 → 3.1/3.2 · VIS-07 → 3.5/3.6 · VIS-08 → 3.5/3.6 (structural start 2.13) — all S3 |
| CXL (8) | CXL-01 → 4.3/4.4 + 4.5/4.6 · CXL-02 → 4.3 + 4.5 · CXL-03 → 4.3/4.4 · CXL-04 → 4.7/4.8 · CXL-05 → 4.7/4.8 (+ 5.7) · CXL-06 → 4.3/4.4 · CXL-07 → 4.3 (record+control) + **5.5/5.6 gate half closes it** · CXL-08 → 4.5/4.6 — all S4 (+5.5) |
| CAP (14) | CAP-01 → 5.3/5.4 · CAP-02 → 5.3/5.4 · CAP-03 → 5.5/5.6 · CAP-04 → 5.5/5.6 · CAP-05 → 5.12/5.13 · CAP-06 → 5.5/5.6 + 5.12 (refined by 6.5–6.8) · CAP-07 → 5.5/5.6 · CAP-08 → 5.10/5.11 + 5.12 · CAP-09 → 5.5/5.6 · CAP-10 → 5.7/5.8 (+ 4.7 poll) · CAP-11 → 5.3 + 5.5 · CAP-12 → 5.5 (lock) + **6.7/6.8 closes under the extended reconcile** · CAP-13 → 5.14/5.15 (codec) + 6.11 (procedure) · CAP-14 → 5.14/5.15 — S5 (+6.11) |
| LEG (9) | LEG-01 → 1.3/1.4 (+ 1.5) · LEG-02 → 1.3/1.4 · LEG-03 → 1.1/1.2 + 1.3/1.4 · LEG-04 → 1.3/1.4 · LEG-05 → 1.5/1.6 · LEG-06 → 1.5/1.6 (+ 6.7) · LEG-07 → 1.5/1.6 · LEG-08 → 1.5/1.6 (+ 5.14) · LEG-09 → 6.7/6.8 |
| HARD (9) | HARD-01 → 6.3/6.4 · HARD-02 → 6.3/6.4 · HARD-03 → 6.3/6.4 · HARD-04 → 6.5/6.6 · HARD-05 → 6.5/6.6 (+ 6.7) · HARD-06 → 6.5/6.6 · HARD-07 → 6.7/6.8 · HARD-08 → 6.7/6.8 · HARD-09 → 6.7/6.8 |

**Total: 9 + 11 + 8 + 8 + 14 + 9 + 9 = 68 scenarios, all assigned.**

## Residuals (design §9) — accepted, no tasks

RR1 (duplicate spawn across a web crash between `Popen` and claim — bounded window, contained by the
terminal-state guard), RR2 (a hung worker past the 7200 s bound may let the drain spawn one worker beyond
true load until restart reconcile — the price of closing pid-reuse), RR3 (an invalid body can draw a 422
before authentication — no state change; moving auth ahead would require the rejected middleware/`Depends`).
All three are documented acceptances; no implementation task is owed for them.
