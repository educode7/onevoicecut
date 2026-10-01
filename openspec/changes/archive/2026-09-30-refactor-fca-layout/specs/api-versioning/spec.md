# API Versioning Specification

## Purpose

The HTTP surface moves from the unversioned `/api/...` prefix to `/api/v1/...` atomically in
one slice. There is no browser UI and no known deployed external client (consumers are the
repo's own tests and the local operator's curl invocations), so the migration keeps no
unversioned alias — a documented, reversible deviation from the generic keep-legacy-version
policy. `fca_config.yaml` is the version registry: it records v1 as the only active version
and carries the procedure for any future version. The deny-by-default and owner-only auth
tests derive from the route table, so they follow the prefix migration automatically rather
than being hand-updated.

## Requirements

### Requirement: All Operations Served Under /api/v1

The system MUST serve every HTTP operation under the `/api/v1` prefix — currently all eight
operations across seven paths: `POST /api/v1/jobs`, `GET /api/v1/jobs`,
`GET /api/v1/jobs/{id}`, `PUT /api/v1/jobs/{id}/media`, `POST /api/v1/jobs/{id}/cancel`,
`POST /api/v1/jobs/{id}/clips`, `GET /api/v1/jobs/{id}/clips/{clip_id}`, and
`GET /api/v1/jobs/{id}/clips/{clip_id}/{profile}`. The migration from the unversioned prefix
MUST be atomic: route registration, test path literals, and documented examples change
together, and the default suite MUST be green with no unversioned path remaining in shipped
code, tests, or documentation. Status codes and auth semantics per operation MUST be
unchanged by the migration.

#### Scenario: AV-01 — Every operation answers under the version prefix

- GIVEN the running application
- WHEN each of the eight operations is requested under `/api/v1/...`
- THEN it MUST be handled exactly as before the migration (same status codes, same auth
  semantics, same effects)

#### Scenario: AV-02 — Route table contains only versioned paths

- GIVEN the application's registered routes
- WHEN they are enumerated
- THEN every route path MUST begin with `/api/v1/`
- AND no route MUST be registered under the bare `/api` prefix

### Requirement: No Unversioned Alias

The unversioned `/api` prefix MUST NOT be registered: no alias route, no redirect, no
compatibility layer. This is a deliberate deviation from the keep-vN policy — `/api` is
pre-versioning legacy, not a maintained version with a deprecation record, and zero known
consumers exist. The deviation is reversible: if a real external client is ever discovered,
an unversioned alias MAY be added as a small follow-up without a version bump.

#### Scenario: AV-03 — Unversioned requests are not served

- GIVEN the running application with the migrated surface
- WHEN a request is made to any former unversioned path (for example `POST /api/jobs`)
- THEN the system MUST respond 404
- AND no job MUST be admitted, no file written, and no process spawned by that request

### Requirement: Version Registry In fca_config.yaml

The project MUST carry an `fca_config.yaml` that records API version status; `v1` MUST be
the only active version it declares, with `api_prefix` `/api/v1`. The set of versions marked
active in the registry MUST equal the set of version prefixes the shipped route table serves.
Any future breaking change MUST follow the registry procedure: add `vN+1`, keep serving
`vN`, and mark `vN` deprecated in the registry before it is ever removed; a registry entry's
status MUST distinguish active from deprecated versions.

#### Scenario: AV-04 — Registry matches the shipped route table

- GIVEN `fca_config.yaml` and the application's registered routes
- WHEN the version prefixes in the route table are compared to the versions marked active
  in the registry
- THEN the two sets MUST be equal (currently exactly `{v1}`)
- AND no version other than v1 MUST be recorded as active

#### Scenario: AV-05 — Registry distinguishes deprecated from active versions

- GIVEN `fca_config.yaml` parsed by its schema
- WHEN a version entry's status is read
- THEN the schema MUST distinguish an active version from a deprecated one
- AND only active versions MUST be recorded as generated/served

### Requirement: Strict Request Schemas

Every JSON request body schema MUST declare `extra="forbid"`: an unknown JSON key MUST be
rejected with HTTP 422 before any handler runs, with no side effect on jobs, files, or
processes. (The raw-body media upload carries no JSON schema and is out of scope for this
requirement; its size bound lives in `media-ingest`.)

#### Scenario: AV-06 — Unknown key on admission is rejected

- GIVEN an authenticated `POST /api/v1/jobs` request whose body carries an unknown JSON key
- WHEN the request is validated
- THEN the system MUST respond 422
- AND no job MUST be created

#### Scenario: AV-07 — Unknown key on clip export is rejected

- GIVEN an authenticated `POST /api/v1/jobs/{id}/clips` request whose body carries an
  unknown JSON key
- WHEN the request is validated
- THEN the system MUST respond 422
- AND no clip export MUST be written

### Requirement: Auth Coverage Generated From The Route Table

Across the version migration, the deny-by-default 401 check and the owner-only 403 check
MUST remain generated from the registered route table (per `operator-authentication`:
Deny By Default On Every Route, AUTH-06). The migration MUST NOT replace them with
hand-maintained path lists; generated coverage is what makes future routes authenticated by
construction.

#### Scenario: AV-08 — Unauthenticated route registration still fails after migration

- GIVEN the v1 surface with route-table-generated auth checks
- WHEN a new route is registered without authentication handling
- THEN the default test run MUST fail
- AND the generated checks MUST derive the route's path from `app.routes`, not from a
  literal list
