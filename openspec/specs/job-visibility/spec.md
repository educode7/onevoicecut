# Job Visibility Specification

## Purpose

The maintainer confirmed the shared visibility model (proposal decision 3, V2): one ministry team
cutting the same church's sermons genuinely needs "is Sunday's sermon done?". Read access to every
job is therefore collaboration, not leakage; the sensitive surfaces are mutation (`job-ownership`)
and machine capacity (`worker-capacity-gate`), both of which stay owner-protected. Visibility is a
read model only: the underlying job listing stays unscoped, because startup reconcile depends on
seeing *all* jobs — including legacy records without an owner.

## Requirements

### Requirement: Authenticated Read Access To Every Job

Any authenticated operator MUST be able to read the status of any job regardless of owner. Read
access MUST be read-only: status reads MUST NOT change job state, write any file, or spawn any
process.

#### Scenario: VIS-01 — Foreign job is readable

- GIVEN a job admitted by operator "a" in any state
- WHEN operator "b" (authenticated, not the owner) reads that job's status
- THEN the system MUST respond 200 with the job's current state
- AND the response MUST carry the job's owner attribution

#### Scenario: VIS-02 — Reading writes nothing

- GIVEN any authenticated operator reading any job's status
- WHEN the status is derived and returned
- THEN no file MUST be created or modified, no state MUST change, and no process MUST be spawned

### Requirement: Complete Listing With Owner Attribution

The system MUST provide a listing route that returns every job to any authenticated
operator — complete across the union of its pages when paginated (see Bounded Pagination On
The Listing) — each item attributed to its owner. The listing MUST be derived from the
unscoped job listing (the same listing reconcile uses). No job MUST be hidden from any
authenticated operator.
(Previously: completeness required exactly N items in a single response; there were no
pagination parameters, so "complete" and "one response" were the same statement.)

#### Scenario: VIS-03 — List returns every operator's jobs with attribution

- GIVEN jobs admitted by operators "a" and "b"
- WHEN any authenticated operator requests the listing (through the union of its pages when
  paginated)
- THEN the listing MUST include every job of both operators
- AND each item MUST attribute the job to its owner

#### Scenario: VIS-04 — Legacy jobs surface in the listing

- GIVEN jobs persisted before this change (no owner)
- WHEN any authenticated operator requests the listing (through the union of its pages when
  paginated)
- THEN those jobs MUST appear in the listing
- AND their owner attribution MUST be null

#### Scenario: VIS-05 — Nothing is hidden

- GIVEN a data directory containing N jobs in any mixture of owned and legacy records
- WHEN any authenticated operator pages through the listing with a bounded page size
- THEN the union of the pages MUST contain exactly N items
- AND every job MUST appear exactly once across the union
- AND no scoping by caller identity MUST remove items from the unfiltered page union

## Open Questions

- **Default page size when `limit` is omitted.** The proposal specifies `limit ≤ 100` and
  offset paging but does not say whether an omitted `limit` returns the full listing or
  applies a bounded default. VIS-03/VIS-04 are written page-union-aware so either answer
  holds; design must pick one before tasks write the parameter tests.
- **Upper bound for `offset`.** OWASP API4 asks for a "bounded offset"; the proposal gives
  no numeric bound for `offset` (only `limit ≤ 100`). Design must choose a bound (or accept
  unbounded non-negative) before a 422 scenario can name it.
### Requirement: Additive Owner Field On Job Responses

Job status responses and listing items MUST gain the owner field additively: every field present
before this change MUST keep its name and meaning, so existing and future clients remain compatible.
The owner field MUST carry the operator identity (a name) or null; a token value MUST NOT appear in
any response (see `operator-authentication`: Token Values Never Leave The Composition Root).

#### Scenario: VIS-06 — Status response is backward compatible and attributed

- GIVEN a client shaped against the pre-change status response
- WHEN it reads the status of an owned job after this change
- THEN every pre-change field MUST be present with unchanged meaning
- AND an additional owner field MUST carry the owning operator's identity

### Requirement: Server-Side Mine-Only Filtering

An optional "mine only" filter MUST be expressed as a boolean that the server resolves against the
authenticated caller's identity. The system MUST NOT accept an operator identity as a request
parameter — not in the body, not in a header, not in the query — for filtering or any other purpose.

#### Scenario: VIS-07 — Mine filter returns only the caller's jobs

- GIVEN jobs admitted by operators "a" and "b"
- WHEN operator "a" requests the listing with the mine-only filter enabled
- THEN the response MUST contain exactly operator "a"'s jobs
- AND no foreign job MUST appear

#### Scenario: VIS-08 — Operator identity parameters are never honored

- GIVEN operator "b" authenticated
- WHEN operator "b" requests the listing with a mine-only filter and a client-supplied operator
  identity naming operator "a"
- THEN the system MUST NOT resolve the filter against operator "a"
- AND the result MUST be computed solely from the authenticated caller ("b"), or the request MUST
  be rejected; it MUST NOT return operator "a"'s jobs selected by the supplied parameter
### Requirement: Bounded Pagination On The Listing

The listing route (GET /api/v1/jobs) MUST accept bounded pagination parameters: `limit` MUST
be an integer in the range 1..100, and `offset` MUST be a non-negative integer. A `limit`
above 100, a `limit` below 1, a non-integer value, or a negative `offset` MUST be rejected
with HTTP 422 before any listing work. Pagination MUST apply after server-side filtering
(for example the mine filter), never as a re-scoping of the store's unscoped listing
underneath — the same unscoped listing reconcile uses remains the source.

#### Scenario: VIS-09 — Page size is honored

- GIVEN a data directory containing 5 jobs
- WHEN an authenticated operator requests the listing with `limit=2&offset=0`
- THEN the response MUST contain at most 2 items
- AND each returned item MUST carry owner attribution exactly as in an unpaginated listing

#### Scenario: VIS-10 — Over-maximum limit is rejected

- GIVEN any authenticated operator requesting the listing
- WHEN the request carries `limit=101`
- THEN the system MUST respond 422
- AND no listing MUST be computed or returned

#### Scenario: VIS-11 — Malformed pagination parameters are rejected

- GIVEN any authenticated operator requesting the listing
- WHEN the request carries a non-integer `limit`, a `limit` of 0 or a negative value, or a
  negative or non-integer `offset`
- THEN the system MUST respond 422
- AND no listing MUST be computed or returned

#### Scenario: VIS-12 — Mine filter composes with pagination

- GIVEN jobs admitted by operators "a" (3 jobs) and "b"
- WHEN operator "a" requests the listing with the mine-only filter and `limit=2`
- THEN the returned page MUST contain only operator "a"'s jobs
- AND the union of pages fetched with the mine filter MUST equal exactly operator "a"'s
  jobs

### Requirement: Explicit Allow-List Listing Responses

Listing responses MUST be projected through explicit response schemas that enumerate every
permitted field (an allow-list). The system MUST NOT serialize undeclared fields, domain
records, or storage records directly, and MUST NOT include token values in any listing
response (see `operator-authentication`: Token Values Never Leave The Composition Root).

#### Scenario: VIS-13 — Response contains only declared fields

- GIVEN a listing response (wrapper and items)
- WHEN its JSON keys are compared to the explicit response schema
- THEN every serialized key MUST be declared in the schema
- AND no undeclared key MUST appear

#### Scenario: VIS-14 — A planted undeclared field fails the default run

- GIVEN a listing item that would serialize a field not declared in the response schema
- WHEN the default test run executes the listing response contract check
- THEN the default test run MUST fail naming the undeclared field

