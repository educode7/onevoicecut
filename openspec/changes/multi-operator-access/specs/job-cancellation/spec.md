# Job Cancellation Specification

## Purpose

The cancellation seam already existed in the storage port — a control file polled by the worker at
chunk boundaries — but had no route: an operator could not stop a runaway multi-hour job. On a
shared machine, one stuck job consumes capacity that belongs to everyone. This capability exposes
the seam over HTTP with ownership verification, preserving the single-writer rule: the web process
writes the control file, the worker polls it at chunk boundaries, and the worker remains the sole
writer of the job record.

## Requirements

### Requirement: Owner-Only Cancellation Route

The system MUST provide a cancellation route (POST /api/jobs/{id}/cancel). Authentication MUST
precede handling (401 without a valid token, per `operator-authentication`). The caller MUST own
the job; a non-owner attempt MUST be denied with 403 and MUST NOT cause any change.

#### Scenario: CXL-01 — Owner cancels a running job

- GIVEN a job owned by operator "a" with a live worker in a worker-bound state
- WHEN operator "a" requests cancellation
- THEN the cancellation MUST be recorded and the request MUST succeed
- AND the recording MUST take effect without waiting for the worker to reach a boundary

#### Scenario: CXL-02 — Non-owner cancellation is denied with nothing touched

- GIVEN a job owned by operator "a"
- WHEN operator "b" (authenticated, not the owner) requests cancellation
- THEN the system MUST respond 403
- AND the job's control file MUST NOT be created or modified, the job record MUST be unchanged, and
  the worker MUST be unaffected

### Requirement: Cancellation Through The Control-File Seam

Cancellation MUST be recorded through the existing control-file mechanism: the web process MUST NOT
write the job record to effect a cancellation. The worker MUST observe cancellation at chunk
boundaries, including before the first chunk. The terminal outcome of a cancelled run MUST be
recorded by the worker through the existing cancellation exit path (the finer machine-code mapping
of that outcome is design dependency U6).

#### Scenario: CXL-03 — Web process writes only the control file

- GIVEN the owner cancels a running job
- WHEN the cancellation is recorded
- THEN the control file MUST exist for the job
- AND the job record MUST NOT have been written by the web process as part of the cancellation

#### Scenario: CXL-04 — Worker stops at the next chunk boundary

- GIVEN a running job with a recorded cancellation
- WHEN the worker reaches the next chunk boundary
- THEN the worker MUST stop transcribing further chunks
- AND the job's terminal state MUST be recorded by the worker (single-writer rule intact)

#### Scenario: CXL-05 — Cancellation before the first chunk does zero work

- GIVEN a cancellation recorded before the worker begins its first chunk
- WHEN the worker starts and polls the control file before the first chunk
- THEN the worker MUST cancel with zero chunks completed
- AND no transcription work MUST have been performed for the job

### Requirement: Idempotent Cancellation

Cancellation of a job that is already cancelled or already in a terminal state MUST be idempotent:
the request MUST succeed without error and MUST NOT change state, duplicate control records, or
affect any worker.

#### Scenario: CXL-06 — Cancelling a finished job is a no-op

- GIVEN a job owned by operator "a" already in a terminal state
- WHEN operator "a" requests cancellation again
- THEN the request MUST succeed
- AND the job record, artifacts, and control files MUST remain exactly as they were

### Requirement: Cancellation Of Queued Jobs

An owner MUST be able to cancel a job that is queued (waiting for a worker slot, see
`worker-capacity-gate`). A cancelled queued job MUST NOT be spawned afterwards. If the spawn-versus-
cancel race resolves in favor of the spawn, the containment defined above applies: the worker
observes the cancellation before the first chunk and does zero work.

#### Scenario: CXL-07 — Owner cancels a queued job

- GIVEN a job owned by operator "a" persisted in the queued state
- WHEN operator "a" requests cancellation
- THEN the cancellation MUST be recorded
- AND the capacity gate MUST NOT spawn the job afterwards

### Requirement: Unknown Identifier

Cancellation of an unknown or malformed job identifier MUST yield the unknown-identifier outcome
(404), indistinguishable from a nonexistent job, before any filesystem access (see `job-ownership`:
Identifier Validation Precedes Filesystem Access).

#### Scenario: CXL-08 — Cancel with malformed or unknown id

- GIVEN an authenticated operator
- WHEN the operator requests cancellation with a malformed or unknown job identifier
- THEN the system MUST respond 404
- AND nothing MUST be written or mutated
