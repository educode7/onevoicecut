# Project Bootstrap Specification

## Purpose

Establishes the Python dependency manager, test runner, and required system binary (ffmpeg) before
any application code exists, so Strict TDD is enforceable from the first implementing change.

## Requirements

### Requirement: Dependency Manager Selection

The system MUST use venv + pip + `requirements.txt` as the Python dependency manager, since no
`uv.lock`, `pyproject.toml`, `Pipfile`, or `environment.yml` exists at the time of this change.

#### Scenario: No existing dependency manifest

- GIVEN the repository has no dependency manifest
- WHEN the project is bootstrapped
- THEN a `.venv` MUST be created and dependencies MUST be tracked in `requirements.txt`

### Requirement: Test Runner Configuration

The system MUST configure `pytest` as the test runner and record it as `test_command` in
`openspec/config.yaml`. The system MUST register an `integration` marker for tests that call a real
ASR engine or LLM provider.

#### Scenario: Default suite excludes integration tests

- GIVEN tests exist marked `integration` and tests that are not
- WHEN the default test command runs (e.g. `pytest -m "not integration"`)
- THEN no test marked `integration` MUST execute
- AND the run MUST NOT invoke any paid API or real local ASR/LLM model

### Requirement: ffmpeg Declared as a System Dependency

ffmpeg MUST be documented as a required system binary, separate from the Python dependency manifest,
since it is not a pip package.

#### Scenario: README documents ffmpeg

- GIVEN a new operator sets up the project
- WHEN they follow the README
- THEN it MUST list ffmpeg installation as a distinct step from `pip install -r requirements.txt`
