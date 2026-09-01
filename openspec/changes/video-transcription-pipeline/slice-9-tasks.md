# Tasks: Slice 9 — Local + Cloud Diarization, SpeakerResolver Seam

> Phase: `sdd-tasks` (rev 5 — calibration re-baseline) · Artifact store: hybrid (openspec + Engram
> `sdd/video-transcription-pipeline/slice-9/tasks`)
> Design reference: `design.md` diarization capability decisions, cross-chunk speaker identity risk
> (`SpeakerResolver` seam). Spec reference: `specs/speech-transcription/spec.md` (Reject Speaker-Mode Jobs
> the Adapter Cannot Satisfy, Contract Parity and Declared Divergence — diarization scenario).
> **Rev 5 re-baseline**: the original tasks.md carried 9a and 9b as raw nominal single-unit slices
> (~230 / ~210 lines). At the ×4 calibration standard slices 11–13 already use, both exceed the 800-line
> per-unit budget (920 / 840 calibrated). Re-split here into **5 work units** (2 for 9a, 3 for 9b) — no
> existing task's number or text changed. Neither slice introduces new domain dataclasses
> (`TranscriptSegment.speaker` already exists from slice 1); the rejection path itself was already proven
> in slice 6 — these two slices only flip declared support from `UNSUPPORTED`/`REQUIRES_SETUP` to
> `AVAILABLE` and wire the real diarization call.
> Full RED/GREEN task detail lives inline in `tasks.md` under "Slice 9a-i/ii" / "Slice 9b-i/ii/iii"; this
> file carries the review-workload contract for those same task IDs.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~1,820 (9a-i ~460 · 9a-ii ~460 · 9b-i ~350 · 9b-ii ~300 · 9b-iii ~250) |
| Test share expectation | 65–80%, per the measured pattern on every slice since 4a (68–81%, never the original 56%) |
| 400-line budget risk | Low per-unit — every unit individually estimated 250–460 lines, none at the ceiling; High in aggregate across all five |
| Chained PRs recommended | Yes |
| Suggested split | 5 work units, PR 28 → PR 32 |
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
| 9a-i | Local diarization capability probe | PR 28 | `pytest tests/unit -m "not paid and not localmodel"` (`localmodel`-marked) | `pytest -m localmodel` — real install-state probe | `adapters/asr/local/faster_whisper_adapter.py` (capability probe + sub-adapter) |
| 9a-ii | Diarizing call + namespaced speaker labels | PR 29 | `pytest tests/unit -m "not paid and not localmodel"` (`localmodel`-marked) | `pytest -m localmodel` real diarization | `adapters/asr/local/` diarization branch |
| 9b-i | Cloud diarization declared divergence | PR 30 | `pytest tests/unit -m "not paid and not localmodel"` (`paid`-marked) | `pytest -m paid` real cloud diarization | `adapters/asr/cloud/` diarization branch |
| 9b-ii | `SpeakerResolver` seam | PR 31 | `pytest tests/unit/usecases/test_stitch_transcript_resolver.py -m "not paid and not localmodel"` | N/A — no-op resolver, fakes only | `usecases/stitch_transcript.py` resolver seam |
| 9b-iii | Admission coverage + capability-probing refactor | PR 32 | `pytest tests/unit/usecases/test_admit_job.py -m "not paid and not localmodel"` | N/A — regression coverage + refactor | `usecases/admit_job.py`, adapter capability-probing helper |

## Dependency Notes

- **9a-i gates 9a-ii** — the diarizing call needs the capability probe/sub-adapter to exist first.
- **9b-i is independent of 9a** — the cloud provider's declared divergence does not depend on the local
  adapter's diarization support. **9b-ii is independent of 9b-i** — the `SpeakerResolver` seam is a
  stitcher-level addition unrelated to either adapter's capability declaration.
- **9b-iii depends on both 9a (local) and 9b-i (cloud)** — it extends slice 6's admission tests to cover
  engines that now declare `AVAILABLE`, and consolidates the capability-probing pattern both real adapters
  established.
