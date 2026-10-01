# Delta for Job Cancellation

## MODIFIED Requirements

### Requirement: Owner-Only Cancellation Route

The system MUST provide a cancellation route (POST /api/v1/jobs/{id}/cancel), dispatched to
the jobs module's cancellation command handler. Authentication MUST precede handling (401
without a valid token, per `operator-authentication`). The caller MUST own the job; a
non-owner attempt MUST be denied with 403 and MUST NOT cause any change.
(Previously: route path was POST /api/jobs/{id}/cancel, and cancellation was handled in
route-level logic rather than a command handler.)

#### Scenario: CXL-01 — Owner cancels a running job

- GIVEN a job owned by operator "a" with a live worker in a worker-bound state
- WHEN operator "a" requests cancellation
- THEN the cancellation MUST be recorded and the request MUST succeed
- AND the recording MUST take effect without waiting for the worker to reach a boundary

#### Scenario: CXL-02 — Non-owner cancellation is denied with nothing touched

- GIVEN a job owned by operator "a"
- WHEN operator "b" (authenticated, not the owner) requests cancellation
- THEN the system MUST respond 403
- AND the job's control file MUST NOT be created or modified, the job record MUST be
  unchanged, and the worker MUST be unaffected
