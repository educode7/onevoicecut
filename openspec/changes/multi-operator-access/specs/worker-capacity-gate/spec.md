# Worker Capacity Gate Specification

## Purpose

Each media upload spawns one multi-hour worker process; with several operators on one shared
machine, unbounded spawning is a fairness and stability failure, not a theoretical one. This
capability adds a persisted queued state and a cap on concurrently active workers, drained by a
supervisor inside the single long-lived process. It follows the repository's strongest invariant:
capacity is *derived on decision, never counted*. An in-memory counter dies with the web process;
persisted queued records plus a derived count survive restarts, which is the crash-is-normal
posture of this codebase.

## Requirements

### Requirement: Queued State Semantics

The system MUST distinguish "awaiting upload" from "awaiting a worker slot". A job that is admitted
and has its media present, but is waiting for a worker slot, MUST be persisted in a dedicated
queued state distinct from the state meaning "admitted, media not yet uploaded". Conflating the two
MUST NOT corrupt progress display or reconcile semantics.

#### Scenario: CAP-01 — Upload at full capacity queues the job

- GIVEN every worker slot is occupied
- WHEN the owner's media upload completes validation
- THEN the job MUST be persisted in the queued state
- AND no worker MUST be spawned for it yet
- AND the upload response MUST succeed (admission is not rejected for capacity reasons)

#### Scenario: CAP-02 — Awaiting-upload state is unchanged

- GIVEN a job admitted but without uploaded media
- WHEN its status is read
- THEN it MUST remain in the awaiting-upload state, not the queued state

### Requirement: Concurrent Worker Cap

The number of concurrently active workers MUST NOT exceed a configured limit N. A slot is free if
and only if the derived active count is below N. The default value of N, and whether the cap is
global or per-engine, are design dependency U3; scenarios below parametrize N.

#### Scenario: CAP-03 — The N+1th job queues

- GIVEN the configured concurrency limit is N (N >= 1) and exactly N workers are derived active
- WHEN another owner's media upload completes
- THEN the new job MUST be persisted as queued rather than spawned
- AND the derived active count MUST remain N

#### Scenario: CAP-04 — Spawning never exceeds the cap

- GIVEN the configured concurrency limit is N
- WHEN the system makes any spawn decision (at upload completion or during a drain sweep)
- THEN the spawn MUST NOT cause the derived active count to exceed N

### Requirement: Derived Active Count, Never A Counter

The active worker count MUST be derived at each gate decision from persisted job records plus
process liveness, in the same manner reconcile already derives it. The system MUST NOT maintain an
in-memory counter of running workers.

#### Scenario: CAP-05 — Restart derives active count from disk

- GIVEN workers running for jobs while the web process restarts
- WHEN the web process comes back and makes a spawn decision
- THEN the active count MUST be derived from the persisted records and process liveness
- AND the cap MUST hold across the restart without any counter having survived it

#### Scenario: CAP-06 — Dead workers free their slots

- GIVEN a job whose worker process is dead
- WHEN the gate derives the active count
- THEN that job MUST NOT be counted as active
- AND its slot MUST be available to queued jobs

### Requirement: Drain Supervisor

A supervisor within the web process lifespan MUST periodically sweep persisted queued jobs and
spawn the oldest of them while slots are free. Queued jobs MUST survive web restarts on disk, and
the drain MUST resume afterwards.

#### Scenario: CAP-07 — Oldest queued job spawns first when a slot frees

- GIVEN queued jobs Q1 (older) and Q2 (newer) and a cap of N with N workers active
- WHEN one worker finishes and the supervisor sweeps
- THEN Q1 MUST be spawned before Q2
- AND Q2 MUST remain queued while no further slot is free

#### Scenario: CAP-08 — Drain resumes after web restart

- GIVEN queued jobs persisted on disk and the web process restarted
- WHEN the supervisor starts sweeping after boot
- THEN the persisted queued jobs MUST be drained as slots become free
- AND no queued job MUST be lost or re-admitted by the restart

### Requirement: Re-Read Before Spawn

The drain MUST re-read the job record at spawn time and MUST NOT spawn a job that is no longer
queued (for example because it was cancelled while queued). The residual race — spawn winning
against a concurrent cancellation — is contained by `job-cancellation`: the worker observes the
cancellation before the first chunk and does zero work.

#### Scenario: CAP-09 — A queued job cancelled before its sweep is not spawned

- GIVEN a queued job whose owner cancelled it after it was queued
- WHEN the drain sweep reaches that job
- THEN the sweep MUST NOT spawn a worker for it
- AND the job MUST remain in its cancellation outcome, never a worker-bound state

#### Scenario: CAP-10 — Spawn-versus-cancel race does zero work when spawn wins

- GIVEN the race in which a spawn decision wins against a concurrent cancellation of the same
  queued job
- WHEN the spawned worker starts
- THEN the worker MUST observe the cancellation before the first chunk
- AND zero chunks MUST be transcribed for the job

### Requirement: Single-Writer Rule Preserved By The Gate

The capacity gate spawns and derives; it MUST NOT write the job record after a worker has started.
The transition from queued to a worker-bound state MUST be the worker's write. The gate's writes
are limited to recording the queued state before any spawn.

#### Scenario: CAP-11 — No gate write after worker start

- GIVEN the gate has spawned a worker for a queued job
- WHEN the job record changes state afterwards
- THEN the write MUST originate from the worker (or from the liveness-justified reconcile), never
  from the capacity gate

### Requirement: Queued Excluded From Reconcile

A queued job has no worker pid and needs no reconciliation. Startup reconcile MUST NOT treat queued
records as interrupted (see `worker-liveness-hardening` for the reconcile scope).

#### Scenario: CAP-12 — Reconcile leaves queued records queued

- GIVEN a data directory containing a queued job at web startup
- WHEN startup reconcile runs
- THEN the queued job MUST remain queued
- AND no interrupted state MUST be written for it

### Requirement: Drain Before Rollback

A queued record is NOT readable by the pre-change build: its state value is unknown to the old
decode, which fails closed and fails startup listing. Reverting to the pre-change build therefore
MUST NOT occur while any job remains queued. Before rollback, the queue MUST be drained (queued
jobs spawned and run to terminal states) or the queued job directories MUST be moved out of the
data directory (they remain resumable by re-upload after re-applying the change). Job records MUST
NOT be hand-edited in place to achieve rollback.

#### Scenario: CAP-13 — Rollback with a drained queue boots cleanly

- GIVEN no job remains in the queued state (the queue drained to terminal states or queued job
  directories moved out of the data directory)
- WHEN the pre-change build starts against the same data directory
- THEN startup MUST succeed and every remaining record MUST decode

#### Scenario: CAP-14 — Rollback with a queued record fails closed

- GIVEN a job record still in the queued state in the data directory
- WHEN the pre-change build starts against that data directory
- THEN that record MUST fail to decode and startup MUST fail
- AND this scenario is the reason the drain-or-move step above is mandatory before any rollback
