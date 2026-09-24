# Feature: LLM generation adapter (Ollama) + production wiring

## Objective

Close the last biting gap: nothing constructs a `TextGenerationPort`, so `save_artifacts` has no
production caller, `load_artifacts` returns `None` for every real job, and `POST /api/jobs/{id}/clips`
answers 409 `ArtifactsNotAvailable`. Build the Ollama-backed adapter, the missing orchestrator glue,
and the worker wiring so a completed transcription produces `artifacts.json`.

## Problem / Why

`generate_artifacts` use-case pieces (speech_windows, run_map, reduce_summaries,
rank_clip_candidates, write_script_variants) are built and unit-tested over the fake, but no
top-level orchestrator chains them and no production code constructs a generator. The user chose
**local via Ollama** (zero billed cost, no key, local-first philosophy — matches the project's
local-ASR-primary stance). Provider stays swappable: the port is provider-neutral, this is one
adapter.

## Authorized scope

User authorized: install Ollama + qwen2.5:7b-instruct (done, verified), implement the adapter,
glue, wiring, tests, docs. NOT authorized: new openspec change artifacts, provider changes,
render-profile measurement, UI work.

## Constraints (repo conventions)

- Python 3.12, mypy strict, every function annotated including tests.
- Strict TDD (source: openspec/config.yaml `strict_tdd: true`; runner:
  `.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"`). RED before GREEN.
- Default suite never talks to a live Ollama; real-server tests are `localmodel`-marked
  (local weights, not billed — `paid` does not apply).
- Errors are domain types: adapter translates every httpx/Ollama failure into `GenerationFailed`
  (or `ContextLengthExceeded` where honestly detectable); never leak provider exceptions.
- Hexagonal boundary: glue lives in `usecases/` (imports domain+ports only); httpx only in
  `adapters/llm/`; env reads only in `runtime/`.
- Secrets rule N/A (no key), but settings discipline applies: `ONEVOICECUT_`-prefixed config read
  in the composition roots; Ollama is a **system binary/service like ffmpeg**, never a pip dep.
- Module docstrings state WHY; work-unit commits, conventional messages, no AI attribution.
- Delivery: budget ~400 authored changed lines per review slice; strategy `ask-on-risk`;
  chain `stacked-to-main`.

## Verified environment facts (prove-before-write, executed once already)

