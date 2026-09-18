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

The system MUST provide a listing route that returns every job to any authenticated operator, each
item attributed to its owner. The listing MUST be derived from the unscoped job listing (the same
listing reconcile uses). No job MUST be hidden from any authenticated operator.

#### Scenario: VIS-03 — List returns every operator's jobs with attribution

- GIVEN jobs admitted by operators "a" and "b"
- WHEN any authenticated operator requests the listing
- THEN the response MUST include every job of both operators
- AND each item MUST attribute the job to its owner

#### Scenario: VIS-04 — Legacy jobs surface in the listing

- GIVEN jobs persisted before this change (no owner)
- WHEN any authenticated operator requests the listing
- THEN those jobs MUST appear in the listing
- AND their owner attribution MUST be null

#### Scenario: VIS-05 — Nothing is hidden

- GIVEN a data directory containing N jobs in any mixture of owned and legacy records
- WHEN any authenticated operator requests the listing
- THEN the listing MUST contain exactly N items
- AND no scoping by caller identity MUST remove items from the unfiltered listing

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
