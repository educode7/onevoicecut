# Tasks: Slice 13 — Rendering Pipeline (Types, Real ffmpeg Pass, Real Vision Tracker)

> Phase: `sdd-tasks` (rev 4 — clip rendering) · Artifact store: hybrid (openspec + Engram
> `sdd/video-transcription-pipeline/slice-13/tasks`)
> Design reference: `design.md` "Decision: one native pass via `sendcmd` from a generated command file",
> "Decision: the four declarations are computed above the port, not reported by the adapter", "Decision:
> a render is a separate short-lived worker, and render state lives per clip", "Decision:
> `TranscriptStoragePort` grows by two methods", "Rev-4 Slice Ordering" (units 13a, 13b, 13c). Spec
> reference: `specs/clip-rendering/spec.md` (10 requirements), `specs/subject-tracking/spec.md` (real-adapter
> requirements).
> This is the largest and riskiest of the three rendering slices — it carries both the "first-of-its-kind
> adapter" cost (a second native ffmpeg process type, per the 3a retro's revised ~700–800/adapter unit)
> and the "first end-to-end assembly" cost that made slice 5c the single worst-measured ratio in the
> change (5.1x). It is split into **ten** units rather than the design's nominal three (13a/13b/13c) for
> exactly that reason.
> Full RED/GREEN task detail lives inline in `tasks.md` under "Slice 13a-i/ii/iii", "Slice 13b-i…v", "Slice
> 13c-i/ii"; this file carries the review-workload contract for those same task IDs.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | **~7,250** (13a-i ~525 · 13a-ii ~525 · 13a-iii ~575 · **13a-iv ~450** · **13a-v ~475** · **13a-vi ~400** · **10c-i ~400** · 13b-i ~575 · 13b-ii ~475 · 13b-iii ~975 · 13b-iv ~675 · 13b-v ~425 · 13c-i ~475 · 13c-ii ~300) |
| Test share expectation | 65–80%, with the render-worker orchestration unit (13b-iii) and the HTTP wiring unit (13b-iv) likely trending toward the 5c-style upper bound — first end-to-end assembly is where adapter gaps surface |
| 800-line budget risk | **Low per-unit once 13b-iii is split** (see below) — every unit then sits at 300–675 with margin. High in aggregate across all fifteen |
| Chained PRs recommended | Yes |
| Suggested split | **15 work units, PR 49 → PR 63.** PR 49–51 (13a-i/ii/iii) have landed; the four rev-5 units take PR 52–55, 13b-iii splits into two, and everything from 13b-i shifts accordingly |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
800-line budget risk: Medium
```

### Rev 5 re-baseline (Open Question 3 answered)

Four units added, three revised. The addition is **~1,725 lines** of new units plus **~625** of growth
in the three unwritten 13b units that per-network delivery reaches into — call it **~2,350 over the
rev-4 forecast**, which is 48% growth on a slice that was already the largest in the change.

**Where the growth is not.** Geometry cost nothing: `TrajectoryPolicy.aspect_w/aspect_h` were already
parameters, so a second aspect is a value, not a code path. Detection cost nothing: `detect` takes no
aspect, so the vision pass is hoisted out of the profile loop by construction rather than by
optimisation. Both are the hexagon's boundary paying for itself — the two places rev 5 could have
become expensive were already parameterised by decisions made before the requirement existed.

**Where it is.** Orchestration. `13b-iii` absorbs the whole fan-out (ten added tasks) and is the only
unit that goes over budget.

**13b-iii is split here rather than at apply time.** Unsplit it estimates ~975, over budget. The seam
was already visible and this repo's stated rule applies — two halves that are each green alone are two
units — so the split is taken now and carried in the table above:

| Sub-unit | Tasks | Est. | PR |
| --- | --- | --- | --- |
| 13b-iii-a — single-profile orchestration | `13b.18`–`13b.26` | ~575 | 58 |
| 13b-iii-b — profile fan-out, shared detection, per-profile declarations | `13b.25a`–`13b.25j` | ~400 | 59 |

This is the fourth time in this change that splitting at the seam rather than at the line count landed
both halves inside budget (7a-ii at 504, 7c at 986, 13a at three units). The rule keeps holding.

**One estimate carries no comparable and should be read as a lower bound.** `13a-v` changes a shipped
module constant into a parameter and touches typography, which nothing in this change has measured
before. The `PlayRes` declaration in particular is the kind of thing that looks like one line and
turns out to be a coordinate-system question across every existing subtitle test.

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|-----------------------|------------------|--------------------|
| 13a-i | `VideoRenderPort`, `RenderRequest`/`RenderedFile`, `domain/rendering.py` (9 types), `ClipId`, `quality_of` arithmetic, structural no-external-asset test | PR 49 | `pytest tests/unit/domain/test_ids.py tests/unit/domain/test_rendering.py tests/unit/ports/test_video_render.py tests/unit/domain/test_framing.py -m "not paid and not localmodel"` | N/A — pure types + arithmetic | `domain/rendering.py`, `domain/ids.py` (`ClipId`), `ports/video_render.py`, `domain/framing.py` (`quality_of`) |
| 13a-ii | ASS subtitle escaping + cue building (`SegmentKind` eligibility, word-boundary splitting, `WORD_LEVEL`/`SEGMENT_LEVEL` and `CaptionCoverage` declarations) | PR 50 | `pytest tests/unit/adapters/ffmpeg/test_subtitles.py tests/unit/usecases/test_build_subtitle_cues.py -m "not paid and not localmodel"` | N/A — pure modules, no ffmpeg | `adapters/ffmpeg/subtitles.py`, `usecases/build_subtitle_cues.py` |
| 13a-iii | Filter-graph composition (`build_render_argv`) + `sendcmd` densification, ULID-filename containment | PR 51 | `pytest tests/unit/adapters/ffmpeg/test_argv_composition.py tests/unit/adapters/ffmpeg/test_sendcmd.py -m "not paid and not localmodel"` | N/A — pure argv/command-file composition, no ffmpeg spawned | `adapters/ffmpeg/argv.py` (`build_render_argv`), `adapters/ffmpeg/sendcmd.py` |
| **13a-iv** | **[rev 5]** `RenderProfile`/`SafeArea`, aspect derived from the output spec, `resolve_render_profiles` with its three refusals | PR 52 | `pytest tests/unit/domain/test_rendering.py tests/unit/usecases/test_render_profiles.py -m "not paid and not localmodel"` | N/A — pure types + resolution | `domain/rendering.py` (two types, `aspect_of`), `usecases/render_profiles.py` |
| **13a-v** | **[rev 5]** Profile-aware ASS geometry: `PlayRes` declaration, safe-area-derived margins; totality characterization test | PR 53 | `pytest tests/unit/adapters/ffmpeg/test_subtitles.py tests/unit/usecases/test_build_subtitle_cues.py -m "not paid and not localmodel"` | N/A — pure module, no ffmpeg | `adapters/ffmpeg/subtitles.py` (header becomes a function) |
| **13a-vi** | **[rev 5]** `ClipExport` re-keying: `profile` field, `variants` tuple, `export_key` | PR 54 | `pytest tests/unit/domain/test_rendering.py -m "not paid and not localmodel"` | N/A — pure types | `domain/rendering.py` (`ClipExport`) |
| **10c-i** | **[rev 5]** `ScriptTarget` names a profile; the four destinations become registry rows; composition-time cross-validation | PR 55 | `pytest tests/unit/usecases/test_generate_artifacts.py tests/unit/runtime/test_settings.py -m "not paid and not localmodel"` | N/A — pure config + resolution | `usecases/generate_artifacts.py` (`ScriptTarget`, `SCRIPT_TARGETS`), `runtime/settings.py` (cross-check) |
| 13b-i | Real `VideoRenderPort` adapter + `render_clip` pre-spawn guards (`ClipRangeInvalid`, timeout, `FfmpegUnavailable`) | PR 56 | `pytest tests/unit/adapters/ffmpeg/test_video_render.py tests/unit/usecases/test_render_clip.py -m "not paid and not localmodel"` | N/A — injected fake `RenderProcessRunner`, real ffmpeg proven in 13b-v | `adapters/ffmpeg/video_render.py`, `usecases/render_clip.py` guard clauses |
| 13b-ii | `ClipExport` storage keyed by **clip and profile** (`render/{clip_id}/{profile}.json`): port methods, filesystem adapter, fake | PR 57 | `pytest tests/unit/ports/test_transcript_storage.py tests/unit/adapters/storage/test_filesystem_transcript_storage.py -m "not paid and not localmodel"` | `pytest -m integration` — round-trip against real files on disk | `ports/transcript_storage.py` (two new methods), `adapters/storage/filesystem_transcript_storage.py`, `tests/fakes/transcript_storage.py` |
| 13b-iii-a | `render_worker` entrypoint: single-profile orchestration + the two pre-render refusal branches + low-confidence propagation | PR 58 | `pytest tests/unit/runtime/test_render_worker.py -m "not paid and not localmodel"` | `python -m onevoicecut.runtime.render_worker --job-id <fake-job> --clip-id <fake-clip>` against fakes | `runtime/render_worker.py` |
| **13b-iii-b** | **[rev 5]** Profile fan-out: shared detection, per-aspect trajectory, per-profile quality and duration-ceiling declarations | PR 59 | `pytest tests/unit/runtime/test_render_worker.py -m "not paid and not localmodel"` | Same entrypoint, a clip whose variants resolve to two profiles | `runtime/render_worker.py` (the profile loop) |
| 13b-iv | HTTP clip routes: `POST /api/jobs/{id}/clips`, `GET .../clips/{clip_id}`, `GET .../clips/{clip_id}/{profile}` | PR 60 | `pytest tests/unit/adapters/web/test_clip_routes.py -m "not paid and not localmodel"` | Real HTTP client against the composed app, fake render worker spawn | `adapters/web/routers/jobs.py` (clip routes), `adapters/web/schemas.py` (clip schemas) |
| 13b-v | Real ffmpeg render integration + graph-composition-under-hostile-path + real timeout | PR 61 | `pytest tests/unit -m "not paid and not localmodel"` (no new unit tests; verifies prior units) | `pytest -m integration` — real ffmpeg render of a tiny synthesized fixture, real burned-in text visible, **captions inside the profile's declared safe area** | `tests/integration/test_render_clip.py` (new), no production rollback — proves PR 49–60 against reality |
| 13c-i | Real vision-backed `SubjectTrackerPort` adapter: in-process span-bounded decode, capability probe | PR 62 | `pytest tests/unit -m "not paid and not localmodel"` (adapter is `localmodel`-marked) | `pytest -m localmodel` — real vision weights, real span-bounded decode | `adapters/vision/*_tracker_adapter.py` |
| 13c-ii | Real adapter contract test + never-synthesized-centre proof | PR 63 | `pytest tests/unit -m "not paid and not localmodel"` (contract body is `localmodel`-marked) | `pytest -m localmodel` — real adapter against the shared contract body | `tests/contract/test_subject_tracker_contract.py` |

## Dependency Notes

Every edge here is a compile-time dependency — a type, function or setting the later unit names and the
earlier one creates. The authoritative table is in `tasks.md` under "Ordering"; these notes explain the
non-obvious edges.

- **13a-i lands first within 13a.** Both 13a-ii and 13a-iii name types it creates: 13a-ii needs
  `SubtitleCue`, `SubtitleTimingSource` and `CaptionCoverage`; 13a-iii needs `ClipId` and `OutputSpec`.
  **13a-ii and 13a-iii are independent of each other** and may proceed in parallel once 13a-i is merged.
  All three must land before 13b-i.
- **13a-ii additionally depends on slice 11b-i** — cue building reads `TranscriptSegment.words`, which
  11b-i creates. This is the edge `design.md`'s Rev-4 Slice Ordering table records as 11b blocking "the
  subtitle half of 13a". 13a-i and 13a-iii carry no dependency on the 11b track.
- **13a-i and 13a-iii depend on 12a-i** — `quality_of` takes a `CropRect`, `RenderedClip` carries a
  `TrackingConfidence`, and `build_render_argv`/`sendcmd` consume a `CropTrajectory`.
- **13b-i depends on all three 13a units** — 13a-iii for the argv it spawns, 13a-ii for the `.ass` writer
  it invokes, 13a-i for `VideoRenderPort` itself.
- **13b-ii depends on 13a-i** — `save_clip_export`/`load_clip_exports` round-trip `ClipExport` and
  `ClipState`, both created in 13a-i. It is otherwise a self-contained storage-codec addition and is
  independent of 13a-ii, 13a-iii and 13b-i, so it may be worked in parallel with them.
- **13b-iii depends on 13a-i (types), 13a-ii (cue building), 13b-i (adapter), and 13b-ii (storage)** — it
  is the orchestration seam and necessarily lands last among the non-integration units.
- **13b-iii also depends on the whole slice-12 track, and it is the only unit that does.** It is the sole
  consumer of `build_trajectory`: `13b.18`'s happy path calls it (**12b-i**) and `13b.24`/`13b.25`
  propagate the `TrackingConfidence` that only exists once stage 6 assigns keyframe origins (**12b-ii**).
  It also names `SubjectTrackerPort.detect`, `DetectionSupport.AVAILABLE` and `TrackingUnavailable`
  (**12a-ii**), and `probe.frame`/`FrameGeometryUnavailable` for its refusal branch (**slice 11a**). Every
  other slice-13 unit consumes only the `CropTrajectory` *type*, which is 12a-i. A revision of the
  ordering table named no 12b unit as a prerequisite of anything, which left the trajectory pipeline
  looking like a dead end.
- **13b-iv depends on 13b-ii and 13b-iii** (it spawns the worker and reads exported state).
- **13b-v depends on 13b-i and 13b-iii** — it is the real-ffmpeg proof of the units before it, matching the
  shipped precedent (3a-iii, 3b-iii, 5c-v) of a dedicated final integration unit.
- **13c-i depends on 12a-ii (the port) and 13b-i** — `13c.3`'s pre-decode span guard refuses a span longer
  than `max_clip_seconds`, and that setting arrives with `render_clip`'s guards in 13b-i. It is independent
  of 13b-ii through 13b-v, so it may be scheduled alongside them if reviewer capacity allows; it is **not**
  independent of the entire 13b track, as an earlier revision claimed. **13c-ii depends on 13c-i**, and so
  inherits that same edge to 13b-i; it depends on nothing else in 13b.
