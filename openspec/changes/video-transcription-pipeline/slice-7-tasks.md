# Tasks: Slice 7 — Local ASR Adapter + Supervisory Watchdog

> Phase: `sdd-tasks` (rev 5 — calibration re-baseline) · Artifact store: hybrid (openspec + Engram
> `sdd/video-transcription-pipeline/slice-7/tasks`)
> Design reference: `design.md` `TranscriptionPort` real-adapter decisions, capability declaration, per-chunk
> timeout / watchdog decision. Spec reference: `specs/speech-transcription/spec.md` (TranscriptionPort
> Contract, Contract Parity and Declared Divergence, Non-Speech Segment Classification),
> `specs/transcription-jobs/spec.md` (Per-Chunk Timeout).
> **Rev 5 re-baseline**: the original tasks.md carried 7a and 7b as raw nominal single-unit slices
> (~260 / ~250 lines). At the ×4 calibration standard slices 11–13 already use, both exceed the 800-line
> per-unit budget (1,040 / 1,000 calibrated). Re-split here into **6 work units** (4 for 7a, 2 for 7b) —
> no existing task's number or text changed. One gap-closing task was added: **7.4d**, a GREEN task for the
> orphaned RED at 7.4c (the original list had no GREEN follow-up for the hallucination-containment test).
> 7a is the first real ASR adapter (no slice 1 comparable — calibrated as a first-of-its-kind adapter unit,
> the category that measured worst on slice 3a/4a/5a). 7b is process-supervision infrastructure
> (`multiprocessing`, mtime polling, kill), not an ASR concern — kept separate from 7a per the original
> document's own split rationale.
> Full RED/GREEN task detail lives inline in `tasks.md` under "Slice 7a-i/ii/iii/iv" / "Slice 7b-i/ii"; this
> file carries the review-workload contract for those same task IDs.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,150 (7a-i ~450 · 7a-ii ~250 · 7a-iii ~250 · 7a-iv ~250 · 7b-i ~625 · 7b-ii ~375) |
| Test share expectation | 65–80%, per the measured pattern on every slice since 4a (68–81%, never the original 56%) |
| 400-line budget risk | Low per-unit — every unit individually estimated 250–625 lines, none at the ceiling; High in aggregate across all six |
| Chained PRs recommended | Yes |
| Suggested split | 6 work units, PR 16 → PR 21 |
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
| 7a-i | Local ASR adapter construction + capabilities declaration | PR 16 | `pytest tests/unit -m "not paid and not localmodel"` (adapter is `localmodel`-marked) | `pytest -m localmodel` — real `faster-whisper`, real weights | `adapters/asr/local/faster_whisper_adapter.py` |
| 7a-ii | Shared contract module + resolver registration | PR 17 | `pytest tests/contract tests/unit/runtime/test_engine_resolver.py -m "not paid and not localmodel"` | `pytest -m localmodel` — contract body against the real adapter | `tests/contract/`, `runtime/engine_resolver.py` (LOCAL branch) |
| 7a-iii | Non-speech classification (VAD + decoder guards) | PR 18 | `pytest tests/unit -m "not paid and not localmodel"` (`localmodel`-marked) | `pytest -m localmodel` — real VAD/decoder-guard behavior | `adapters/asr/local/faster_whisper_adapter.py` (classification mapping) |
| 7a-iv | Hallucination containment on music-only fixtures | PR 19 | `pytest tests/unit -m "not paid and not localmodel"` (`localmodel`-marked) | `pytest -m localmodel` — real music-only fixture | `adapters/asr/local/faster_whisper_adapter.py` (hallucination guards) |
| 7b-i | Watchdog core (mtime-timeout kill) | PR 20 | `pytest tests/unit/runtime/test_supervisor.py -m "not paid and not localmodel"` | `pytest -m localmodel` real timeout-kill scenario | `runtime/supervisor.py` |
| 7b-ii | Shared adapter-construction/secret-read resolver refactor | PR 21 | `pytest tests/unit -m "not paid and not localmodel"` | N/A — refactor only, no new behavior | `runtime/engine_resolver.py` (shared construction helper) |

## Dependency Notes

- **7a-i gates 7a-ii, 7a-iii, and 7a-iv** — all three need the adapter class to exist. 7a-iii gates 7a-iv
  (the hallucination-containment fixture in 7a-iv exercises the decoder guards 7a-iii enables).
- **7b-i has no dependency on 7a** and may be worked in parallel; it is ordered after 7a in the PR sequence
  only because the original document placed it there.
- **7b-ii's own task text (7.7) names slice 8a's cloud adapter as the thing it shares construction logic
  with** — a forward reference the original document already carried. This unit may need to be deferred
  until slice 8a-i lands, the same way task 4.20 discovered mid-flight that it had nothing to extract; note
  this at apply time rather than resequencing the task list here.
