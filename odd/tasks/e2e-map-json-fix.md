# Feature: make MAP generation survive a fenced model answer

## Objective

Unblock the local end-to-end run at the artifact-generation stage: a job that
transcribed successfully must reach `artifacts.json` instead of staying
`COMPLETED` with no artifacts.

## Problem

The e2e run (`%TEMP%\opencode\e2e`, job `01M3TP8S2QY49REXWRR6DBA8K9`) reached
`COMPLETED` with `transcript.txt` and then died at generation:

```
transcribe-worker: artifact generation failed; the job stays COMPLETED with its
transcript: the model did not answer with JSON: ... Expecting value: line 1
column 1 (char 0)
```

`qwen2.5:7b-instruct` answers the MAP prompt with its JSON wrapped in a
```` ```json ```` fence. `parse_map_response` runs `json.loads` on the raw string
and refuses it. Reproduced against the real server: the adapter's exact call
fails, the same prompt with Ollama's `"format": "json"` returns clean JSON with
keys `moments` + `summary`.

## Why both halves

The user chose **both** defences:

1. **Transport** — ask Ollama for structured output on the MAP call only. The
   server constrains the grammar, so a preamble or a stray fence cannot happen.
2. **Parser** — accept a fenced answer before the strict schema check. Any
   provider (today's Ollama, a future cloud adapter) may fence, and the schema
   validation that follows is what actually protects the ids.

`TextGenerationPort.complete()` serves three callers with different shapes:
MAP needs JSON, FOLD (summary prose) and SCRIPT (script prose) must **not** be
JSON-constrained. The mode therefore has to be per call, never per adapter.

## Scope

In:

- `TextGenerationPort.complete` gains a keyword-only `json_mode: bool = False`.
- `OllamaTextGenerator` maps it to `"format": "json"`; the key is **absent**
  when false, so the existing payload assertions stay exact.
- `_map_one` (the MAP call) passes `json_mode=True`.
- `parse_map_response` strips a surrounding markdown fence before `json.loads`
  and keeps every existing refusal.

Out:

- No change to FOLD, SCRIPT, windowing, ranking, prompts or response shape.
- No change to the HTTP surface, storage, worker wiring or render half.
- No new dependency.

## Constraints

- Strict TDD: every task opens RED before its implementation.
- `mypy` strict over `src` and `tests`.
- Default pytest run must stay free of paid APIs and real model weights.
- Module docstrings state *why*, not *what*; no narration comments.
- The architecture test (`tests/test_architecture.py`, AB-01…AB-12) must stay
  green — this touches `domain/interfaces`, `infrastructure` and
  `application` inside `clips` only.

## Tasks

- [x] **T1 — RED (transport).** In `tests/systems/pipeline/clips/infrastructure/llm/test_ollama_generator.py`:
  a payload test asserting `json_mode=True` sends `"format": "json"`, and one
  asserting the default payload carries **no** `format` key.
- [x] **T2 — RED (parser).** In the generation use-case tests: a fenced
  ```` ```json ```` answer parses; a bare ```` ``` ```` fence parses; a prose
  preamble outside a fence is still refused; a fence whose content is invalid
  JSON is still refused.
- [x] **T3 — RED (call site).** Assert `run_map` records `json_mode=True` on the
  fake and that the FOLD and SCRIPT calls do not request it.
- [x] **T4 — GREEN (transport).** Add `json_mode` to the Protocol, the Ollama
  adapter payload, `_map_one`, and both fakes
  (`tests/fakes/text_generation.py`, the inline fake in
  `test_generate_artifacts_retry.py`).
- [x] **T5 — GREEN (parser).** Fence tolerance in `parse_map_response`, with
  the module docstring saying why the fence is presentation and the schema is
  the contract.
- [ ] **T6 — verify + commit.** Full default suite and `mypy src tests` green;
  one Conventional Commit carrying tests and code together.
  *(Verification observed below; the commit is deliberately held — this unit's
  task contract forbids committing and leaves the tree for the orchestrator.)*

## Acceptance criteria

- `run_map` against the real Ollama server produces `artifacts.json` for the
  e2e job (observed in the follow-up e2e run, not in this unit).
- FOLD and SCRIPT payloads are byte-identical to today's.
- The default suite and `mypy` are green with zero skips attributable to this
  change.

## Checks

```powershell
.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"
.venv\Scripts\python.exe -m mypy src tests
```

## Route and forecast

- Route: **delegated direct** (writer trigger — 4+ non-trivial files across
  `domain/interfaces`, `infrastructure`, `application` and three test files).
- Authored-line forecast: ~150 (tests dominate), well inside the 400-line
  heuristic and the 800-line `review.budget_lines`.
- Delivery: single unit, single commit on the current branch.

## Progress

- [ ] T1…T6 complete, with observed command results.
  - [x] T1 RED observed: `TypeError: OllamaTextGenerator.complete() got an
    unexpected keyword argument 'json_mode'`.
  - [x] T2 RED observed: the two fenced-parse tests failed with the defect's
    own error, `Expecting value: line 1 column 1 (char 0)`; the two refusal
    tests passed from the start (pinned, not new behavior).
  - [x] T3 RED observed: `AttributeError: 'GenerationCall' object has no
    attribute 'json_mode'` on all three call-site tests.
  - [x] T4 GREEN: focused run of adapter + call-site tests — 36 passed.
  - [x] T5 GREEN: `pytest tests/systems/pipeline/clips -q` → **731 passed**.
  - [x] T6 verification observed: default suite → `4 failed, 2249 passed,
    44 deselected` — the 4 failures are worker engine-resolution tests that
    fail identically on the clean baseline (proven by stash run) because
    `.env` now sets `ONEVOICECUT_LOCAL_MODEL_SIZE="small"`, which the worker
    root re-applies after `monkeypatch.delenv`; `mypy src tests` → **Success:
    no issues found in 373 source files**. Commit held per task contract.