- Ollama 0.34.3 at `%LOCALAPPDATA%\Programs\Ollama\ollama.exe` (installer added it to the user
  PATH for NEW processes; this agent session's PATH is stale — use the full path).
- Server live at `http://127.0.0.1:11434`; model `qwen2.5:7b-instruct` pulled.
- Proven shapes: `POST /api/generate` `{model, prompt, stream:false, options:{temperature,
  num_predict}}` → 200 JSON with `response` (str), `eval_count`, `done_reason`, timings;
  unknown model → 404 `{"error":"model 'x' not found"}`; `GET /api/tags` → 200
  `{"models":[{"name":"qwen2.5:7b-instruct", ...}]}`.
- Cold model load ~84 s; warm ~5 tok/s on this CPU. `localmodel` tests need generous timeouts
  (≥600 s) and small `num_predict`.
- Ollama TRUNCATES prompts beyond the model context (reports `prompt_eval_count`); it does not
  error — so `ContextLengthExceeded` is effectively unreachable from this adapter and `run_map`'s
  halve-and-retry stays harmless. Record this honestly in the adapter docstring.

## Tasks

- [x] T1 (delegated, RED→GREEN): `adapters/llm/ollama_generator.py` — `OllamaTextGenerator`
      implementing `TextGenerationPort` over httpx (sync, `stream:false`); `model_id()` returns
      the model name; `max_output_tokens`→`num_predict`, `temperature`→`options.temperature`;
      base URL injectable (default `http://127.0.0.1:11434`); timeout injectable; every transport
      /non-200/malformed-JSON failure → `GenerationFailed` naming model and cause; 404 unknown
      model → `GenerationFailed` whose message names the model and how to pull it. Pure tests via
      `httpx.MockTransport` in the default suite. Commit: `feat(llm): ...`.
- [x] T2 (delegated): preflight probe — `GET /api/tags` and check the configured model is pulled
      (exact name match; document normalization if any). Probe lives beside the adapter (or a
      declarations-style split if the writer judges it cleaner); never called from use cases.
      Pure tests with MockTransport. Commit with T1 or separately.
- [x] T3 (delegated): orchestrator glue in `usecases/generate_artifacts.py` —
      `run_generation(transcript, *, generate, targets, max_output_tokens_map,
      max_output_tokens_reduce, max_output_tokens_variants)` (names/shape at writer's discretion,
      use-case imports stay domain+ports only) chaining speech_windows → run_map →
      reduce_summaries → rank_clip_candidates → write_script_variants → returns `GenerationResult`.
      Edge cases: zero speech windows (no speech at all) — return an honest empty result or raise,
      per the existing zero-window behavior of `speech_windows` (document the decision);
      `max_candidates` default 8. Tested with `FakeTextGenerationPort`. Commit: `feat(generation): ...`.
- [x] T4 (delegated): worker wiring — `runtime/` reads `ONEVOICECUT_LLM_MODEL` (no default:
      unset registers NO generator, the `LOCAL_MODEL_SIZE` lesson) and `ONEVOICECUT_OLLAMA_HOST`
      (default `http://127.0.0.1:11434`), plus `ONEVOICECUT_SCRIPT_TARGETS` (default
      `DEFAULT_SCRIPT_TARGETS`, parsed by `resolve_script_targets`). In `run_job` AFTER
      `transcribe_job` returns a COMPLETED record: if a generator resolves AND its probe sees the
      model, load the transcript, run `run_generation`, `storage.save_artifacts`. Generation
      failure is logged and the job STAYS COMPLETED (transcript is the primary deliverable;
      `errors.py:190` already blesses COMPLETED-with-no-artifacts). No generator configured →
      silent skip, artifacts stay absent, clips keep answering the proven 409. Tests follow the
      worker-wiring precedent (`test_worker_engine_wiring.py` style, injected spies). Commit:
      `feat(runtime): ...`.
- [x] T5 (delegated): `localmodel` e2e test — real Ollama call through the adapter (tiny prompt,
      `num_predict` ≤ 32, assert non-empty string + `model_id`), skip honestly when the server or
      model is absent. NO `paid` marker anywhere (nothing is billed).
- [x] T6 (delegated, with T4/T5 commit): docs — CLAUDE.md (gap list loses "No LLM adapter";
      settings table gains the two/three new vars; Ollama documented as a system service beside
      ffmpeg; test counts refreshed), `.env.example` (commented assignments for the new vars),
      requirements files untouched (httpx already core; Ollama never a pip dep).
- [ ] T7 (orchestrator): native review cycle(s) per RDD, merge to main, push on user approval.

## Acceptance criteria

- Default suite green, 0 skips, count grew by the new tests; mypy strict clean.
- `localmodel` e2e passes against the live Ollama.
- A job whose worker has `ONEVOICECUT_LLM_MODEL` set and Ollama running ends COMPLETED **with**
  `artifacts.json`; without either, ends COMPLETED with no artifacts and the proven 409 — both
  paths proven by default-suite tests (injected spies), not by hope.
- No provider exception crosses the adapter boundary; no env read below `runtime/`.

## Progress log

- 2026-09-24: Ollama 0.34.3 installed via winget; qwen2.5:7b-instruct pulled; API shapes proven
  by one real call each (generate/404/tags). Branch `feat/llm-generation-adapter` created.
  Route: delegated direct (writer trigger: 5+ non-trivial files). TDD: strict (project config).
- Next: delegate T1–T6 to one writer.
- 2026-09-23 (writer): T1–T6 implemented, strict RED→GREEN per unit. Commits: `054abf3`
  (adapter + probe + 25 MockTransport tests), `f9bcf2c` (`run_generation` + 7 tests),
  `79f2ca9` (worker wiring + 14 spy tests + two spy-signature updates the spies' own comments
  predicted), `9a29bfe` (localmodel e2e). Docs commit follows with this file.
  Decisions the spec left open, and why:
  - **Response is stripped; whitespace-only is refused** as `GenerationFailed("empty response")`.
    A blank map answer already fails in `parse_map_response`; a blank *script body* would have
    shipped silently, so one honest refusal covers both.
  - **`ContextLengthExceeded` is never raised** and the adapter docstring says why: Ollama
    truncates silently, so any detection would be a guess that triggers `run_map` halve-and-retry
    loops that cannot help.
  - **Probe match is exact, no `:latest` normalization** — widening `qwen2.5` silently would
    answer a question the operator did not ask; the skip/log line names `ollama pull`.
  - **Output budgets**: `MAX_MAP_OUTPUT_TOKENS = 512` (JSON summary + a few moments ≈ 300
    tokens; 512 covers a rich answer at ~100 s per call on the measured ~5 tok/s CPU),
    `MAX_REDUCE_OUTPUT_TOKENS = 512` (a fold's output is the next fold's input; unbounded growth
    ends in the fold-budget refusal), `MAX_VARIANT_OUTPUT_TOKENS = 384` (a 45 s plain script is
    ~170 tokens; double headroom, paid over the 8×4 candidate-target grid).
  - **Zero speech → honest empty `GenerationResult`** (`summary=""`, no candidates, zero
    generator calls), never a crash: the floor `reduce_summaries` already documented, and saving
    it beats skipping — "generation ran, found no speech" is a fact an operator can read.
  - **Wiring seams**: `run_job` gained `generation` (data), `generator_factory` and `model_probe`
    (injected callables), the `extractor_factory` pattern; `main` reads `configured_generation()`
    only in the branch where it also reads `configured_resolver()`, so an injected engine implies
    an injected generator and the E2E harness leaks nothing.
  Verification (all foreground, this tree): default suite `2076 passed, 44 deselected, 0 skips`
  (2120 collected); `mypy src tests` clean over 256 files; localmodel e2e `2 passed in 10.70s`
  against the live server, and `2 skipped` against a dead port (honest-skip proven both ways).
