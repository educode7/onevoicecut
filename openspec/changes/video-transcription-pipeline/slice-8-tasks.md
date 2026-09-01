# Tasks: Slice 8 — Cloud ASR Adapter + ChunkTooLarge Recovery

> Phase: `sdd-tasks` (rev 5 — calibration re-baseline) · Artifact store: hybrid (openspec + Engram
> `sdd/video-transcription-pipeline/slice-8/tasks`)
> Design reference: `design.md` `TranscriptionPort` real cloud-adapter decisions, Cloud Adapter
> Request-Size Handling. Spec reference: `specs/speech-transcription/spec.md` (TranscriptionPort Contract,
> Contract Parity and Declared Divergence, Cloud Adapter Request-Size Handling, Non-Speech Segment
> Classification).
> **Rev 5 re-baseline**: the original tasks.md carried 8a and 8b as raw nominal single-unit slices
> (~260 / ~145 lines). At the ×4 calibration standard slices 11–13 already use, 8a exceeds the 800-line
> per-unit budget (1,040 calibrated); 8b stays close to budget (580) but is still split for margin. Re-split
> here into **6 work units** (4 for 8a, 2 for 8b) — no existing task's number or text changed. 8a is the
> first real HTTP-client ASR adapter (no slice 1 comparable — calibrated as a first-of-its-kind adapter
> unit, the same category that measured worst on slice 3a/4a/5a).
> Full RED/GREEN task detail lives inline in `tasks.md` under "Slice 8a-i/ii/iii/iv" / "Slice 8b-i/ii"; this
> file carries the review-workload contract for those same task IDs.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,100 (8a-i ~450 · 8a-ii ~150 · 8a-iii ~300 · 8a-iv ~300 · 8b-i ~450 · 8b-ii ~150) |
| Test share expectation | 65–80%, per the measured pattern on every slice since 4a (68–81%, never the original 56%) |
| 400-line budget risk | Low per-unit — every unit individually estimated 150–450 lines, none at the ceiling; High in aggregate across all six |
| Chained PRs recommended | Yes |
| Suggested split | 6 work units, PR 22 → PR 27 |
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
| 8a-i | Cloud ASR adapter construction + HTTP client | PR 22 | `pytest tests/unit -m "not paid and not localmodel"` (adapter is `paid`-marked) | `pytest -m paid` — real API key, real billed call | `adapters/asr/cloud/*_adapter.py` |
| 8a-ii | Resolver registration | PR 23 | `pytest tests/unit/runtime/test_engine_resolver.py -m "not paid and not localmodel"` | N/A — registration only, no I/O | `runtime/engine_resolver.py` (CLOUD branch) |
| 8a-iii | Real byte-cap validation | PR 24 | `pytest tests/unit/usecases/test_plan_chunks.py -m "not paid and not localmodel"` | `pytest -m paid` — real 25MB cap assertion | `usecases/plan_chunks.py` (byte-cap assertion) |
| 8a-iv | Classification declaration (cloud) | PR 25 | `pytest tests/unit -m "not paid and not localmodel"` (`paid`-marked) | `pytest -m paid` — real provider classification behavior | `adapters/asr/cloud/*_adapter.py` (classification declaration) |
| 8b-i | `ChunkTooLarge` split-and-retry | PR 26 | `pytest tests/unit/usecases/test_transcribe_job_split_retry.py -m "not paid and not localmodel"` | `pytest -m paid` oversized-chunk scenario | `usecases/{plan_chunks,transcribe_job}.py` split-retry branch |
| 8b-ii | In-call-timeout construction refactor | PR 27 | `pytest tests/unit -m "not paid and not localmodel"` | N/A — refactor only | `runtime/engine_resolver.py` (timeout construction) |

## Dependency Notes

- **8a-i gates 8a-ii, 8a-iii, and 8a-iv** — all three need the adapter class to exist. 8a-iii reuses the
  slice-2a planning formula and only needs the real capability value from 8a-i.
- **8b-i depends on slice 2a's `plan_chunks.py` and slice 4b's `transcribe_job.py`** (both already shipped)
  and on 8a-i's real adapter existing to raise `ChunkTooLarge` in practice. **8b-ii depends on 8b-i and on
  slice 7b-ii's resolver refactor landing first** — it unifies the same in-call-timeout construction across
  both engine branches.
