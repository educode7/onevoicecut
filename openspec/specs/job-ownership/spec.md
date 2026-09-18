# Job Ownership Specification

## Purpose

Authentication without ownership is the BOLA window: an authenticated operator B could overwrite
operator A's uploaded media and consume A's machine slot. This capability binds each job to exactly
one operator, written once at admission, and makes every mutation conditional on that binding.
Per the hexagonal boundary, ownership *policy* lives in the use cases while *identity resolution*
lives in the web adapter. The confirmed visibility model (proposal decision 3, V2) makes foreign
job existence already public via the shared list (`job-visibility`), so a non-owner mutation is
denied with HTTP 403 — not hidden with 404.

## Requirements

### Requirement: Owner Written Once At Admission

The system MUST record the authenticated caller as the job's owner when the job is admitted.
Ownership MUST be immutable: no later operation — media upload, state transition, cancellation,
resume, reconcile, or capacity-gate action — MUST reassign it. Records without an owner MUST NOT
be backfilled at boot with a fictional owner (see `legacy-job-compatibility`).

#### Scenario: OWN-01 — Admission records the caller as owner

- GIVEN operator "a" authenticated per `operator-authentication`
- WHEN operator "a" admits a job
- THEN the persisted job record MUST carry owner "a"
- AND no job MUST exist whose recorded owner differs from the authenticated caller at admission

#### Scenario: OWN-02 — Owner is immutable across the lifecycle

- GIVEN a job admitted by operator "a"
- WHEN the job passes through media upload, worker-bound states, terminal states, restart reconcile,
  and any capacity-gate transition
- THEN the record's owner MUST remain "a" at every point

### Requirement: Owner-Only Mutation

Mutating paths — media upload, cancellation, and artifact purge — MUST verify that the
authenticated operator owns the target job before any effect, and MUST reject non-owner attempts
with a dedicated ownership error that maps to HTTP 403 under the confirmed visibility model.
A rejected attempt MUST leave the job exactly as found.

#### Scenario: OWN-03 — Owner uploads media successfully

- GIVEN a job admitted by operator "a" awaiting media
- WHEN operator "a" uploads media to it
- THEN the upload MUST succeed and the worker lifecycle MUST proceed as before this change

#### Scenario: OWN-04 — Non-owner upload is denied with nothing touched

- GIVEN a job admitted by operator "a" awaiting media
- WHEN operator "b" (authenticated, not the owner) attempts to upload media to it
- THEN the system MUST respond 403
- AND no partial upload file MUST exist, the previously stored media (if any) MUST be untouched,
  the job record MUST be unchanged, and no worker MUST be spawned

#### Scenario: OWN-05 — Every mutation class is denied to non-owners

- GIVEN a job admitted by operator "a"
- WHEN operator "b" attempts any mutating operation — parametrized over media upload, cancellation,
  and artifact purge
- THEN each attempt MUST be rejected with 403 (or the corresponding ownership error at the use-case
  level for route-less seams)
- AND the job's record, media, control files, and artifacts MUST be unchanged in every case

#### Scenario: OWN-06 — Purge seam carries ownership

- GIVEN the artifact-purge seam (no HTTP route exists for it yet)
- WHEN it is invoked by an operator that does not own the job
- THEN it MUST raise the ownership error and MUST NOT remove any artifact
- AND an invocation by the owner MUST proceed
- AND the seam's contract MUST require the operator identity now, so a future route needs no
  signature surgery

### Requirement: Identity Never Travels As A Request Parameter

The operator identity MUST be derived exclusively from the authenticated token. No request MUST
carry an operator identity in a body field, header, or query parameter, and any client-supplied
identity value MUST have no effect on the recorded owner or on any authorization decision. Listing
filters MUST be booleans resolved server-side (`job-visibility`).

#### Scenario: OWN-07 — Client-supplied operator identity has no effect

- GIVEN operator "b" authenticated per `operator-authentication`
- WHEN operator "b" admits a job with a request that includes a client-supplied operator identity
  naming operator "a"
- THEN the recorded owner MUST be "b" (the caller resolved from the token)
- AND no route MUST accept or apply an operator identity from any request parameter

### Requirement: Unguessable Identifiers Are Not Authorization

Job identifiers are server-minted and unguessable, but obscurity provides no access control and
MUST NOT be relied upon as such. Knowledge of another operator's job id — including ids legitimately
observed in the shared listing — MUST NOT grant any mutating access.

#### Scenario: OWN-08 — Known foreign id grants no mutation

- GIVEN operator "b" knows the exact job id of operator "a"'s job (for example from the shared
  listing, where foreign jobs are visible read-only)
- WHEN operator "b" requests a mutating operation on that id
- THEN the system MUST deny the mutation with 403
- AND the denial MUST result from the ownership check, not from identifier secrecy

### Requirement: Identifier Validation Precedes Filesystem Access

Every route naming a job MUST validate the identifier against the canonical identifier shape before
touching the filesystem. A malformed or unknown identifier MUST yield the unknown-identifier outcome
(404), indistinguishable from a nonexistent job, and MUST NOT enumerate which identifiers exist.

#### Scenario: OWN-09 — Malformed id on a mutating route

- GIVEN an authenticated operator
- WHEN the operator requests a mutating operation with a malformed job identifier
- THEN the system MUST respond 404 (unknown identifier) before any filesystem access
- AND nothing MUST be written, spawned, or otherwise mutated

### Requirement: Authorization Wraps The Upload Path Without Restructuring It

The ownership check MUST wrap the existing upload mechanics, not change them: raw-body streaming,
commit-by-rename from a sibling partial file, extensionless stored source, content type from probe
rather than extension, client filenames as metadata only (percent-encoded header, never a path
component), and path containment checked inside the job directory before any spawn. The surviving
invariants of the pipeline — single-writer rule, atomic chunk-result writes, list-form worker
command lines — MUST hold unchanged under this capability.

#### Scenario: OWN-10 — Owner upload preserves the upload mechanics

- GIVEN the owner uploads media through the authorized path
- WHEN the upload completes
- THEN the stored media MUST be committed by rename from a sibling partial file
- AND the client-supplied filename MUST appear only as metadata, never as a path component
- AND the stored source MUST remain extensionless with content type established by probe

#### Scenario: OWN-11 — Capability refusal ordering is preserved with owner

- GIVEN an authenticated admission requesting a speaker mode the selected engine's declared
  capability cannot satisfy
- WHEN the job is admitted
- THEN the system MUST refuse with the capability error before any storage touch, exactly as before
  this change
- AND no job record (owned or otherwise) MUST be created for that submission
