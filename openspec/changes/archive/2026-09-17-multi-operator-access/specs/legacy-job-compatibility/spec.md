# Legacy Job Compatibility Specification

## Purpose

Operator machines hold job records written before this change. Record decoding is field-explicit
and fail-closed, so a *required* owner field would turn every pre-change record into a corrupt
record — and since job listing re-raises decode failures rather than skipping them, startup
reconcile would brick boot on any existing data directory. This capability makes the owner an
optional, key-tolerant field: legacy records decode with no owner, boot and listing survive mixed
populations, and — because the field is additive — post-change records remain readable after
reverting to the pre-change build. Forward and backward compatibility are one requirement seen from
two directions, and rollback safety is the reason the owner field MUST never become required.

## Requirements

### Requirement: Owner Decodes As Optional

A job record with the owner key absent MUST decode with owner none, and a record with owner null
MUST decode with owner none. A present owner string MUST be validated as an operator identifier.
The owner key MUST NOT be required in either build direction.

#### Scenario: LEG-01 — Pre-change record decodes without owner

- GIVEN a job record persisted before this change (no owner key at all)
- WHEN the record is decoded by the post-change build
- THEN decoding MUST succeed with owner none
- AND the record MUST NOT be treated as corrupt

#### Scenario: LEG-02 — Explicit null owner decodes as none

- GIVEN a job record whose owner key is present and null
- WHEN the record is decoded
- THEN decoding MUST succeed with owner none

#### Scenario: LEG-03 — Present owner decodes to the validated identity

- GIVEN a job record whose owner is a valid operator identity string
- WHEN the record is decoded
- THEN decoding MUST succeed with owner equal to that operator identity

#### Scenario: LEG-04 — Invalid owner string fails closed

- GIVEN a job record whose owner is a string that fails operator-identity validation
- WHEN the record is decoded
- THEN the record MUST be treated as corrupt at the boundary (fail closed)
- AND it MUST NOT be silently coerced to none or to any invented identity

### Requirement: Legacy Records Never Corrupt Boot Or Listing

The presence of legacy records MUST NOT fail boot, reconcile, or listing. Reconcile MUST keep
seeing every job, including legacy ones. No boot-time backfill MUST invent an owner; legacy records
keep owner none. The mutability semantics of legacy jobs (immutable forever versus claimable) are
design dependency U2; whichever semantics design chooses, the invariants in this requirement MUST
hold.

#### Scenario: LEG-05 — Boot succeeds on a legacy-only data directory

- GIVEN a data directory containing only pre-change records
- WHEN the post-change build starts
- THEN boot MUST succeed
- AND the listing MUST return every legacy record with owner none

#### Scenario: LEG-06 — Mixed populations list and reconcile completely

- GIVEN a data directory mixing legacy records (owner none) and post-change records (owned)
- WHEN the post-change build starts and lists jobs
- THEN every record of both kinds MUST be listed with correct owner attribution (null for legacy)
- AND startup reconcile MUST process worker-bound jobs of both kinds alike

#### Scenario: LEG-07 — No fictional owner is invented at boot

- GIVEN legacy records with owner none at boot
- WHEN the post-change build starts
- THEN boot MUST NOT rewrite legacy records to add an owner
- AND no record's bytes MUST change absent a real lifecycle transition

### Requirement: Post-Change Records Readable By The Pre-Change Build

The owner field MUST be additive: a record carrying owner MUST decode cleanly under pre-change
decode rules, which read exactly their known fields and ignore unknown keys. Rollback MUST NOT
require rewriting or cleaning records. Persisted operator *names* in records read by the old build
are inert, and token values are never persisted (`operator-authentication`: Token Values Never
Leave The Composition Root), so no secret cleanup is owed by a rollback.

#### Scenario: LEG-08 — Owned record round-trips through the pre-change shape

- GIVEN a job record written by the post-change build with owner "a"
- WHEN it is decoded using the pre-change field set
- THEN decoding MUST succeed with the unknown owner key ignored
- AND every pre-change field MUST decode with unchanged meaning

### Requirement: Auxiliary Files Are Rollback-Safe

Files introduced or written by the post-change build MUST NOT break the pre-change build. Control
files written by the cancellation route remain valid cancellation signals for workers of either
build (workers poll them regardless of which code version wrote them). Worker liveness files MUST
be ignored by pre-change listing and reconcile: files inside a job directory are never enumerated
as jobs.

#### Scenario: LEG-09 — Pre-change build tolerates new auxiliary files

- GIVEN a data directory containing job records alongside control files and worker liveness files
  written by the post-change build
- WHEN the pre-change build starts against it
- THEN boot and listing MUST succeed exactly as before this change
- AND any control file present MUST still function as a cancellation signal for workers
