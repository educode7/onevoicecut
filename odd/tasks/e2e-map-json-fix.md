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
- [x] **T6 — verify + commit.** Full default suite and `mypy src tests` green;
  one Conventional Commit carrying tests and code together.
  *(Verification observed below; the commit was made by the orchestrator —
  `c309f82 fix(clips): survive a fenced MAP answer from the local LLM`, 9 files,
  +321/−18 — because the unit's task contract forbids the writer committing.)*
- [ ] **T7 — make the score contract reach the model.** Found by the follow-up
  e2e run: the fence fix worked, generation advanced into schema validation and
  then refused with `a moment's score 8.5 is outside the 0..1 range`. The
  domain bounds `Moment.score` to `0..1` because ranking is comparison, but
  `_MAP_INSTRUCTION` only says `"score": float` — **the range is never
  communicated**, so `8.5` is a reasonable reading of the instruction. The
  refusal is right; the prompt is wrong. Do not coerce or rescale an
  out-of-range score: silently normalising a 0..10 answer cannot tell 8.5 from
  87, and hiding that disagreement is the failure mode the validator exists to
  stop.
  - [x] **T7-RED.** Observed `3 failed, 12 passed in 0.19s` — two assertions
    that extracted bounds from the prompt and found none, and one that asked
    for `SCORE_RANGE_TEXT` and got `AttributeError: module ... has no
    attribute 'SCORE_RANGE_TEXT'`. Exactly the three properties the fix must
    establish, each failing for its own reason.
  - [x] **T7-GREEN.** `SCORE_MIN = 0.0`, `SCORE_MAX = 1.0`,
    `SCORE_RANGE_TEXT = f"{SCORE_MIN:g}..{SCORE_MAX:g}"` feed both
    `_MAP_INSTRUCTION` (one new Spanish sentence: *El score de cada momento es
    un decimal dentro de 0..1; no uses ninguna otra escala.*) and
    `_read_moment`'s refusal, whose message stays **byte-identical**.
    `RESPONSE_SHAPE` untouched; no coercion anywhere. The three tests extract
    the bounds from the rendered strings rather than comparing a constant to
    itself, so a prompt that never mentions the range cannot pass.
    *Evidence: `pytest .../test_generation_scope_boundary.py -q` → `15 passed`;
    `pytest tests/systems/pipeline/clips -q` → `734 passed in 45.44s`;
    `mypy src tests` → `Success: no issues found in 373 source files`; full
    default suite → `4 failed, 2252 passed, 44 deselected`, and a control run
    with these two files stashed gave `4 failed, 2249 passed` — **the same 4
    ids**, 2249 + 3 new tests = 2252, zero regressions. Parent spot check:
    focused file re-run → `15 passed in 0.17s`; diff read in full.*
    *(Committed separately by the orchestrator as
    `0550979 fix(clips): tell the model the score range it is ranked on`, 2
    files, +104/−4.)*

  **The 4 baseline failures span three files, not one**: `test_worker_entrypoint.py` (1),
  `test_cloud_engine_wiring.py::TestTheWorkerRefusal` (1), `test_worker_engine_wiring.py` (2).
  Count and cause match the `.env` `LOCAL_MODEL_SIZE` poisoning exactly.

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

- [x] T1…T6 complete, with observed command results.
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
    no issues found in 373 source files**. Commit `c309f82` made by the
    orchestrator after this verification.
- **RDD review: blocked by the environment, not by the change.** The native
  review (`review-15c4f8b37747bc1b`, medium tier, 1 lens) consented and started,
  but the `review-reliability` sub-agent could not launch: the model provider
  refuses sub-agent requests that carry a deny-heavy permission block with
  `OpenCode's free tier can only be used from within OpenCode` (deterministic,
  upstream OpenCode issue, reproduced 8/8 there; `general` — which has no deny
  block — succeeded here for the writer run). The reviewer's result cannot be
  synthesised, so authority was released with `review abandon`
  (`operator_disposition`, actor `gentle-orchestrator`, zero lens results
  discarded, zero findings) and the review is owed again once that gate is
  fixed. Delivery follows ordinary repository policy; this record exists so the
  missing receipt is visible rather than silent.
