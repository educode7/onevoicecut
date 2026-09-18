# Worker Liveness Hardening Specification

## Purpose

Liveness decisions — capacity slots (`worker-capacity-gate`) and startup reconcile — historically
trusted a bare pid liveness check that explicitly accepted the pid-reuse risk "on a single-operator
machine": a premise this change falsifies. Several operators mean more processes and more restarts,
widening the reuse window, and the shared job listing makes every stuck job visible to every
operator — an orphaned job stops being private. This capability adds a worker-written heartbeat
file (a derived signal: a file on disk, not a counter; survives restarts; written only by the
worker, so the single-writer rule stays intact) and extends reconcile from one worker-bound state
to all of them, so a dead job is caught in whichever state it died.

## Requirements

### Requirement: Worker Heartbeat File

The worker MUST touch a heartbeat file inside its own job directory at worker start and at every
chunk boundary. The heartbeat file MUST be written only by the worker. It is a derived liveness
signal, not a counter, and MUST NOT require a timer thread: chunk-boundary granularity is
acceptable because startup reconcile is the actual pid-reuse case, and chunk boundaries may be
far apart on multi-hour input.

#### Scenario: HARD-01 — Heartbeat created at worker start

- GIVEN a worker starting on a job
- WHEN the worker begins
- THEN a heartbeat file MUST exist in the job directory with a fresh timestamp

#### Scenario: HARD-02 — Heartbeat refreshed at chunk boundaries

- GIVEN a running worker transcribing a multi-chunk job
- WHEN each chunk boundary is reached
- THEN the heartbeat file MUST be touched again
- AND its freshness MUST reflect the latest boundary

#### Scenario: HARD-03 — Only the worker writes the heartbeat

- GIVEN the web process, the capacity gate, and reconcile running alongside workers
- WHEN any of them operates on a job directory
- THEN none of them MUST write or modify the heartbeat file
- AND the worker MUST remain the sole writer of it

### Requirement: Liveness Combines Pid And Heartbeat Freshness

A job's worker MUST be considered alive if and only if its recorded pid is alive AND its heartbeat
is fresh. Either signal alone MUST NOT suffice. The freshness bound is a design choice; coarse
bounds are acceptable because the guarded case is reconcile after restart, not tight live
monitoring.

#### Scenario: HARD-04 — Live pid with fresh heartbeat is active

- GIVEN a worker-bound job whose recorded pid is alive and whose heartbeat is fresh
- WHEN the system decides liveness (capacity derivation or reconcile)
- THEN the worker MUST be considered alive
- AND the capacity gate MUST count it as active and reconcile MUST leave the job untouched

#### Scenario: HARD-05 — Live pid with stale heartbeat is not trusted

- GIVEN a worker-bound job whose recorded pid is alive but whose heartbeat is stale (the pid-reuse
  case)
- WHEN the system decides liveness
- THEN the worker MUST NOT be considered alive
- AND reconcile MUST mark the job interrupted

#### Scenario: HARD-06 — Dead pid is inactive regardless of heartbeat

- GIVEN a worker-bound job whose recorded pid is dead
- WHEN the system decides liveness
- THEN the worker MUST NOT be considered alive no matter how fresh the heartbeat file is

### Requirement: Reconcile Covers All Worker-Bound States

Startup reconcile MUST cover every state in which a worker is expected to be running — extraction,
planned, transcribing, stitching, and generating — not only transcribing. A job found in any
worker-bound state whose worker is not alive (per the combined liveness definition) MUST be marked
interrupted. The queued state is not worker-bound and MUST be excluded from reconcile
(`worker-capacity-gate`: Queued Excluded From Reconcile).

#### Scenario: HARD-07 — Dead worker in any worker-bound state is interrupted

- GIVEN a job in any worker-bound state — parametrized over extraction, planned, transcribing,
  stitching, and generating — whose worker is not alive
- WHEN startup reconcile runs
- THEN the job MUST be marked interrupted
- AND this MUST hold for every worker-bound state, not only transcribing

#### Scenario: HARD-08 — Live workers are not interrupted

- GIVEN jobs in worker-bound states whose workers are alive per the combined liveness definition
- WHEN startup reconcile runs
- THEN those jobs MUST NOT be marked interrupted
- AND their records MUST remain unchanged

#### Scenario: HARD-09 — Reconcile leaves non-worker-bound states untouched

- GIVEN jobs in states that are not worker-bound (awaiting upload, queued, terminal states)
- WHEN startup reconcile runs
- THEN reconcile MUST NOT change any of them
