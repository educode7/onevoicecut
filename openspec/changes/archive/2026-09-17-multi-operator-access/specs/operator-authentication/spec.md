# Operator Authentication Specification

## Purpose

OneVoiceCut's documented premise was "single operator, runs locally"; the settled deployment is
several operators against ONE shared server. With zero authentication, every route is a broken
object / broken function authorization surface (OWASP API1/API5): anyone who can reach the port can
admit jobs, upload multi-hour media into any job, and spawn unbounded worker processes on the
shared machine. This capability gives the HTTP surface an identity layer: a static per-operator
token map read at the composition root, verified on every request, with a fail-closed and
deny-by-default posture. Authentication here only establishes *who is calling*; what a caller may
read is defined by `job-visibility`, and what a caller may mutate by `job-ownership`.

## Requirements

### Requirement: Static Per-Operator Token Map

The system MUST authenticate operators against a static map of operator-token pairs loaded from
configuration at startup. Each configured operator MUST have exactly one token. The configuration
MUST be read only at the composition root, mirroring the engine-secret discipline. Token comparison
MUST be performed in constant time. The concrete map format and rotation ergonomics are a design
dependency (proposal U4); this requirement states the observable contract that any format MUST
satisfy.

#### Scenario: AUTH-01 — Valid token resolves its operator

- GIVEN a server configured with an operator-token map that contains operator "a"
- WHEN a request arrives bearing operator "a"'s token
- THEN the system MUST resolve the caller identity to operator "a"
- AND the request MUST proceed to normal route handling

### Requirement: Deny By Default On Every Route

Every route MUST require a valid operator token before any work is done. A route with no explicit
authentication handling is closed, not open. A request without a valid token MUST be rejected with
HTTP 401 and MUST NOT admit a job, write any file, spawn any process, or otherwise mutate state.

#### Scenario: AUTH-02 — Missing token is rejected on every route

- GIVEN a server configured with at least one operator token
- WHEN an unauthenticated request is made to any route — parametrized over every registered route,
  currently: POST /api/jobs, PUT /api/jobs/{id}/media, GET /api/jobs/{id}, GET /api/jobs, and
  POST /api/jobs/{id}/cancel
- THEN the system MUST respond 401
- AND no job MUST be admitted, no upload MUST be written, no cancellation MUST be recorded, and no
  worker MUST be spawned

#### Scenario: AUTH-03 — Unknown or wrong token is rejected

- GIVEN a server configured with an operator-token map
- WHEN a request bears a token that matches no configured operator
- THEN the system MUST respond 401
- AND the request MUST have no effect on jobs, files, or processes

#### Scenario: AUTH-04 — Malformed credentials are rejected

- GIVEN a server configured with an operator-token map
- WHEN a request presents credentials that cannot be resolved to a token (for example a missing or
  unparsable authorization header)
- THEN the system MUST reject the request with 401 and MUST NOT mutate anything
- AND the exact differentiation between malformed and absent credential forms in the response body
  is design dependency U6; the normative outcome is rejection with no state change

#### Scenario: AUTH-05 — Authentication failures do not enumerate operators

- GIVEN a server configured with an operator-token map
- WHEN one request bears a token matching no operator and another request bears a structurally valid
  but incorrect token for an existing operator
- THEN both responses MUST be indistinguishable in status and shape
- AND no response MUST reveal which operator names are configured

#### Scenario: AUTH-06 — Every registered route is proven authenticated by the test suite

- GIVEN the test suite contains a per-endpoint authentication check generated from the registered
  route table (not from a hand-maintained list)
- WHEN a route is registered without authentication handling
- THEN that check MUST fail the default test run
- AND this MUST hold equally for routes added in the future (deny-by-default enforced by a test,
  not by a document)

### Requirement: Fail-Closed Boot

The composition root MUST refuse to start when authentication cannot be established. A server MUST
NOT run with authentication disabled, degraded, or silently empty.

#### Scenario: AUTH-07 — Zero configured operators refuses boot

- GIVEN configuration with zero operators in the token map (empty or absent map)
- WHEN the application is built
- THEN the composition root MUST refuse to start
- AND no request-serving process MUST come up

#### Scenario: AUTH-08 — Malformed token map refuses boot

- GIVEN configuration whose operator-token map cannot be parsed into operator-token pairs (the
  exact malformed forms depend on the format chosen by design dependency U4)
- WHEN the application is built
- THEN the composition root MUST refuse to start
- AND the failure MUST occur before any route can serve requests

### Requirement: Token Values Never Leave The Composition Root

Token values MUST never be persisted in job records, written to logs, or passed in worker command
lines. Only operator identities (names) are persisted — identical to the engine-secret precedent.
The worker receives nothing identity-related: it loads a record that already carries the owner.

#### Scenario: AUTH-09 — Admission persists the operator name, never the token

- GIVEN an authenticated admission by operator "a" whose token is "t-a"
- WHEN the job record is persisted
- THEN the record MUST contain owner "a"
- AND the record MUST NOT contain "t-a" or any token value
- AND no log line produced by the request MUST contain "t-a"
- AND the spawned worker command line, when any is spawned, MUST NOT contain "t-a" nor any operator
  identity (it carries only the job identifier and data directory, unchanged by this change)
