# Tasks: Slice 12 — Subject Tracking Domain + Trajectory Arithmetic

> Phase: `sdd-tasks` (rev 4 — clip rendering) · Artifact store: hybrid (openspec + Engram
> `sdd/video-transcription-pipeline/slice-12/tasks`)
> Design reference: `design.md` "Decision: three new ports, and what each is forbidden to know",
> "Decision: trajectory planning is a six-stage pure pipeline, and the order is load-bearing",
> "Decision: the trajectory is built at detection rate; densification belongs to the adapter", "Rev-4
> Slice Ordering" (units 12a, 12b). Spec reference: `specs/subject-tracking/spec.md` (13 requirements).
> **Depends on slice 11a** (`FrameSize`/`MediaProbe.frame`) for `crop_size_for(frame, policy)`; has no
> dependency on slice 11b. Densification (4 Hz → 25 Hz command rate) is deliberately **excluded** from
> this slice — it lives in the ffmpeg `sendcmd` writer (slice 13a-iii), never inside `CropTrajectory`,
> because resampling the trajectory itself would mark ~84% of keyframes `INTERPOLATED` and flag every
> clip low-confidence on a purely cosmetic change.
> Full RED/GREEN task detail lives inline in `tasks.md` under "Slice 12a-i/ii" / "Slice 12b-i/ii"; this
> file carries the review-workload contract for those same task IDs.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,075 (12a-i ~450 · 12a-ii ~525 · 12b-i ~525 · 12b-ii ~575) |
| Test share expectation | 65–80%, consistent with every slice measured since 4a |
| 400-line budget risk | Low per-unit — every unit individually estimated 450–575 lines; High in aggregate across all four |
| Chained PRs recommended | Yes |
| Suggested split | 4 work units, PR 28 → PR 31 |
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
| 12a-i | `domain/framing.py` entities (`TimeSpan`, `CropRect`, `KeyframeOrigin`, `CropKeyframe`, `CropTrajectory` + `__post_init__` invariant, `TrackingConfidence`, `TrajectoryPolicy`) + `crop_size_for` | PR 28 | `pytest tests/unit/domain/test_framing.py -m "not paid and not localmodel"` | N/A — pure domain types | `domain/framing.py` (entities + `crop_size_for`) |
| 12a-ii | `SubjectTrackerPort`, `BoundingBox`, `SubjectDetection`, `DetectionSupport`/`TrackerCapabilities`, fake detector | PR 29 | `pytest tests/unit/ports/test_capabilities.py tests/unit/ports/test_subject_tracker.py -m "not paid and not localmodel"` | N/A — fake detector only | `ports/subject_tracker.py`, `ports/capabilities.py` (`DetectionSupport`), `tests/fakes/subject_tracker.py` |
| 12b-i | Trajectory pipeline stages 2–4: centres, smoothing, dead-zone | PR 30 | `pytest tests/unit/usecases/test_plan_trajectory.py -m "not paid and not localmodel"` | N/A — pure functions over the fake detector's output | `usecases/plan_trajectory.py` (centres/smooth/dead-zone stages) |
| 12b-ii | Trajectory pipeline stages 5–6 + confidence: clamp, interpolation, fallback, provenance, `LOW_CONFIDENCE` | PR 31 | `pytest tests/unit/usecases/test_plan_trajectory.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/plan_trajectory.py` (clamp/fill stages, confidence calculation) |

## Dependency Notes

- **12a-i and 12a-ii are independent of each other** — the domain entities do not need the port, and the
  port's `SubjectDetection`/`BoundingBox` do not need `CropTrajectory`. Both may proceed in parallel; both
  must land before 12b-i.
- **12b-i must land before 12b-ii** — stage 5 (clamp) and stage 6 (fill) are applied to the output of
  stages 2–4, and the confidence calculation in 12b-ii reads the origins stage 6 assigns.
- **12a-i depends on slice 11a's `FrameSize`** for `crop_size_for(frame, policy)`'s signature. No other
  unit in this slice depends on slice 11.
