# Delta for Operator Authentication

## ADDED Requirements

### Requirement: Principal Resolution Through CurrentPrincipal

The HTTP layer MUST resolve the bearer token to a `CurrentPrincipal` in
`shared/presentation/security.py`, built over the unchanged static operator-token map
(constant-time comparison; the map loaded only through the project's `Settings`
configuration at the composition root, never by modules reading the environment directly).
The system MUST NOT introduce JWT issuing or verification: authentication remains the static
bearer-token map — a documented deviation from the FCA skill's JWT template. The resolved
principal MUST carry the operator identity only, never a token value, and MUST be passed
into use cases, where ownership and authorization decisions are made; presentation-layer
code MUST NOT make ownership decisions beyond translating application errors to HTTP
statuses. Authorization header values and request bodies MUST NOT be logged.

#### Scenario: AUTH-10 — Valid token resolves a principal that reaches the use case

- GIVEN a server configured with operator "a" and token "t-a"
- WHEN a request arrives bearing `Bearer t-a`
- THEN `CurrentPrincipal` MUST resolve to identity "a"
- AND the use case invoked by the route MUST receive identity "a" as its principal
- AND no token value MUST cross from the presentation layer into the application layer

#### Scenario: AUTH-11 — No JWT participates in authentication

- GIVEN the shipped authentication stack
- WHEN its components are inspected
- THEN no JWT issuing or verification component MUST participate
- AND ONLY the static operator-token map MUST be able to authenticate a request

#### Scenario: AUTH-12 — Credentials and bodies never appear in logs

- GIVEN any authenticated request being handled
- WHEN the request's logs are collected
- THEN no produced log line MUST contain the token value
- AND no produced log line MUST contain the request body

### Requirement: Authorization Precedence 401 → 404 → 403

On every route that names a job, checks MUST apply in this order: authentication (401),
identifier validation and record load (404), then ownership (403). An unauthenticated
request MUST receive 401 regardless of the identifier it carries, so a caller without
credentials never learns whether an id exists. An authenticated request with a malformed or
unknown identifier MUST receive 404 before any filesystem access. An authenticated non-owner
attempting a mutation on a known job MUST receive 403 — under the shared-visibility model
foreign job existence is already public, so a non-owner mutation is denied, not hidden (see
`job-ownership`).

#### Scenario: AUTH-13 — Unauthenticated request with a malformed id gets 401

- GIVEN a server configured with at least one operator token
- WHEN an unauthenticated request hits a job-naming route with a malformed identifier
- THEN the system MUST respond 401
- AND the response MUST NOT reveal whether any identifier exists
- AND nothing MUST be written or spawned

#### Scenario: AUTH-14 — Authenticated request with a malformed or unknown id gets 404

- GIVEN an authenticated operator
- WHEN the operator requests a job-naming route with a malformed or unknown identifier
- THEN the system MUST respond 404
- AND the 404 MUST be produced before any filesystem access
- AND nothing MUST be written or mutated

#### Scenario: AUTH-15 — Authenticated non-owner mutation gets 403

- GIVEN a job owned by operator "a" and an authenticated operator "b"
- WHEN operator "b" attempts a mutating operation on that job
- THEN the system MUST respond 403
- AND the job's record, media, control files, and artifacts MUST be unchanged
- AND no worker MUST be spawned

### Requirement: Token Map Held As SecretStr And Parsed Only At Composition

`ONEVOICECUT_OPERATOR_TOKENS` MUST be carried on `Settings` as a pydantic `SecretStr`, so the
repr and str of the settings object (and any structured log derived from them) redact the raw
value. The plaintext MUST be extracted (via `get_secret_value()`) only inside a composition
root — `main.py` or the retained parallel `runtime/` roots — at the moment the static token
map is built; it MUST NOT be stored on `Settings` as a plain string, written to `JobRecord`,
placed in process argv, or written to any log line. Presentation and application code MUST
receive only the resolved principal or the parsed token map by injection, never the
environment string. Parse failures MUST continue to name pair positions and operator names
only, never token values (unchanged `InvalidTokenMap` discipline).

#### Scenario: AUTH-16 — Settings never expose the raw token map

- GIVEN a configured `Settings` instance carrying at least one operator token
- WHEN its `repr` or `str` is produced
- THEN no token value from the map MUST appear in the output
- AND `operator_tokens` MUST be an instance of `SecretStr`

#### Scenario: AUTH-17 — Token map parsing happens only at a composition root

- GIVEN the shipped modules under `shared/`, `systems/`, and `runtime/`
- WHEN the source is inspected for calls to `get_secret_value` on the operator-token field
- THEN every such call MUST reside in a composition root (`main.py` or `runtime/`)
- AND no module `presentation`, `application`, or `domain` file MUST extract the plaintext

#### Scenario: AUTH-18 — Parse failures still never echo token values

- GIVEN a malformed operator token map (for example an empty pair or a duplicate token)
- WHEN a composition root attempts to parse it
- THEN the refusal MUST name the pair position or operator name
- AND the message MUST NOT contain any token value from the configured map

## MODIFIED Requirements

### Requirement: Deny By Default On Every Route

Every route MUST require a valid operator token before any work is done. A route with no
explicit authentication handling is closed, not open. A request without a valid token MUST
be rejected with HTTP 401 and MUST NOT admit a job, write any file, spawn any process, or
otherwise mutate state.
(Previously: route enumeration named five unversioned `/api/...` paths; the list now names
all eight registered operations under `/api/v1/...`, matching the versioned surface.)

#### Scenario: AUTH-02 — Missing token is rejected on every route

- GIVEN a server configured with at least one operator token
- WHEN an unauthenticated request is made to any route — parametrized over every registered
  route, currently: POST /api/v1/jobs, PUT /api/v1/jobs/{id}/media, GET /api/v1/jobs/{id},
  GET /api/v1/jobs, POST /api/v1/jobs/{id}/cancel, POST /api/v1/jobs/{id}/clips,
  GET /api/v1/jobs/{id}/clips/{clip_id}, and GET /api/v1/jobs/{id}/clips/{clip_id}/{profile}
- THEN the system MUST respond 401
- AND no job MUST be admitted, no upload MUST be written, no cancellation MUST be recorded,
  no clip export MUST be written, and no worker MUST be spawned

#### Scenario: AUTH-03 — Unknown or wrong token is rejected

- GIVEN a server configured with an operator-token map
- WHEN a request bears a token that matches no configured operator
- THEN the system MUST respond 401
- AND the request MUST have no effect on jobs, files, or processes

#### Scenario: AUTH-04 — Malformed credentials are rejected

- GIVEN a server configured with an operator-token map
- WHEN a request presents credentials that cannot be resolved to a token (for example a
  missing or unparsable authorization header)
- THEN the system MUST reject the request with 401 and MUST NOT mutate anything
- AND the exact differentiation between malformed and absent credential forms in the
  response body is design dependency U6; the normative outcome is rejection with no state
  change

#### Scenario: AUTH-05 — Authentication failures do not enumerate operators

- GIVEN a server configured with an operator-token map
- WHEN one request bears a token matching no operator and another request bears a
  structurally valid but incorrect token for an existing operator
- THEN both responses MUST be indistinguishable in status and shape
- AND no response MUST reveal which operator names are configured

#### Scenario: AUTH-06 — Every registered route is proven authenticated by the test suite

- GIVEN the test suite contains a per-endpoint authentication check generated from the
  registered route table (not from a hand-maintained list)
- WHEN a route is registered without authentication handling
- THEN that check MUST fail the default test run
- AND this MUST hold equally for routes added in the future (deny-by-default enforced by a
  test, not by a document)
