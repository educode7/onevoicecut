# Tasks: Slice 13 — Rendering Pipeline (Types, Real ffmpeg Pass, Real Vision Tracker)

> Phase: `sdd-tasks` (rev 4 — clip rendering) · Artifact store: hybrid (openspec + Engram
> `sdd/video-transcription-pipeline/slice-13/tasks`)
> Design reference: `design.md` "Decision: one native pass via `sendcmd` from a generated command file",
> "Decision: the three declarations are computed above the port, not reported by the adapter", "Decision:
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
| Estimated changed lines | ~4,900 (13a-i ~525 · 13a-ii ~525 · 13a-iii ~575 · 13b-i ~575 · 13b-ii ~400 · 13b-iii ~575 · 13b-iv ~525 · 13b-v ~425 · 13c-i ~475 · 13c-ii ~300) |
| Test share expectation | 65–80%, with the render-worker orchestration unit (13b-iii) and the HTTP wiring unit (13b-iv) likely trending toward the 5c-style upper bound — first end-to-end assembly is where adapter gaps surface |
| 400-line budget risk | Low per-unit — every unit individually estimated 300–575 lines, with margin; High in aggregate across all ten |
| Chained PRs recommended | Yes |
| Suggested split | 10 work units, PR 49 → PR 58 (shifted from PR 32–41 by the rev-5 re-baseline of slices 7a–10b) |
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
| 13a-i | `VideoRenderPort`, `RenderRequest`/`RenderedFile`, `domain/rendering.py` (8 types), `ClipId`, `quality_of` arithmetic, structural no-external-asset test | PR 49 | `pytest tests/unit/domain/test_ids.py tests/unit/domain/test_rendering.py tests/unit/ports/test_video_render.py tests/unit/domain/test_framing.py -m "not paid and not localmodel"` | N/A — pure types + arithmetic | `domain/rendering.py`, `domain/ids.py` (`ClipId`), `ports/video_render.py`, `domain/framing.py` (`quality_of`) |
| 13a-ii | ASS subtitle escaping + cue building (word-boundary splitting, `WORD_LEVEL`/`SEGMENT_LEVEL` declaration) | PR 50 | `pytest tests/unit/adapters/ffmpeg/test_subtitles.py tests/unit/usecases/test_build_subtitle_cues.py -m "not paid and not localmodel"` | N/A — pure modules, no ffmpeg | `adapters/ffmpeg/subtitles.py`, `usecases/build_subtitle_cues.py` |
| 13a-iii | Filter-graph composition (`build_render_argv`) + `sendcmd` densification, ULID-filename containment | PR 51 | `pytest tests/unit/adapters/ffmpeg/test_argv_composition.py tests/unit/adapters/ffmpeg/test_sendcmd.py -m "not paid and not localmodel"` | N/A — pure argv/command-file composition, no ffmpeg spawned | `adapters/ffmpeg/argv.py` (`build_render_argv`), `adapters/ffmpeg/sendcmd.py` |
| 13b-i | Real `VideoRenderPort` adapter + `render_clip` pre-spawn guards (`ClipRangeInvalid`, timeout, `FfmpegUnavailable`) | PR 52 | `pytest tests/unit/adapters/ffmpeg/test_video_render.py tests/unit/usecases/test_render_clip.py -m "not paid and not localmodel"` | N/A — injected fake `RenderProcessRunner`, real ffmpeg proven in 13b-v | `adapters/ffmpeg/video_render.py`, `usecases/render_clip.py` guard clauses |
| 13b-ii | `ClipExport` storage: `save_clip_export`/`load_clip_exports` on the port, filesystem adapter, fake | PR 53 | `pytest tests/unit/ports/test_transcript_storage.py tests/unit/adapters/storage/test_filesystem_transcript_storage.py -m "not paid and not localmodel"` | `pytest -m integration` — round-trip against real files on disk | `ports/transcript_storage.py` (two new methods), `adapters/storage/filesystem_transcript_storage.py`, `tests/fakes/transcript_storage.py` |
| 13b-iii | `render_worker` entrypoint: orchestration + the two pre-render refusal branches + low-confidence propagation | PR 54 | `pytest tests/unit/runtime/test_render_worker.py -m "not paid and not localmodel"` | `python -m onevoicecut.runtime.render_worker --job-id <fake-job> --clip-id <fake-clip>` against fakes | `runtime/render_worker.py` |
| 13b-iv | HTTP clip routes: `POST /api/jobs/{id}/clips`, `GET /api/jobs/{id}/clips/{clip_id}` | PR 55 | `pytest tests/unit/adapters/web/test_clip_routes.py -m "not paid and not localmodel"` | Real HTTP client against the composed app, fake render worker spawn | `adapters/web/routers/jobs.py` (clip routes), `adapters/web/schemas.py` (clip schemas) |
| 13b-v | Real ffmpeg render integration + graph-composition-under-hostile-path + real timeout | PR 56 | `pytest tests/unit -m "not paid and not localmodel"` (no new unit tests; verifies prior units) | `pytest -m integration` — real ffmpeg render of a tiny synthesized fixture, real burned-in text visible | `tests/integration/test_render_clip.py` (new), no production rollback — proves PR 49–55 against reality |
| 13c-i | Real vision-backed `SubjectTrackerPort` adapter: in-process span-bounded decode, capability probe | PR 57 | `pytest tests/unit -m "not paid and not localmodel"` (adapter is `localmodel`-marked) | `pytest -m localmodel` — real vision weights, real span-bounded decode | `adapters/vision/*_tracker_adapter.py` |
| 13c-ii | Real adapter contract test + never-synthesized-centre proof | PR 58 | `pytest tests/unit -m "not paid and not localmodel"` (contract body is `localmodel`-marked) | `pytest -m localmodel` — real adapter against the shared contract body | `tests/contract/test_subject_tracker_contract.py` |

## Dependency Notes

- **13a-i, 13a-ii, 13a-iii have no dependency on each other** and may proceed in parallel once slices 11a
  and 12a/12b are merged. All three must land before 13b-i.
- **13b-i depends on 13a-iii** (the argv it spawns). **13b-ii is independent** of every other 13b/13a unit
  — it is a storage-codec addition and may be worked in parallel with 13a/13b-i.
- **13b-iii depends on 13a-i (types), 13a-ii (cue building), 13b-i (adapter), and 13b-ii (storage)** — it
  is the orchestration seam and necessarily lands last among the non-integration units.
- **13b-iv depends on 13b-ii and 13b-iii** (it spawns the worker and reads exported state).
- **13b-v depends on 13b-i and 13b-iii** — it is the real-ffmpeg proof of the units before it, matching the
  shipped precedent (3a-iii, 3b-iii, 5c-v) of a dedicated final integration unit.
- **13c-i and 13c-ii are independent of every 13b unit** — the vision tracker's port was fixed in 12a-ii,
  so the real adapter can be built and proven without any rendering code existing yet. They may be
  scheduled in parallel with the entire 13b track if reviewer capacity allows.
