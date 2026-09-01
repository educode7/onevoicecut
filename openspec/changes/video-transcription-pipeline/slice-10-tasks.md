# Tasks: Slice 10 — Map-Reduce Summarization + Clip Candidates + Script Variants

> Phase: `sdd-tasks` (rev 5 — calibration re-baseline) · Artifact store: hybrid (openspec + Engram
> `sdd/video-transcription-pipeline/slice-10/tasks`)
> Design reference: `design.md` MAP/REDUCE summarization decisions, clip candidate ranking, script-variant
> generation. Spec reference: `specs/script-generation/spec.md` (Map-Reduce Summarization, Clip Candidate
> Output, N Script Variants Per Clip Candidate, Scope Boundary — No Rendering).
> **Rev 5 re-baseline**: the original tasks.md carried 10a and 10b as raw nominal single-unit slices
> (~320 / ~240 lines). At the ×4 calibration standard slices 11–13 already use, both exceed the 800-line
> per-unit budget (1,280 / 960 calibrated). Re-split here into **8 work units** (4 for 10a, 4 for 10b) — no
> existing task's number or text changed. Neither slice introduces new domain dataclasses —
> `GenerationResult`, `ClipCandidate`, `ScriptVariant`, and `TextGenerationPort` all already exist from
> slice 1 — so this is pure use-case logic over an already-built port, the closest comparable in the change
> to the well-measured pure-logic slices (2a/2b).
> Full RED/GREEN task detail lives inline in `tasks.md` under "Slice 10a-i/ii/iii/iv" / "Slice
> 10b-i/ii/iii/iv"; this file carries the review-workload contract for those same task IDs.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,550 (10a-i ~400 · 10a-ii ~300 · 10a-iii ~400 · 10a-iv ~300 · 10b-i ~300 · 10b-ii ~250 · 10b-iii ~350 · 10b-iv ~250) |
| Test share expectation | 65–80%, per the measured pattern on every slice since 4a (68–81%, never the original 56%) |
| 400-line budget risk | Low per-unit — every unit individually estimated 250–400 lines, none at the ceiling; High in aggregate across all eight |
| Chained PRs recommended | Yes |
| Suggested split | 8 work units, PR 33 → PR 40 |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Low
```

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|-----------------------|------------------|--------------------|
| 10a-i | Fake `TextGenerationPort` + MAP windowing | PR 33 | `pytest tests/fakes tests/unit/usecases/test_generate_artifacts_map.py -m "not paid and not localmodel"` | N/A — fakes only | `tests/fakes/text_generation.py`, `usecases/generate_artifacts.py` MAP phase |
| 10a-ii | Speech-only windowing | PR 34 | `pytest tests/unit/usecases/test_generate_artifacts_map.py -m "not paid and not localmodel"` | N/A — pure functions over fakes | `usecases/generate_artifacts.py` (speech-only filter) |
| 10a-iii | Segment-id validation + REDUCE fold | PR 35 | `pytest tests/unit/usecases/test_generate_artifacts_map.py -m "not paid and not localmodel"` | `pytest -m paid` real LLM call (REDUCE fold) | `usecases/generate_artifacts.py` REDUCE phase |
| 10a-iv | Context-length retry + token-estimation refactor | PR 36 | `pytest tests/unit/usecases/test_generate_artifacts_map.py -m "not paid and not localmodel"` | `pytest -m paid` real context-length-exceeded retry | `usecases/generate_artifacts.py` (retry + token-estimation helper) |
| 10b-i | Clip candidate ranking | PR 37 | `pytest tests/unit/usecases/test_generate_artifacts_variants.py -m "not paid and not localmodel"` | `pytest -m paid` real LLM call | `usecases/generate_artifacts.py` candidate-ranking phase |
| 10b-ii | Musical-range eligibility | PR 38 | `pytest tests/unit/usecases/test_generate_artifacts_variants.py -m "not paid and not localmodel"` | N/A — pure resolution logic over fakes | `usecases/generate_artifacts.py` (`kind`-agnostic candidate resolution) |
| 10b-iii | N script variants | PR 39 | `pytest tests/unit/usecases/test_generate_artifacts_variants.py -m "not paid and not localmodel"` | `pytest -m paid` real LLM call per `(candidate, target)` | `usecases/generate_artifacts.py` variant phase, `runtime/settings.py` (`script_targets`) |
| 10b-iv | Scope-boundary assertion + prompt refactor | PR 40 | `pytest tests/unit -m "not paid and not localmodel"` | N/A — structural assertion + refactor | `usecases/generate_artifacts.py` (prompt-template helper) |

## Dependency Notes

- **10a-i gates 10a-ii, 10a-iii, and 10a-iv** — all three build on the fake port and MAP windowing. **10a-iv
  depends on 10a-iii** (it retries the REDUCE fold on `ContextLengthExceeded`).
- **10b-i through 10b-iv all depend on 10a's MAP/REDUCE infrastructure**, not on each other in sequence:
  **10b-ii depends on 10b-i** (musical-range eligibility extends candidate ranking); **10b-iii is
  independent of 10b-i/10b-ii** (script variants attach to whatever candidates exist); **10b-iv depends on
  10a and 10b-iii** — it asserts the combined `GenerationResult` shape and extracts the prompt-template
  helper shared by MAP/REDUCE/variant calls, so it must land last.
