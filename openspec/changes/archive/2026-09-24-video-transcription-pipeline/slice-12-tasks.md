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
| 800-line budget risk | Low per-unit — every unit individually estimated 450–575 lines; High in aggregate across all four |
| Chained PRs recommended | Yes |
| Suggested split | 4 work units, PR 45 → PR 48 (shifted from PR 28–31 by the rev-5 re-baseline of slices 7a–10b) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
800-line budget risk: Low
```

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|-----------------------|------------------|--------------------|
| 12a-i | `domain/framing.py` entities (`TimeSpan`, `CropRect`, `KeyframeOrigin`, `CropKeyframe`, `CropTrajectory` + `__post_init__` invariant, `TrackingConfidence`, `TrajectoryPolicy`) + `crop_size_for` | PR 45 | `pytest tests/unit/domain/test_framing.py -m "not paid and not localmodel"` | N/A — pure domain types | `domain/framing.py` (entities + `crop_size_for`) |
| 12a-ii | `SubjectTrackerPort`, `BoundingBox`, `SubjectDetection`, `DetectionSupport`/`TrackerCapabilities`, fake detector | PR 46 | `pytest tests/unit/ports/test_capabilities.py tests/unit/ports/test_subject_tracker.py -m "not paid and not localmodel"` | N/A — fake detector only | `ports/subject_tracker.py`, `ports/capabilities.py` (`DetectionSupport`), `tests/fakes/subject_tracker.py` |
| 12b-i | Trajectory pipeline stages 2–4: centres, smoothing, dead-zone | PR 47 | `pytest tests/unit/usecases/test_plan_trajectory.py -m "not paid and not localmodel"` | N/A — pure functions over the fake detector's output | `usecases/plan_trajectory.py` (centres/smooth/dead-zone stages) |
| 12b-ii | Trajectory pipeline stages 5–6 + confidence: clamp, interpolation, fallback, provenance, `LOW_CONFIDENCE` | PR 48 | `pytest tests/unit/usecases/test_plan_trajectory.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/plan_trajectory.py` (clamp/fill stages, confidence calculation) |

## Dependency Notes

- **12a-ii depends on 12a-i** — `SubjectTrackerPort.detect(media, span, sample_hz)` takes a `TimeSpan`, and
  `TimeSpan` is created in 12a-i. The port's `SubjectDetection`/`BoundingBox` genuinely do not need
  `CropTrajectory`, but that is not enough to make the two units independent: one type in one signature is
  a compile-time edge. 12a-i must land first; both must land before 12b-i.
- **12b-i must land before 12b-ii** — stage 5 (clamp) and stage 6 (fill) are applied to the output of
  stages 2–4, and the confidence calculation in 12b-ii reads the origins stage 6 assigns. 12b-ii's clamp
  also relies on `crop_size_for`'s postcondition (`crop_w <= frame.width`), pinned in 12a-i by `12a.8`.
- **12a-i depends on slice 11a's `FrameSize`** for `crop_size_for(frame, policy)`'s signature. No other
  unit in this slice depends on slice 11.
- **Both 12b units gate slice 13b-iii**, and only 13b-iii — it is the sole consumer of
  `build_trajectory`'s output (`13b.18` calls it; `13b.24`/`13b.25` propagate the `TrackingConfidence`
  stage 6's origins produce). Every other slice-13 unit names only the `CropTrajectory` *type* from 12a-i,
  never the pipeline that builds one. 12a-ii additionally gates 13b-iii (the `detect` call and the
  tracking-unavailable refusal) and 13c-i (the real adapter).
