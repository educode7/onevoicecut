# Architecture Boundary Specification

## Purpose

This capability makes the FastAPI Clean Architecture layout enforceable rather than
conventional. The FCA layer import rules are specified as an AST-walking test
(`tests/test_architecture.py`, rewritten from the hexagonal `domain/usecases/ports` guard):
a planted violation fails the default test run, so the boundary is proven per slice rather
than reviewed per PR. Coverage lands incrementally — each migration slice extends the walker
to the module it migrates (accepted resolution of proposal OQ2) — and the legacy hexagonal
rules remain in force until their packages are empty, so no phase exists in which
present-but-draining code is structurally unenforced.

This spec constrains placement; it does not decide it. Proposal OQ3 (how
`TranscriptStoragePort` spans the three modules) is deferred to design; none of its candidate
options — split per-module interfaces, wiring-only reachability, or a justified `shared/`
exception — is precluded by the rules below.

## Requirements

### Requirement: Inward Layer Dependencies Only

Layer dependencies MUST point inward: `presentation` → `application` → `domain`, and
`infrastructure` → `application`/`domain`. Concretely: a module's `domain` imports only the
standard library and `shared/domain`; its `application` imports its own module's `domain`;
its `infrastructure` imports its own module's `domain` and `application` plus its own
third-party libraries; its `presentation` imports its own module's `application` plus
FastAPI and Pydantic. `presentation` MUST NOT import `infrastructure` directly (composition
roots excepted by Wiring Lives Only In Composition Roots), `application` MUST NOT import
`presentation`, and no layer may import outward from its position in the matrix.

#### Scenario: AB-01 — Presentation importing infrastructure fails the default run

- GIVEN a file under a module's `presentation/` layer
- WHEN it imports a module from any `infrastructure/` package
- THEN the architecture test MUST fail the default test run
- AND the violation report MUST name the file and the imported module

#### Scenario: AB-02 — Application importing presentation fails the default run

- GIVEN a file under a module's `application/` layer
- WHEN it imports from that module's `presentation/` package
- THEN the architecture test MUST fail the default test run

#### Scenario: AB-03 — Domain importing above itself fails the default run

- GIVEN a file under a module's `domain/` layer
- WHEN it imports from `shared/infrastructure`, `shared/presentation`, or any
  `presentation`/`application`/`infrastructure` package
- THEN the architecture test MUST fail the default test run

### Requirement: Domain Import Prohibitions

A module's `domain` MUST import only the standard library and `shared/domain`. `domain`
MUST NOT import FastAPI, Pydantic, SQLAlchemy, `onevoicecut.adapters`, `onevoicecut.runtime`,
or any `infrastructure` package. This preserves the load-bearing hexagonal rule — domain has
zero third-party imports — inside the FCA layout.

#### Scenario: AB-04 — Domain importing a web framework or Pydantic fails the default run

- GIVEN a file under a module's `domain/` layer
- WHEN it imports `fastapi` or `pydantic`
- THEN the architecture test MUST fail the default test run

#### Scenario: AB-05 — Domain importing adapters or runtime fails the default run

- GIVEN a file under a module's `domain/` layer
- WHEN it imports `onevoicecut.adapters...` or `onevoicecut.runtime...`
- THEN the architecture test MUST fail the default run
- AND this MUST hold equivalently for the legacy `domain/`, `usecases/`, and `ports/`
  packages while they still exist

### Requirement: Cross-Module Isolation

A module MUST NOT import another module's `domain` or `infrastructure`. Cross-module contact
MUST go through domain events, or through an explicit interface declared in the importing
module's own `domain/interfaces` with the implementation bound at a composition root.
(`typing.Protocol` remains the interface mechanism; nothing here chooses among proposal OQ3's
storage-interface options.)

#### Scenario: AB-06 — Transcripts application importing jobs infrastructure fails

- GIVEN a file under `systems/.../transcripts/application/`
- WHEN it imports from the `jobs` module's `infrastructure/` package
- THEN the architecture test MUST fail the default run

#### Scenario: AB-07 — Clips domain importing jobs domain fails

- GIVEN a file under `systems/.../clips/domain/`
- WHEN it imports from the `jobs` module's `domain/` package
- THEN the architecture test MUST fail the default run

### Requirement: Shared Kernel Is Domain-Agnostic

`shared/` MUST hold only domain-agnostic kernel code (errors, principal, settings, security,
domain-agnostic adapters) and MUST NOT import any `systems.*` module package. Module-specific
business rules MUST NOT be placed in `shared/`.

#### Scenario: AB-08 — Shared kernel importing a module fails the default run

- GIVEN a file under `shared/`
- WHEN it imports any `onevoicecut.systems.*` module package
- THEN the architecture test MUST fail the default run

### Requirement: Wiring Lives Only In Composition Roots

Adapter construction and dependency binding MUST occur only in composition roots: `main.py`,
each `{module}_module_api.py`, and the retained parallel composition roots under `runtime/`
(the worker, render worker, supervisor, and the resolver modules they own — proposal
Deviation 3; they import module wiring and are never imported by it). Module `presentation`,
`application`, and `domain` layers MUST NOT construct adapters, and MUST NOT import
`onevoicecut.runtime`.

#### Scenario: AB-09 — Presentation constructing an adapter fails the default run

- GIVEN a file under a module's `presentation/` layer
- WHEN it imports a concrete adapter class in order to construct or bind it
- THEN the architecture test MUST fail the default run

#### Scenario: AB-10 — Application importing runtime fails the default run

- GIVEN a file under a module's `application/` layer
- WHEN it imports `onevoicecut.runtime...`
- THEN the architecture test MUST fail the default run

### Requirement: Guard Coverage Is Incremental And Never Vacuous

The architecture test MUST enforce the rules above through static AST parsing: an import is a
violation the moment it is written in source text, whether or not the imported package is
importable. Coverage MUST grow with migration — from the slice that migrates a module into
`systems/`, the walker MUST apply that module's rules — and the legacy rules over `domain/`,
`usecases/`, and `ports/` MUST remain in force until those packages no longer exist. No slice
MAY reduce coverage of code that still exists; there MUST be no window in which present code
is structurally unenforced.

#### Scenario: AB-11 — Mid-migration tree is fully covered on both sides

- GIVEN the `jobs` module has migrated into `systems/` while `usecases/` and `ports/` still
  exist
- WHEN a violation is planted in the migrated `jobs` tree
- THEN the architecture test MUST fail the default run
- AND when a legacy-style violation is planted under `usecases/` or `ports/`
- THEN the architecture test MUST fail the default run as well

#### Scenario: AB-12 — The rewritten guard itself is proven RED before GREEN

- GIVEN the rewritten architecture test lands in a slice
- WHEN a rule-violating import is planted anywhere in the covered tree
- THEN the default test run MUST fail naming that file
- AND removing the planted import MUST return the default run to green
