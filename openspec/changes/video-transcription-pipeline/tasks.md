# Tasks: Video Transcription Pipeline

> Phase: `sdd-tasks` (rev 5 — calibration re-baseline) · Artifact store: hybrid (mirror of Engram `sdd/video-transcription-pipeline/tasks`)
> Inputs: `proposal.md` rev 3, `design.md`, all seven `specs/*/spec.md`, `openspec/config.yaml`, and **slice 1's actual code on
> disk** (`src/onevoicecut/`, `tests/`) as the calibration source for this revision.
> **Rev 4 delta**: proposal Open Question 8 was answered after slice 1 shipped — source footage routinely contains a singer
> alongside the speaker, or music under and between spoken passages. Music is normal input, not a defect. This adds slice 1b
> (`SegmentKind`) ahead of chunk planning, plus classification/containment tasks in 2b, 7a, 8a, 10a and 10b. No
> previously-checked slice-1 task is modified.
> **Rev 5 delta (this session)**: slices 7a–10b carried raw nominal single-unit-per-slice estimates (7a ~260 … 10b ~240)
> while slices 11–13 already used the ×4-calibrated standard — a self-contradiction (a "Low, 145–350 lines" row sitting
> next to a "treat everything as ×4" row). Re-baselined onto the same standard slices 11–13 use: the eight single-unit
> slices become **25 work units** (PR 16 → PR 40), each ≤ 650 calibrated lines, using the same 250–275-line stable unit
> size measured across nine prior slices (3.2x–5.1x overrun, mean ≈ 4.0x, test share 61–81%). Slices 11–13 shift from
> PR 24–41 to **PR 41 → PR 58**; slices 1–6 (PR 1–15, DONE) are untouched. **Grand total: 58 work units, PR 1 → PR 58.**
> No existing task's number or text changed; one gap-closing task was added (7.4d, a missing GREEN for an orphaned RED).
> See the per-slice `slice-7-tasks.md` … `slice-10-tasks.md` files (index + forecast only, mirroring `slice-11-tasks.md`).
> **Deviation note**: this document exceeds the 530-word task-artifact budget, for the same reason rev 2 stated — ten-plus
> slices expressed as explicit RED-before-GREEN pairs, each naming its file and spec scenario, plus a per-unit line-budget
> split, cannot compress below several thousand words without turning tasks back into the vague prose the skill forbids.

## Why This Revision Exists

Slice 1 was estimated at ~380 lines and landed at **1,273** (`git diff --numstat main...HEAD`: 1,253 insertions + 20
deletions, 47 files) — a **3.35x overrun** of the 400-line review budget. It shipped under a one-time user-accepted
`size:exception`. The user then directed a proper re-estimation of slices 2–10b instead of firefighting per slice, on
the explicit condition that this is **not** a flat 3.35x multiplier — each slice must be judged on what it actually
requires, using slice 1's real code as calibration data.

### Measured composition of slice 1 (ground truth)

| Category | Lines | Share |
| --- | --- | --- |
| Tests | 714 | 56% |
| Production `src/` | 461 | 36% |
| Config / manifests | 98 | 8% |
| **Total** | **1,273** | |

### Calibration units derived from the actual code (not intuition)

Read from `src/onevoicecut/{domain,ports,usecases}` and `tests/{unit,fakes,test_architecture.py}` on disk:

| Unit | Measured cost | Evidence |
| --- | --- | --- |
| One frozen dataclass (construction test + separate `FrozenInstanceError` test) | ~20–30 lines total (prod + test) | `domain/chunking.py` (53 lines, 4 dataclasses + 1 enum) + `test_chunking.py` (102 lines, 8 tests) ≈ 31/entity |
| One `Protocol` port method (protocol line + fake implementation line) | ~7–9 lines combined | `ports/transcript_storage.py` (39 lines / 12 methods) + `tests/fakes/transcript_storage.py` (63 lines / 12 methods) ≈ 8.5/method |
| One use-case orchestrating 3–4 ports, single scenario | ~110–115 lines (prod + test) | `usecases/ingest_media.py` (63) + `test_ingest_media_walking_skeleton.py` (49) = 112 |
| One `ast`-based architecture walker (one-time, already paid) | 59 lines | `tests/test_architecture.py` |

**The decisive finding**: every domain dataclass the design specifies (`SourceMedia`, `AudioTrack`, `MediaProbe`,
`JobRecord`, `ChunkPlan`, `PlannedChunk`, `AudioChunk`, `ChunkResult`, `TranscriptSegment`, `Transcript`,
`ClipCandidate`, `ScriptVariant`, `GenerationResult`) **and all five ports already exist on disk**, built in slice 1.
Confirmed by reading `src/onevoicecut/domain/generation.py` and `src/onevoicecut/domain/transcript.py` directly —
`ClipCandidate`/`ScriptVariant`/`GenerationResult` (needed by slice 10a/10b) and `Transcript`/`TranscriptSegment`
(needed by slices 7–9) are already there. **This means slices 2–10b do not pay slice 1's dominant cost driver** (12
dataclass test pairs + 5 port/fake pairs ≈ 700+ of slice 1's 1,273 lines). A flat 3.35x multiplier would double-charge
every remaining slice for scaffolding that is already sunk cost.

**What remaining slices pay for instead** (per-slice, judged individually below):
1. **Real adapter I/O surface** that slice 1 never built — ffmpeg subprocess (3), a real filesystem
   `TranscriptStoragePort` (4), FastAPI streaming HTTP (5), `faster-whisper` (7), a cloud HTTP client (8), diarization
   sub-adapters (9). Slice 1 has no measured comparable for this category, so a **+10–15% uncertainty margin** is
   applied to adapter-heavy slices (3, 4, 5, 7, 8, 9) rather than treating them as precisely calibrated.
2. **Threat-matrix breadth** — slice 5 alone carries 4 of the design's 4 applicable adversarial rows, each needing
   multiple parametrized RED tests (hostile filename, oversized upload, non-media content, path traversal).
3. **RED/GREEN pair count** — slice 4 has 10 pairs orchestrating a stateful chunk loop (resume, retry, timeout,
   atomic write); that volume was always going to be large, and the original single-slice estimate (~400, "at the
   ceiling, no headroom") is exactly the failure pattern already observed in slice 1.
4. **One real gap found in rev 2's task list**: slice 4 never explicitly test-drove the filesystem
   `TranscriptStoragePort`'s remaining 8 methods (job/plan/transcript/artifact CRUD) beyond the atomic-write and
   single-writer tasks — it assumed them "used by this slice" without a RED task. Two new tasks (4.0a/4.0b) close
   that gap in this revision.

Pure-logic slices with **zero new dataclasses, zero new ports, zero new I/O** (2, 6, 10a, 10b) get no uncertainty
margin — they are directly comparable to slice 1's well-measured domain/use-case test ratios.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Slice 1 actual (DONE) | 1,273 lines (461 prod / 714 test / 98 config) vs ~380 estimated — 3.35x, accepted `size:exception` |
| Slices 1b–10b calibrated estimate | ~5,270 lines (was ~4,885 before Open Question 8 was answered) |
| Added by Open Question 8 (non-speech audio) | ~385 lines — slice 1b (~150), `kind` propagation through stitching (~20), local classification + hallucination containment (~70), cloud classification declaration (~50), speech-only MAP windowing (~60), musical-range clip eligibility (~35) |
| **New total estimate** | **~6,543 lines** (was ~3,110 in rev 2 — 2.10x, not 3.35x, because scaffolding is sunk) |
| Slice 1b actual (DONE) | 553 lines, delivered as **two** units after a mid-flight split: 1b-i 309 (PR 2) + 1b-ii 244 (PR 3). Estimated ~150 as one unit — 3.2x. Both units land under budget; the unsplit slice would have been 21% over |
| Slice 4a actual (DONE) | 1,642 lines across **six** units, all under budget. Estimated ~340 — 4.8x, the worst ratio so far. Test share 68% against the 56% assumed. Revised unit: an adapter that also owns its persistence format ≈ 1,600 lines |
| Slices 4b+4c+4d actual (DONE) | 2,425 lines across **nine** units, all under budget. Estimated ~680 — 3.6x. Test share 70%. **The 56% test share in the rev-3 calibration is now disproved by three consecutive measurements (68%, 70%, 70%); every remaining estimate built on it is low by roughly a third on the test axis alone** |
| Slice 5a actual (DONE) | 1,017 lines across **four** units, all under budget. Estimated ~260 — 3.9x. Test share 61%, the lowest since 4a: a route handler is mostly delegation and Pydantic does its own validating. **Running overrun across every measured slice is now 3.2x–4.8x with no slice under 3.2x** |
| Slice 5b actual (DONE) | 1,018 lines across **four** units, all under budget. Estimated ~270 — 3.8x. Test share **81%**, the highest of the change: a threat matrix is mostly cases, and the code answering them is short (190 lines of `src` for eleven closed threat rows) |
| Slice 5c actual (DONE) | 1,274 lines across **five** units, all under budget. Estimated ~250 — **5.1x, the worst ratio of the change**. The estimate priced a status route and a wiring file; what it did not price is that the first end-to-end assembly is where the gaps between seven slices of adapters become visible |
| **Estimate reliability after nine measured slices** | Range 3.2x–5.1x, **mean ≈ 4.0x, no slice under 3.2x**. This is no longer estimation noise — it is a fixed multiplier. Every remaining number in this document is now `estimate × 4`, applied consistently below — this closes the rev-4 self-contradiction, where slices 7a–10b had been left at raw nominal single-unit estimates (145–350 lines) while slices 11–13 already used this calibration |
| **Rev 5 re-baseline (this session)** | Slices 7a–10b split from 8 raw-nominal single-unit slices into **25 calibrated work units** (PR 16 → PR 40), matching the 250–275-line stable unit size measured across the nine slices above. Full per-unit detail in `slice-7-tasks.md` … `slice-10-tasks.md` and inline below. Slices 11–13 shift from PR 24–41 to PR 41 → PR 58 as a result — see the rev-4 appendix further down, updated to match |
| Per-unit 800-line budget risk | **Low** — every one of the 40 work units in this section (15 DONE + 25 new) is individually estimated at 127–650 lines, with margin, not at the ceiling |
| Aggregate 800-line budget risk | **High** by construction — this is why the change stays split into 40 work units through slice 10b (58 across the whole change, including slices 11–13) |
| Chained PRs recommended | Yes |
| Suggested split | **40 work units**, PR 1 (slice 1, done) → PR 40 (slice 10b-iv); the full change is 58 work units, PR 1 → PR 58 once slices 11–13 are included |
| Delivery strategy | auto-chain |
| Chain strategy | **stacked-to-main** (resolved this session — no longer pending) |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
800-line budget risk: Low
```

**Is this one change or should it split?** Fifty-eight stacked PRs across ten domains is large but each unit is
independently revertible, dependency order is linear (1b → 2 → 4 → {5,6} → {7,8} → 9 → 10a → 10b → {11,12,13}), and
every unit ends green on the default suite. Nothing here requires two teams working concurrently or two independent
release cadences — it is one coherent hexagonal build-out, not two products. **Recommendation: keep it one change**,
delivered as a long stacked-PR chain, not split into separate OpenSpec changes. If the user wants a narrower blast
radius per change instead, the natural split point is: **Change A** = bootstrap + core pipeline (slices 1–6, ingest
through diarization gate, no real ASR yet) and **Change B** = engines + generation + rendering (slices 7a–13, real ASR
adapters, summarization, and clip rendering). That split is offered, not assumed.

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|-----------------------|------------------|--------------------|
| 1 (DONE) | Bootstrap + walking skeleton | PR 1 | `pytest tests/unit tests/test_architecture.py -m "not paid and not localmodel"` | N/A — fake-only skeleton | `src/onevoicecut/{domain,ports,usecases/ingest_media.py}`, `tests/`, config files |
| 1b-i (DONE) | `SegmentKind` + `ClassificationSupport`: domain field and capability declaration | PR 2 | `pytest tests/unit/domain/test_transcript.py tests/unit/ports/test_capabilities.py -m "not paid and not localmodel"` | N/A — pure domain/port types | `domain/transcript.py`, `ports/capabilities.py` (+ minimal fake field) |
| 1b-ii (DONE) | Classification test doubles + speech-only message export | PR 3 | `pytest tests/unit/ports/test_transcription_classification.py tests/unit/usecases/test_ingest_media_walking_skeleton.py -m "not paid and not localmodel"` | N/A — fakes only | `tests/fakes/`, `usecases/ingest_media.py` export filter |
| 2a | Chunk planning: stride/overlap/byte-cap/tail-merge | PR 4 | `pytest tests/unit/usecases/test_plan_chunks.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/plan_chunks.py` |
| 2b | Overlap stitching: match/fallback/straddling-segment | PR 5 | `pytest tests/unit/usecases/test_stitch_transcript.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/stitch_transcript.py` |
| 3a | ffmpeg extraction: probe/extract + argv/path safety | PR 6 | `pytest tests/unit/adapters/ffmpeg/test_argv_composition.py tests/unit/adapters/ffmpeg/test_extract.py -m "not paid and not localmodel"` | `pytest -m integration` (real ffmpeg, skips if absent) | `adapters/ffmpeg/extractor.py` (probe/extract only) |
| 3b | ffmpeg slicing + PATH check + integration + README | PR 7 | `pytest tests/unit/adapters/ffmpeg/test_slice.py -m "not paid and not localmodel"` | `pytest -m integration` slicing fixture | `adapters/ffmpeg/extractor.py` (slice), `README.md` |
| 4a | Real filesystem `TranscriptStoragePort` adapter (CRUD + atomic write + single-writer) | PR 8 | `pytest tests/unit/adapters/storage/test_filesystem_transcript_storage.py -m "not paid and not localmodel"` | `pytest -m integration` — crash-simulated atomic write | `adapters/storage/filesystem_transcript_storage.py` |
| 4b | `transcribe_job` core loop: happy path/failure isolation/retry/timeout/propagation | PR 9 | `pytest tests/unit/usecases/test_transcribe_job.py -m "not paid and not localmodel"` | N/A — fakes only, no real subprocess yet | `usecases/transcribe_job.py` |
| 4c | Resume + derived progress | PR 10 | `pytest tests/unit/usecases/test_{resume_job,progress}.py -m "not paid and not localmodel"` | N/A — fakes only | `usecases/{resume_job,progress}.py` |
| 4d | Worker entrypoint + purge seam + chunk-loop refactor | PR 11 | `pytest tests/unit -m "not paid and not localmodel"` | `python -m onevoicecut.runtime.worker --job-id <fake-job>` against fakes | `runtime/worker.py`, `usecases/purge_job_artifacts.py` |
| 5a | Job creation + streaming upload (real `MediaSourcePort`) | PR 12 | `pytest tests/unit/adapters/web/test_admit_job_route.py tests/unit/adapters/web/test_upload_stream.py -m "not paid and not localmodel"` | Real HTTP client, constant-memory streaming assertion | `adapters/web/routers/jobs.py` (POST/PUT), `adapters/storage/media_source.py` |
| 5b | Upload security threat-matrix (size limit, hostile filename, non-media content, traversal) | PR 13 | `pytest tests/unit/adapters/web/test_upload_security.py -m "not paid and not localmodel"` | Real HTTP client with hostile fixtures | Guard clauses inside `adapters/web/routers/jobs.py` and `adapters/storage/media_source.py` — revertible without touching PR 10's happy path |
| 5c | Status route + app/lifespan wiring + E2E | PR 14 | `pytest tests/unit/adapters/web/test_status_route.py -m "not paid and not localmodel"` | Real HTTP client E2E: upload → poll → `.txt`, fake engines | `runtime/app.py`, `adapters/web/routers/jobs.py` (GET) |
| 6 | Speaker mode + engine selection + diarization rejection | PR 15 | `pytest tests/unit/usecases/test_admit_job.py -m "not paid and not localmodel"` | Same E2E harness as PR 12, extended with a rejection-path scenario | `usecases/admit_job.py` guard clause |
| 7a-i | Local ASR adapter construction + capabilities | PR 16 | `pytest tests/unit -m "not paid and not localmodel"` (adapter is `localmodel`-marked) | `pytest -m localmodel` — real `faster-whisper`, real weights | `adapters/asr/local/faster_whisper_adapter.py` |
| 7a-ii | Shared contract module + resolver registration | PR 17 | `pytest tests/contract tests/unit/runtime/test_engine_resolver.py -m "not paid and not localmodel"` | `pytest -m localmodel` — contract body against the real adapter | `tests/contract/`, `runtime/engine_resolver.py` (LOCAL branch) |
| 7a-iii | Non-speech classification (VAD + decoder guards) | PR 18 | `pytest tests/unit -m "not paid and not localmodel"` (classification test is `localmodel`-marked) | `pytest -m localmodel` — real VAD/decoder-guard behavior | `adapters/asr/local/faster_whisper_adapter.py` (classification mapping) |
| 7a-iv | Hallucination containment on music-only fixtures | PR 19 | `pytest tests/unit -m "not paid and not localmodel"` (`localmodel`-marked) | `pytest -m localmodel` — real music-only fixture | `adapters/asr/local/faster_whisper_adapter.py` (hallucination guards) |
| 7b-i | Watchdog core (mtime-timeout kill) | PR 20 | `pytest tests/unit/runtime/test_supervisor.py -m "not paid and not localmodel"` | `pytest -m localmodel` real timeout-kill scenario | `runtime/supervisor.py` |
| 7b-ii | Shared adapter-construction/secret-read resolver refactor | PR 21 | `pytest tests/unit -m "not paid and not localmodel"` | N/A — refactor only, no new behavior | `runtime/engine_resolver.py` (shared construction helper) |
| 8a-i | Cloud ASR adapter construction + HTTP client | PR 22 | `pytest tests/unit -m "not paid and not localmodel"` (adapter is `paid`-marked) | `pytest -m paid` — real API key, real billed call | `adapters/asr/cloud/*_adapter.py` |
| 8a-ii | Resolver registration | PR 23 | `pytest tests/unit/runtime/test_engine_resolver.py -m "not paid and not localmodel"` | N/A — registration only, no I/O | `runtime/engine_resolver.py` (CLOUD branch) |
| 8a-iii | Real byte-cap validation | PR 24 | `pytest tests/unit/usecases/test_plan_chunks.py -m "not paid and not localmodel"` | `pytest -m paid` — real 25MB cap assertion | `usecases/plan_chunks.py` (byte-cap assertion) |
| 8a-iv | Classification declaration (cloud) | PR 25 | `pytest tests/unit -m "not paid and not localmodel"` (`paid`-marked) | `pytest -m paid` — real provider classification behavior | `adapters/asr/cloud/*_adapter.py` (classification declaration) |
| 8b-i | `ChunkTooLarge` split-and-retry | PR 26 | `pytest tests/unit/usecases/test_transcribe_job_split_retry.py -m "not paid and not localmodel"` | `pytest -m paid` oversized-chunk scenario | `usecases/{plan_chunks,transcribe_job}.py` split-retry branch |
| 8b-ii | In-call-timeout construction refactor | PR 27 | `pytest tests/unit -m "not paid and not localmodel"` | N/A — refactor only | `runtime/engine_resolver.py` (timeout construction) |
| 9a-i | Local diarization capability probe | PR 28 | `pytest tests/unit -m "not paid and not localmodel"` (`localmodel`-marked) | `pytest -m localmodel` — real install-state probe | `adapters/asr/local/faster_whisper_adapter.py` (capability probe + sub-adapter) |
| 9a-ii | Diarizing call + namespaced speaker labels | PR 29 | `pytest tests/unit -m "not paid and not localmodel"` (`localmodel`-marked) | `pytest -m localmodel` real diarization | `adapters/asr/local/` diarization branch |
| 9b-i | Cloud diarization declared divergence | PR 30 | `pytest tests/unit -m "not paid and not localmodel"` (`paid`-marked) | `pytest -m paid` real cloud diarization | `adapters/asr/cloud/` diarization branch |
| 9b-ii | `SpeakerResolver` seam | PR 31 | `pytest tests/unit/usecases/test_stitch_transcript_resolver.py -m "not paid and not localmodel"` | N/A — no-op resolver, fakes only | `usecases/stitch_transcript.py` resolver seam |
| 9b-iii | Admission coverage + capability-probing refactor | PR 32 | `pytest tests/unit/usecases/test_admit_job.py -m "not paid and not localmodel"` | N/A — regression coverage + refactor | `usecases/admit_job.py`, adapter capability-probing helper |
| 10a-i | Fake `TextGenerationPort` + MAP windowing | PR 33 | `pytest tests/fakes tests/unit/usecases/test_generate_artifacts_map.py -m "not paid and not localmodel"` | N/A — fakes only | `tests/fakes/text_generation.py`, `usecases/generate_artifacts.py` MAP phase |
| 10a-ii | Speech-only windowing | PR 34 | `pytest tests/unit/usecases/test_generate_artifacts_map.py -m "not paid and not localmodel"` | N/A — pure functions over fakes | `usecases/generate_artifacts.py` (speech-only filter) |
| 10a-iii | Segment-id validation + REDUCE fold | PR 35 | `pytest tests/unit/usecases/test_generate_artifacts_map.py -m "not paid and not localmodel"` | `pytest -m paid` real LLM call (REDUCE fold) | `usecases/generate_artifacts.py` REDUCE phase |
| 10a-iv | Context-length retry + token-estimation refactor | PR 36 | `pytest tests/unit/usecases/test_generate_artifacts_map.py -m "not paid and not localmodel"` | `pytest -m paid` real context-length-exceeded retry | `usecases/generate_artifacts.py` (retry + token-estimation helper) |
| 10b-i | Clip candidate ranking | PR 37 | `pytest tests/unit/usecases/test_generate_artifacts_variants.py -m "not paid and not localmodel"` | `pytest -m paid` real LLM call | `usecases/generate_artifacts.py` candidate-ranking phase |
| 10b-ii | Musical-range eligibility | PR 38 | `pytest tests/unit/usecases/test_generate_artifacts_variants.py -m "not paid and not localmodel"` | N/A — pure resolution logic over fakes | `usecases/generate_artifacts.py` (`kind`-agnostic candidate resolution) |
| 10b-iii | N script variants | PR 39 | `pytest tests/unit/usecases/test_generate_artifacts_variants.py -m "not paid and not localmodel"` | `pytest -m paid` real LLM call per `(candidate, target)` | `usecases/generate_artifacts.py` variant phase, `runtime/settings.py` (`script_targets`) |
| 10b-iv | Scope-boundary assertion + prompt refactor | PR 40 | `pytest tests/unit -m "not paid and not localmodel"` | N/A — structural assertion + refactor | `usecases/generate_artifacts.py` (prompt-template helper) |

## Open-Question Tracking (unchanged — task IDs referenced below are unaffected by the re-split)

| Question | Where it lands | Blocking behavior |
|---|---|---|
| Q3 — script-variant target networks/formats | Task 10.16 | Ships with `settings.script_targets = ["generic"]`; answering Q3 edits config only |
| Q5 — storage location | Slice 4a filesystem `TranscriptStoragePort` adapter | Assumption baked in per design; a different answer changes the adapter + one setting |
| Q6 — retention/cleanup | Task 4.21 (now in slice 4d) | Ships an unused `PurgeJobArtifacts` seam |
| New — cross-chunk speaker identity | Task 9.8 (now in slice 9b) | Ships a no-op `SpeakerResolver` seam + namespaced `cNN/SNN` labels |
| New — concurrency (one job at a time) | Slice 4b/7b supervisor | Assumption, not blocking |
| Q8 — non-speech audio (music/singing) in source | **ANSWERED**; lands as slice 1b + tasks 2.12b, 7.4a–c, 8.5a–b, 10.4a–c, 10.13a–b | Not blocking — `SegmentKind` ships in 1b against fakes; real engine behavior is asserted per adapter in 7a/8a |
| Q9 — should musical ranges be *promoted* as clip candidates, not merely permitted? | Task 10.13b | Ships permitting them (`kind`-agnostic candidate resolution); answering Q9 changes prompt + ranking in slice 10b only, no type change |

---

## Slice 1: Bootstrap + Walking Skeleton — DONE

**Actual: 1,273 lines (461 prod / 714 test / 98 config) vs ~380 estimated — 3.35x overrun, shipped under an accepted
`size:exception`.** This is the calibration source for the rest of this document; see "Why This Revision Exists"
above. Tasks unchanged from rev 2, all checked off — do not modify.

Closes: `project-bootstrap` Dependency Manager Selection, Test Runner Configuration; `transcript-artifacts`
Plain-Text Export; `speech-transcription` TranscriptionPort Contract (fake path); `speech-transcription`
Capability Declaration (type-level).

- [x] 1.1 Create `.venv`; write `requirements.txt` (fastapi, uvicorn, pydantic, pydantic-settings, httpx,
      pinned `==`), `requirements-dev.txt` (pytest, pytest-asyncio, mypy), `requirements-local-asr.txt`,
      `requirements-diarization.txt` (both empty placeholders); install dev+core into `.venv`.
- [x] 1.2 Create `pytest.ini` with `--strict-markers`; register `integration`, `localmodel`, `paid` markers.
- [x] 1.3 Record the exact `test_command`/`build_command` values for `sdd-apply` to write into `openspec/config.yaml`.
- [x] 1.4 Add mypy config targeting `src` and `tests`.
- [x] 1.5 RED: `tests/unit/test_bootstrap.py::test_placeholder`.
- [x] 1.6 GREEN: fix the placeholder; confirm the suite is green and `mypy src tests` passes on empty `src/`.
- [x] 1.7 RED: `tests/unit/domain/test_ids.py` — `JobId`/`MediaId` ULID validation.
- [x] 1.8 GREEN: `domain/ids.py` — `NewType` ids + regex validator.
- [x] 1.9 RED: `tests/unit/domain/test_{media,jobs,chunking,transcript,generation}.py` — construct every dataclass,
      assert `FrozenInstanceError` on mutation.
- [x] 1.10 GREEN: `domain/{media,jobs,chunking,transcript,generation,errors}.py`.
- [x] 1.11 RED: `tests/unit/ports/test_capabilities.py`.
- [x] 1.12 GREEN: `ports/{media_source,audio_extractor,transcription,text_generation,transcript_storage,
      capabilities}.py` — five `Protocol`s.
- [x] 1.13 RED: `tests/unit/usecases/test_ingest_media_walking_skeleton.py`.
- [x] 1.14 GREEN: `tests/fakes/*` + `usecases/ingest_media.py`.
- [x] 1.15 RED: `tests/test_architecture.py` seeded with a throwaway forbidden import.
- [x] 1.16 GREEN: land the `ast` walker; remove the throwaway fixture.
- [x] 1.17 REFACTOR: extract shared fake-construction helpers into `tests/fakes/__init__.py`; suite green.

---

## Slice 1b: `SegmentKind` Domain Amendment — DONE (split into 1b-i + 1b-ii)

**Actual: 553 lines vs ~150 estimated — 3.2x overrun.** Suite 61 passed (was 42), `mypy src tests` clean over 39
files, architecture boundary test green.

**Split at delivery** rather than shipped over budget. The overrun was measured *before* committing, and the cut
fell on a boundary that already existed — domain/port types on one side, test doubles and use-case wiring on the
other — so it is not an artificial division to satisfy a number:

| Unit | Commit | Lines | Contents | Suite at that commit |
| --- | --- | --- | --- | --- |
| 1b-i | `feat(domain): classify transcript segments…` | 309 | `SegmentKind`, `ClassificationSupport`, the three pure selectors, minimal fake field | 55 passed, mypy clean |
| 1b-ii | `feat(transcript): keep music out of the message export` | 244 | Second test double, script-driven fake, protocol-typed `FakePorts`, export filter | 61 passed, mypy clean |

Both units end green on the default suite independently, which is what makes them separately revertible. 1b-i
carries a one-line capability field on the existing fake — without it that commit would not compile, since
`non_speech_classification` is required with no default.

### What the estimate missed (new calibration data)

| Category | Insertions | Share |
| --- | --- | --- |
| Tests | 390 | 85% |
| Production `src/` | 94 | 15% |

The test share is **85%, not slice 1's measured 56%**, and that gap is the entire overrun. Three costs the estimate
omitted, none of which the existing "~31 lines per dataclass" calibration unit covers:

1. **A capability axis with two declared states needs two test doubles, not one.** `ClassificationSupport` has an
   `UNSUPPORTED` side, and the no-silent-degradation invariant is only assertable against an adapter that actually
   declares it. That forced a second fake (`NonClassifyingFakeTranscriptionPort`, ~40 lines) plus a new test module
   (`tests/unit/ports/test_transcription_classification.py`, ~70 lines) that the estimate treated as free.
2. **Making an existing fake configurable is not free.** `FakeTranscriptionPort` grew a script parameter and a
   timing helper so tests could drive mixed speech/music content — ~45 lines of change to a file the estimate
   assumed only needed a one-field edit.
3. **A required field with no default is a breaking change to every existing construction site.** Three existing
   capability tests had to be rewritten, plus `FakePorts.transcription` re-typed to the protocol.

**Revised calibration unit for the remaining slices**: *one new capability axis ≈ 450–500 lines*, dominated by test
doubles, not by the enum or the dataclass field. Slices 7a, 8a and 9a each add real-engine behavior on this same
axis and should be re-checked against this number rather than against the dataclass unit.

### Tasks

Closes: `transcript-artifacts` Structured Transcript Domain Object (classification half), Message Export Contains
Speech Only; `speech-transcription` Non-Speech Segment Classification (contract/type level, fakes only — real
engine behavior lands 7a/8a), Capability Declaration (classification half).

**Why this slice exists, and why it is here.** Proposal Open Question 8 was answered after slice 1 shipped: source
footage routinely contains a singer alongside the speaker, or music under and between spoken passages. Music is
normal input. `TranscriptSegment` therefore needs a content classification, and `TranscriptionCapabilities` needs a
classification declaration.

**Ordering rationale**: `TranscriptSegment` crosses every layer. Landing this before slice 2b means the stitcher is
written `kind`-aware from the start rather than retrofitted; landing it before slices 3–9 means no adapter and no
storage format has to be revised. Right now `adapters/` and `runtime/` do not exist at all, so the blast radius is
two production files. After slice 9 it would be the stitcher, the filesystem storage adapter, both ASR adapters, the
map-reduce use case, and every test around them. This is the same argument the proposal used to pull chunking
forward to slices 2 and 4.

**Calibration**: 2 small enums + 1 dataclass field + 1 capability field + one export-filter branch. Using slice 1's
measured ~31 lines/entity and ~1.55:1 test:prod ratio, with no adapter-uncertainty margin (no I/O, no new port, no
real engine).

- [x] 1b.1 RED: `tests/unit/domain/test_transcript.py` — `SegmentKind` has exactly `{speech, music, uncertain}`;
      `TranscriptSegment.kind` **defaults to `UNCERTAIN`**; the dataclass stays frozen.
- [x] 1b.2 GREEN: `domain/transcript.py` — add `SegmentKind(StrEnum)` and `TranscriptSegment.kind: SegmentKind =
      SegmentKind.UNCERTAIN`.
- [x] 1b.3 RED: `tests/unit/ports/test_capabilities.py` — `ClassificationSupport` has exactly
      `{unsupported, available}`; `TranscriptionCapabilities.non_speech_classification` is required (no default,
      so no adapter can omit its declaration).
- [x] 1b.4 GREEN: `ports/capabilities.py` — add `ClassificationSupport(StrEnum)` + the capability field.
- [x] 1b.5 RED: non-classifying-fake test — a fake `TranscriptionPort` declaring
      `non_speech_classification=UNSUPPORTED` returns segments whose `kind` is `UNCERTAIN`, and **never**
      `SPEECH`. This is the no-silent-degradation invariant on the classification axis.
- [x] 1b.6 GREEN: `tests/fakes/transcription.py` — classification-aware fake; existing fake construction helpers
      in `tests/fakes/__init__.py` updated to carry `kind`.
- [x] 1b.7 RED: message-export test — `export_text` output excludes `MUSIC`-classified segment text while those
      segments remain present in the saved structured `Transcript`.
- [x] 1b.8 GREEN: `usecases/ingest_media.py` — filter the export join to `kind == SPEECH`; `save_transcript`
      keeps every segment unchanged.
- [x] 1b.9 RED: uncertain-not-presented-as-message test — `UNCERTAIN` segments are excluded from the export (or
      marked distinguishably), consistently rather than per-segment.
- [x] 1b.10 GREEN: implement the chosen consistent policy for `UNCERTAIN` in the export.
- [x] 1b.11 REFACTOR: extract the speech-only selection into one helper reusable by slice 10a's MAP windowing,
      so the "speech only" rule has a single definition; suite green + `mypy src tests` clean.

---

## Slice 2a: Chunk Planning — DONE

**Actual: 340 lines (101 prod / 239 test) vs ~250 estimated — 1.36x, under the 400-line budget.**
Suite 84 passed (was 61), `mypy src tests` clean over 41 files. Measured before committing, per the
discipline slice 1b's overrun forced.

Test share 70% — between slice 1's 56% and slice 1b's 85%. The pure-logic calibration held far better
than the capability-axis one did: no new test double was needed, which is exactly the cost that broke
the 1b estimate. Two guards beyond the task list (non-positive duration, unplannable bitrate) account
for roughly 35 of the 90 lines over estimate; both prevent a hang rather than a wrong answer.

Closes: `speech-transcription` Chunk Planning, Cloud Adapter Request-Size Handling (planning half only — real cap
value lands slice 8a). Pure functions, no I/O, no new dataclasses/ports — directly comparable to slice 1's
domain-test ratio (~1.8:1 test:prod), so no adapter-uncertainty margin applied.

- [x] 2.1 RED: `tests/unit/usecases/test_plan_chunks.py` — stride/overlap arithmetic against
      `target_chunk_seconds=600, overlap_s=5.0` fixture durations.
- [x] 2.2 GREEN: `usecases/plan_chunks.py` — `stride_s`/chunk-bounds formula from design.
- [x] 2.3 RED: byte-cap test — fake `TranscriptionCapabilities(max_chunk_bytes=25_000_000)` drives
      `cap_s` derivation and stride selection.
- [x] 2.4 GREEN: extend planner with `bytes_per_second`/`cap_s` bridging logic (0.9 headroom factor).
- [x] 2.5 RED: tail-merge test — trailing chunk `< min_chunk_seconds` (30s) merges into predecessor.
- [x] 2.6 GREEN: implement tail merge.

## Slice 2b: Overlap Stitching — DONE (split into 2b-i + 2b-ii)

**Actual: 606 lines (188 prod / 418 test) vs ~230 estimated — 2.6x.** Suite 107 passed (was 84),
`mypy src tests` clean over 43 files. Split before committing, per the discipline slice 1b forced.

| Unit | Commit | Lines | Contents |
| --- | --- | --- | --- |
| 2b-i | `feat(usecases): reconcile overlapping chunk results…` | 391 | tokenizer, matcher, fallback, clipping, globalization |
| 2b-ii | `feat(usecases): refuse to stitch an incomplete set…` | 215 | plan-integrity guard, preservation invariants, degenerate inputs |

**Rejected split**: cutting between the match path and the fallback would have forced 2b-i to ship a
throwaway fallback policy that 2b-ii then replaced. A reviewer reviewing code already known to be
discarded is worse than one over-budget review, so the cut moved to algorithm vs. invariants instead.

**TDD honesty note**: only cycles 1 and 3 were genuinely RED. The cycle-2 fallback tests passed on first
run because cycle 1's own cases (accent mismatch, sub-minimum match) already exercised the fallback, so
implementing them was unavoidable in cycle 1's GREEN. Those tests are characterization, not driving.
Cycle 3's plan-integrity tests were real RED — that gap was not covered.

**Two deviations from the design, both recorded in the commit messages**: a match starting mid-segment
cuts at that segment's *end* rather than its start (cutting at the start would discard the words in front
of the phrase, since text cannot be split at a token boundary); and the plan-integrity guard is new — the
design never said what happens when results are incomplete.

Closes: `speech-transcription` Overlap Stitching. Split from planning because the matcher/fallback/straddling
algorithm is independently testable and independently revertible from the planner — a bug in stitching should
never require reverting planning.

- [x] 2.7 RED: `tests/unit/usecases/test_stitch_transcript.py` — matched suffix/prefix overlap (≥4 tokens,
      accents preserved) cuts once, no duplication or loss.
- [x] 2.8 GREEN: `usecases/stitch_transcript.py` — tokenize/match/cut per design algorithm.
- [x] 2.9 RED: no-match fallback — overlap cuts at the snapped midpoint, never loses more than `overlap_s`.
- [x] 2.10 GREEN: implement fallback branch.
- [x] 2.11 RED: straddling-segment test — a segment crossing the cut truncates, drops if empty, never duplicated.
- [x] 2.12 GREEN: implement truncation/drop.
- [x] 2.12b RED: `kind`-preservation test — stitching carries each segment's `SegmentKind` through unchanged,
      including across a cut; the fallback branch (which fires routinely on musical overlap windows) never
      relabels a segment.
- [x] 2.13 REFACTOR: consolidate the tokenizer helper shared by matcher and fallback; suite green.

---

## Slice 3a: ffmpeg Extraction + Argv/Path Safety — DONE (split into 3a-i, 3a-ii, 3a-iii)

**Actual: 786 lines vs ~200 estimated — 3.9x, the worst overrun since slice 1.** Suite 165 passed +
10 skipped, `mypy src tests` clean over 54 files.

| Unit | Commit | Lines |
| --- | --- | --- |
| 3a-i | `feat(adapters): compose ffmpeg argv and contain paths…` | 260 |
| 3a-ii | `feat(adapters): implement AudioExtractorPort over the ffmpeg binaries` | 342 |
| 3a-iii | `test(integration): exercise ffmpeg extraction against the real binary` | 184 |

**✔ VERIFIED 2026-08-31 — the integration tests now run and pass.** ffmpeg 9.0.1 (gyan.dev, installed
via winget) put `ffmpeg`/`ffprobe` on PATH, and all 13 `integration`-marked tests across 3a and 3b pass
against the real binaries on the first attempt. The flag set written from documentation and never
executed — `-protocol_whitelist file`, `-map 0:a:0`, `-c:a flac`, `mpeg4`/`aac` for fixture synthesis —
is accepted as written. Nothing needed changing.

**Deviation — the fixture is generated, not checked in.** Task 3.5 called for "a tiny checked-in
fixture", but `.gitignore` lines 25/29/30 exclude `*.mp4`/`*.mp3`/`*.wav` to keep operator media out of
the repository. A committed media fixture would have been silently ignored by git: the test would have
looked present and never run. It is synthesized with `-f lavfi` instead, which needs no `.gitignore`
negation and puts no binary in the history.

**Why 3.9x.** The estimate treated "first adapter" as a +10-15% uncertainty margin on a
pure-logic baseline. It is not a margin, it is a different category: an injected process runner, a
recording double, JSON parsing with four failure paths, 15 parametrized hostile filenames, and a
second whole test tier (integration) that did not exist before. **Revised unit: a first-of-its-kind
adapter ≈ 700-800 lines.** Slices 4a, 5a, 7a and 8a each introduce one and are still estimated on the
old margin.

Closes: `audio-extraction` Video to Normalized Audio; `project-bootstrap` ffmpeg Declared as a System Dependency;
threat-matrix row **ffmpeg subprocess argv**. First real subprocess adapter in the codebase — no slice 1 comparable,
+15% uncertainty margin applied.

- [x] 3.1 RED: `tests/unit/adapters/ffmpeg/test_argv_composition.py` — hostile filenames (`;`, `--`,
      leading `-`, spaces) produce list-form argv, never a shell string.
- [x] 3.2 GREEN: `adapters/ffmpeg/extractor.py` — `probe`/`extract` via `subprocess.run([...])`, never `shell=True`.
- [x] 3.3 RED: path-outside-job-dir test — a resolved path escaping the job directory is rejected before spawn.
- [x] 3.4 GREEN: add `Path.resolve()` containment check before every ffmpeg invocation.
- [x] 3.5 RED: `integration`-marked test against a tiny checked-in fixture — extraction produces a
      16kHz mono FLAC `AudioTrack`; skips via `ffmpeg_available` fixture when ffmpeg is absent.
- [x] 3.6 GREEN: wire the real ffmpeg extract command (`-nostdin -protocol_whitelist file`, explicit timeout).

## Slice 3b: ffmpeg Slicing + Runtime Check + Integration Fixture — DONE (split into 3b-i, 3b-ii, 3b-iii)

**Actual: 732 lines vs ~190 estimated — 3.9x**, the same factor as 3a. Suite 184 passed + 13 skipped,
`mypy src tests` clean over 57 files.

| Unit | Commit | Lines |
| --- | --- | --- |
| 3b-i | `feat(adapters): fail with install instructions when ffmpeg is off PATH` | 201 |
| 3b-ii | `feat(adapters): slice the normalized track into planned chunks` | 339 |
| 3b-iii | `docs: add README, and cover slicing in the integration tests` | 192 |

**✔ VERIFIED 2026-08-31 — all 13 integration tests pass against real ffmpeg.** The claim that mattered
most, `test_a_slice_really_holds_the_requested_duration`, is the one that confirms `-ss` before `-i` and
`-t` as a length behave as the argv unit tests assume. They do. The re-encode decision below is
therefore verified end to end, not just argued.

### Port-contract gap found during implementation

`AudioExtractorPort.slice()` must return an `AudioChunk`, which carries a `job_id` — but none of its
three arguments has one. `AudioTrack` knows only its `media_id`; `PlannedChunk` is pure geometry; the
`ChunkPlan` that *does* hold the job id is never passed. Neither the design nor the spec noticed.
Resolved by putting `job_id` on the adapter's constructor next to `job_dir`, which the adapter already
took, so it is per-job by construction. Rejected alternative: deriving it from `job_dir.name`, which is
shorter and a hidden coupling. Cost: 25 existing construction sites updated across three test files.

### Design decision not in the plan: re-encode rather than stream-copy

`-c:a copy` would be faster, but it lands each cut on the nearest frame boundary. The stitcher derives
its contested window from the **plan**, so drifted actual boundaries would desynchronise the two. Chunks
are re-encoded to the same 16 kHz mono FLAC and report the plan's boundaries, not the file's. On this
format the encode cost is far below the transcription it feeds.

### A coupling this slice introduced and then fixed

The PATH check deliberately reads the real `PATH` — that is what it exists to check — which meant every
runner-injected unit test suddenly asserted something about whether the machine had ffmpeg. Ten tests
broke. An autouse fixture in `tests/unit/adapters/ffmpeg/conftest.py` now isolates them, and only
`test_availability.py` drives PATH answers explicitly.

Closes: `audio-extraction` Chunk Slicing, ffmpeg Runtime Availability Check. Split from 3a because slicing depends
on the chunk plan (slice 2a), while probe/extract do not — keeping them together would create an artificial
dependency the design doesn't require.

- [x] 3.7 RED: `integration`-marked slicing test — an N-chunk plan produces N `AudioChunk`s matching
      boundaries/overlap.
- [x] 3.8 GREEN: implement `slice()` against the plan.
- [x] 3.9 RED: ffmpeg-missing-from-PATH test — actionable error naming ffmpeg, not a raw subprocess exception.
- [x] 3.10 GREEN: PATH check at first use; raise `FfmpegUnavailable` with remediation text.
- [x] 3.11 Update `README.md` with ffmpeg install as a step distinct from `pip install -r requirements.txt`.
- [x] 3.12 REFACTOR: share the subprocess-invocation helper between `probe`/`extract`/`slice`; suite green.

---

## Slice 4a: Filesystem `TranscriptStoragePort` Adapter — DONE (split into 4a-i … 4a-vi)

**Actual: 1,642 lines vs ~340 estimated — 4.8x, the worst ratio of the change so far.** Suite 276 passed,
0 skipped, `mypy src tests` clean over 66 files.

| Unit | Commit | Lines |
| --- | --- | --- |
| 4a-i | `feat(adapters): decode persisted job state instead of trusting it` | 319 |
| 4a-ii | `feat(adapters): persist transcript content without dropping its classification` | 271 |
| 4a-iii | `feat(adapters): store the job record as one directory per job` | 286 |
| 4a-iv | `feat(adapters): persist the plan, transcript and artifacts a job accumulates` | 293 |
| 4a-v | `feat(adapters): commit a chunk result by rename so resume can trust it` | 289 |
| 4a-vi | `feat(adapters): request cancellation through a file the worker does not own` | 194 |

Closes: `transcript-artifacts` Intermediate Chunk Result Persistence, Storage Location (Q5 assumption),
Per-Job Storage Isolation, Plain-Text Export.

### Why 4.8x, and what the estimate keeps missing

The 3a retro already revised "first-of-its-kind adapter" up to ~700-800 lines and warned that 4a was
still costed on the old +15% margin. That warning was right and still too low. Two costs were not in
any calibration unit:

- **A codec is a second module.** Persisting nine entity types explicitly is ~270 lines of `src` before
  the adapter exists. Reflective `Entity(**payload)` would have been ~30, and was rejected: it accepts
  whatever an older process wrote and fails somewhere far away, which is the one thing resume cannot
  afford.
- **Absence is a test case per method.** Every read has a "not produced yet" state, every write has an
  "against a job that does not exist" state. Twelve port methods generate roughly twenty tests that a
  dict-backed fake never needed, because a dict has no half-created directories.

Test share was **68%** (1,113 test / 529 src), against the 56% the rev-3 calibration assumed. **Revised
unit: an adapter with its own persistence format ≈ 1,600 lines.** Slice 5a (web/upload) is the next one
carrying an unmeasured category, and is still estimated at ~260.

### Decisions taken here that were not in the design

- **`create_job` refuses an existing id** (`JobAlreadyExists`). The port declares create and update
  separately; a create that also overwrote would let a reused id silently discard a running job's state.
- **Reads are tolerant, writes are strict.** `load_chunk_plan`/`load_transcript` return `None` for a job
  that does not exist, but every save requires `job.json` to be present. A save against an uncreated job
  would leave a directory holding a transcript and no record — the exact shape `list_jobs` skips, so the
  orphan would be invisible rather than merely wrong.
- **Every write is atomic, not just `save_chunk_result`.** The design specified the `.tmp` → fsync →
  `os.replace` contract only for chunk results. A torn `job.json` is no more survivable, and the worker
  rewrites it at every state transition, so `_write` is the one commit path.
- **`list_jobs` skips a non-job directory but not a corrupt job record.** A scratch folder is not a
  listing failure; a job silently missing from the list invites re-running a three-hour transcription.
- **A malformed job id reports `JobNotFound`, not a distinct error**, so the store never reveals which
  ids exist. The id is validated *before* being joined onto a path, rather than resolved and then checked
  for containment — containment answers "did we escape?" after the path exists.
- **`request_cancellation` / `cancellation_requested` live on the adapter, not the port.** The port has no
  cancellation method and the design's single-writer rule needs one. Whether it is promoted to
  `TranscriptStoragePort` is slice 4b's call, when the core loop actually polls it.

- [x] 4.0a **RED (new)**: `tests/unit/adapters/storage/test_filesystem_transcript_storage.py` — `create_job`/
      `load_job`/`update_job`/`list_jobs` round-trip through real JSON files on disk; `save_chunk_plan`/
      `load_chunk_plan`, `save_transcript`/`load_transcript`, `save_artifacts`, `export_text` round-trip.
- [x] 4.0b **GREEN (new)**: `adapters/storage/filesystem_transcript_storage.py` implementing all non-atomic
      `TranscriptStoragePort` methods against `{ONEVOICECUT_DATA_DIR}/jobs/{job_id}/`.
- [x] 4.15 RED: atomic chunk-write test — a simulated crash between `.tmp` write and `os.replace` leaves
      the loader unaffected by a stale `.tmp`.
- [x] 4.16 GREEN: `save_chunk_result` — `os.replace`, fsync-before-replace, stale `.tmp` ignored by loader.
- [x] 4.17 RED: single-writer test — only the worker writes `job.json`; `control.json` cancellation flag
      is polled at chunk boundaries.
- [x] 4.18 GREEN: `control.json` read path in the filesystem adapter.

## Slices 4b + 4c + 4d — ALL DONE (delivered as nine units)

**Actual: 2,425 lines vs ~680 estimated — 3.6x.** Suite 362 passed, 0 skipped, `mypy src tests` clean
over 81 files. Test share 70% (1,689 test / 736 src), consistent with 4a's 68% and again above the
rev-3 calibration's 56%.

| Unit | Commit | Lines |
| --- | --- | --- |
| 4b-i | `feat(ports): storage answers where a job's working files go` | 129 |
| 4b-ii | `feat(usecases): drive one job from source media to a stored transcript` | 383 |
| 4b-iii | `feat(usecases): contain a chunk failure, and stop when the operator says stop` | 399 |
| 4b-iv | `feat(usecases): retry a failed chunk, but never a timed-out one` | 309 |
| 4b-v | `feat(runtime): resolve the engine a job asked for, and never substitute it` | 219 |
| 4c-i | `feat(domain): derive progress from what is on disk, never from a counter` | 230 |
| 4c-ii | `feat(usecases): make resume a property of the loop, not a second code path` | 371 |
| 4d-i | `feat(adapters): record the source media the worker will need hours later` | 127 |
| 4d-ii | `feat(runtime): headless worker entrypoint, one process per job` | 380 |

Closes: `transcription-jobs` Asynchronous Job Lifecycle, Chunk-Level Failure Isolation, Per-Chunk
Timeout, Resume From First Incomplete Chunk, Chunk-Level Progress Reporting, Job Record Carries Speaker
Mode and Engine Choice (propagation half); `transcript-artifacts` Retention Is Unbounded (seam only).

### Task 4.20 has nothing to extract, and that is the result

The task called for extracting "the chunk-loop state machine shared by `transcribe_job`/`resume_job`".
There is no such shared machine, because 4c did not build a second loop. **Resume is a property of the
one loop**: it asks `pending_chunks` what is still owed and works on that, so a first run and a restart
take the same route. A dedicated resume path would have executed rarely, been tested rarely, and been
exactly where a bug survives a year. The refactor the plan anticipated was avoided rather than
performed.

### Deviations from the task list, and why

- **4.4 named `usecases/progress.py`; `derive_progress` landed in `domain/jobs.py`.** It is pure
  arithmetic over domain entities with no port involved, and `domain/transcript.py` already establishes
  that shape — `without_music` and `render_message_text` are the same thing. The codebase's own
  precedent beat the plan.
- **4.12 named a "watchdog stub".** None was built. The use case passes `timeout_s` into the request and
  treats the resulting failure as a chunk failure; enforcing a wall-clock kill needs a real process to
  supervise, which is slice 7b's job. A stub would have been a stub of nothing.

### Design decisions taken here that the design did not specify

- **`ChunkTimeout` is a new error, and it is the one failure that is never retried.** A retry mostly
  spends the budget again: three attempts at a thirty-minute chunk is ninety minutes to learn the same
  thing. It subclasses `TranscriptionFailed` so adapters raising it satisfy existing callers, and is
  caught first so the broader handler cannot retry it.
- **`DiarizationUnsupported` is not contained per chunk.** Every remaining chunk raises it identically,
  so containing it would discover the same fact 87 times and then blame the last chunk for the job's
  configuration. It fails the job at the first chunk.
- **A job with any failed chunk never stitches.** A hole reads as continuous text once stitched, because
  the words either side run together. `FAILED`, no transcript, failed indices named on the record.
- **`transcribe_job` returns `JobRecord`, not `Transcript`.** Three outcomes are normal — completed,
  failed, cancelled — and only one produces a transcript.
- **The engine resolver never substitutes.** Falling back from local to cloud would, on precisely the
  job where the distinction mattered, ship private material to a provider and report success.
  `EngineUnavailable` instead. Adapters are constructed at resolution so a missing key fails before the
  run, not three hours in.
- **An existing chunk plan is reused, never recomputed.** Stored results are indexed against the stored
  plan; a plan differing by one chunk would re-map every completed result onto the wrong range.
- **Extraction re-runs on resume.** Minutes against hours of ASR, and the alternative — a use case
  stat-ing the filesystem to decide — would put I/O above the ports.
- **The purge seam takes `keep`, not `remove`.** A policy listing what to delete silently grows to cover
  new artifact kinds; one listing what to keep fails closed. Source video, transcript and artifacts are
  absent from the purgeable set: the first cannot be regenerated, the others are the product.

### Port gaps the wiring exposed

Both found by writing the composition root, neither anticipated by the design — the same shape as the
`job_id` gap `AudioExtractorPort.slice()` turned out to have in 3b.

1. **`audio_path` / `chunk_path`** — the loop must hand the extractor somewhere to write. Composing
   those paths in a use case would put the on-disk layout in a second module.
2. **`save_media` / `load_media`** — the worker runs in another process and could not rebuild
   `SourceMedia`: the job record carries only a `media_id`, and an invented checksum is worse than none.

`cancellation_requested` was also promoted onto the port, which 4a had explicitly left for this slice to
decide. The Protocol conformance assignment added in 4a-vi caught the real adapter falling behind the
port the moment it grew — before any caller existed.

- [x] 4.1 RED: `tests/unit/usecases/test_transcribe_job.py` — job runs against fake ports through all
      chunks, `JobRecord` transitions `PENDING→…→COMPLETED`.
- [x] 4.2 GREEN: `usecases/transcribe_job.py` orchestrating plan → slice → transcribe → persist per chunk.
- [x] 4.3 RED: progress derived from `results/` listing vs `ChunkPlan`, never a mutable counter; ETA
      `None` until first chunk done.
- [x] 4.4 GREEN: pure derivation function (`domain/jobs.py`, see deviation above).
- [x] 4.5 RED: chunk-84-of-87 failure test — chunks 1-83 remain persisted/intact, job record not terminated.
- [x] 4.6 GREEN: per-chunk error isolation in `transcribe_job.py`; `ChunkResult(state=FAILED)` recorded.
- [x] 4.7 RED: `tests/unit/usecases/test_resume_job.py` — resume after a simulated crash continues at the
      first `!= DONE` chunk; completed chunks untouched.
- [x] 4.8 GREEN: `usecases/resume_job.py` — work-set = chunks where state != DONE.
- [x] 4.9 RED: transient-cloud-error retry test — only the failed chunk retries.
- [x] 4.10 GREEN: bounded per-chunk retry in `transcribe_job.py`.
- [x] 4.11 RED: per-chunk timeout test — a timed-out chunk marks `FAILED`, job continues; a
      3-hour job within per-chunk timeouts is never terminated on elapsed time alone.
- [x] 4.12 GREEN: `TranscriptionRequest.timeout_s` honored in-call (no watchdog stub — see deviation).
- [x] 4.13 RED: job-record propagation test — engine choice resolves to the matching fake adapter, and
      `speaker_mode=multi` produces a diarized request and a labelled transcript.
- [x] 4.14 GREEN: `runtime/engine_resolver.py` (fakes only this slice) + propagation wiring.
- [x] 4.19 RED+GREEN: headless entrypoint `python -m onevoicecut.runtime.worker --job-id <id>` proven by
      an E2E-style test — real filesystem, fake engines.
- [x] 4.20 REFACTOR: **not applicable** — no shared state machine exists to extract (see above).
- [x] 4.21 GREEN: add an unused `usecases/purge_job_artifacts.py` seam (`PurgeJobArtifacts(job_id, keep)`),
      no caller wired. **Answers Q6 later**.

---

## Slice 5a: Job Creation + Streaming Upload — DONE (split into 5a-i … 5a-iv)

**Actual: 1,017 lines vs ~260 estimated — 3.9x.** Suite 411 passed, 0 skipped, `mypy src tests` clean
over 93 files. Test share 61% (620 test / 392 src) — the lowest since 4a, because a route handler is
mostly delegation and the schema does its own validating.

| Unit | Commit | Lines |
| --- | --- | --- |
| 5a-i | `feat(domain): generate the ULIDs, not just validate them` | 148 |
| 5a-ii | `feat(adapters): admit a job over HTTP without starting any work` | 321 |
| 5a-iii | `feat(adapters): stream an upload to disk without ever holding the file` | 271 |
| 5a-iv | `feat(adapters): PUT the sermon as a raw body, no multipart anywhere` | 279 |

Closes: `media-ingest` Non-Blocking Upload Acceptance.

### An unplanned prerequisite: nothing could mint an id

`domain/ids.py` only validated. Every test so far supplied its own id, and admission is the first code
that has to create one — so ULID generation landed first, as its own unit. Written rather than added as
a dependency: the encoding is forty lines, and the property the system already leans on deserves a test
rather than trust. `list_jobs` returns jobs in directory-name order and calls that creation order, which
is true only because the 48-bit millisecond timestamp comes first and big-endian. That claim now has a
test.

### Two defects the tests caught, both real

1. **The fake storage raised `KeyError` where the real adapter raises `JobNotFound`.** The upload route
   catches `JobNotFound`, so it worked against the real adapter and 404-ed into a 500 against the double.
   A fake that raises a different type than the thing it stands in for is not a fake — it is a second
   implementation with its own contract, and every caller written against it is wrong somewhere else.
2. **HTTP header values are ASCII and the source language is not.** `predicación del domingo.mp4` cannot
   travel in a header as written, and that filename is the ordinary case here rather than an edge case.
   The filename now travels percent-encoded and is decoded on arrival; decoding is a no-op for a plain
   ASCII name, so a simple client still works.

### Deviation: the stored source has no extension

The design sketch said `jobs/{ulid}/source{ext}`. The extension was never load-bearing — content type is
validated by `ffprobe`, never by a suffix — so keeping it removes nothing and adds one more thing a
client could influence. `source_path` is extensionless. This also removes the need for slice 5b to
derive a path extension from an allowlist; what 5b still owes is validating the *probed* container.

### How the memory claim is actually proven

Task 5.3 asked for constant memory. Structural absence of `UploadFile` is necessary but not sufficient —
a handler could still accumulate the stream itself. So the writer's test measures peak heap while eight
megabytes stream past in 64 KiB chunks, and the threshold was checked against a deliberately
accumulating writer, which peaks at 17 MB against a 2 MB limit. Watching the file grow on disk was tried
first and does not work: Python's buffered writer holds early chunks in memory anyway, and durability of
a half-finished upload has no consumer.

A second, structural test parses every module in the web adapter and asserts `UploadFile`, `File` and
`Form` are never imported — an absence a request-level test cannot demonstrate.

- [x] 5.1 RED: `tests/unit/adapters/web/test_admit_job_route.py` — `POST /api/jobs` with valid JSON
      returns `201 {job_id}`; missing engine returns `422`.
- [x] 5.2 GREEN: FastAPI app skeleton, `POST /api/jobs` route + Pydantic schema, `AdmitJob` wiring
      (single-speaker/no-diarization path only — MI5 rejection lands slice 6).
- [x] 5.3 RED: `PUT /api/jobs/{id}/media` streams raw bytes to disk with constant memory (assert no
      `UploadFile`/multipart path exists).
- [x] 5.4 GREEN: `adapters/web/routers/jobs.py` — `async for part in request.stream()` writer;
      `adapters/storage/media_source.py` (real, replacing the fake for this path).

## Slice 5b: Upload Security Threat-Matrix — DONE (split into 5b-i … 5b-iv)

**Actual: 1,018 lines vs ~270 estimated — 3.8x.** Suite 465 passed, 0 skipped, `mypy src tests` clean
over 98 files. Test share **81%** (828 test / 190 src) — the highest of the change, and expected: a
threat matrix is mostly cases, and the code that answers them is short.

| Unit | Commit | Lines |
| --- | --- | --- |
| 5b-i | `feat(adapters): two size checks, because one of them can be lied to` | 332 |
| 5b-ii | `feat(adapters): decide what an upload is by looking inside it` | 343 |
| 5b-iii | `test(integration): prove ffprobe rejects what we assume it rejects` | 130 |
| 5b-iv | `feat(adapters): validate the job id at the door, not only in storage` | 217 |

Closes: threat-matrix rows **Uploaded-file classification**, **HTTP routing/path params**, **Resource
exhaustion at ingest**; `media-ingest` Upload Size Limit.

### A defect the size tests found that was not about size

Writing to the destination directly truncates it before the first byte arrives, so **a failed retry was
already destroying the upload that had succeeded**. The upload now commits by rename — sibling `.part`,
fsync, `os.replace` — the same commit the storage adapter uses for JSON, and for the same reason.
Cleanup runs on any failure, not only the size limit, because a dropped connection leaves exactly the
same truncated file. A truncated sermon is the dangerous leftover: a partial `.mp4` frequently still
probes as valid media, so anything surviving an aborted upload would be transcribed as if it were the
whole service.

`.part` rather than `.tmp` on purpose — it can be gigabytes and can outlive a crash, so an operator
clearing disk space should be able to tell an abandoned upload from an interrupted metadata write.

### The first version of the traversal test proved nothing

Against a permissive storage, `..` and `../..` **never reach the handler at all** — the client and the
router normalise them away — so a test using those passes whether or not the route validates anything.
The forms that survive routing are **`%2e%2e`**, which arrives decoded as `..` in the path parameter,
and a plain `not-a-ulid`. Both reached the writer before 5b-iv.

This also exposed why route-level validation was needed at all: the previous safety was an artifact of
statement order, since a hostile id happened to die at `load_job` first. A handler that built the writer
before loading the job would have handed it a path outside the data directory.

### Tasks 5.7/5.8 were already closed by slice 5a

The hostile-filename rule landed with the writer: the filename is recorded and never consulted, and the
destination is chosen before the request is read. **The container allowlist named in 5.8 does not exist
and should not** — 5a made the stored path extensionless, so there is no path extension to derive. What
replaced it is 5.9/5.10: the *probed* container is what gets recorded, and `unverified` stops being the
value at exactly that point.

- [x] 5.5 RED: oversized-upload test — `Content-Length` precheck rejects before any bytes read; a lying
      header caught by a running byte counter aborts and deletes the partial file.
- [x] 5.6 GREEN: implement both checks against the injected upload limit.
- [x] 5.7 RED: hostile-filename test — `../../etc/passwd`-style filename never becomes a path component;
      stored path stays inside the job dir. *(Landed in 5a.)*
- [x] 5.8 GREEN: filename treated as metadata only. **Deviation**: no container allowlist and no path
      extension — the stored path is `jobs/{ulid}/source`, and content type comes from `ffprobe`.
- [x] 5.9 RED: non-media-content-with-media-extension test — `ffprobe`-based validation rejects it, not
      the extension. Includes an `integration`-marked test against the real binary.
- [x] 5.10 GREEN: `probe()` call (slice 3a) in the ingest path raises `UnsupportedContainer`; a container
      with no audio stream is refused too, and the refused file is discarded.
- [x] 5.11 RED: `job_id` path-traversal test per route (`%2e%2e`, backslashes, null byte, wrong lengths,
      excluded Crockford letters) rejected before the writer is reached.
- [x] 5.12 GREEN: route-level validation reusing `domain/ids.py`.

## Slice 5c: Status Route + App Wiring + E2E — DONE (split into 5c-i … 5c-v)

**Actual: 1,274 lines vs ~250 estimated — 5.1x, the worst ratio of the change.** Suite 511 passed,
0 skipped, `mypy src tests` clean over 104 files. Test share 74%.

| Unit | Commit | Lines |
| --- | --- | --- |
| 5c-i | `feat(adapters): report chunk-level progress over HTTP` | 309 |
| 5c-ii | `feat: export the .txt, and hand the job to a worker after upload` | 318 |
| 5c-iii | `feat(runtime): compose the web process from configuration` | 219 |
| 5c-iv | `feat(runtime): mark jobs whose worker died, and let a worker claim its own` | 243 |
| 5c-v | `test(integration): a sermon uploaded over HTTP becomes a transcript on disk` | 195 |

Closes: `transcription-jobs` Chunk-Level Progress Reporting (HTTP surface); the ingest→worker→poll loop
proven end to end for the first time.

### Why 5.1x: the E2E did not expose a gap, writing towards it did

Task 5.17 anticipated "any wiring gap the E2E test exposes". The E2E passed on its first run — because
two gaps were found and closed *before* it, while building what it needed:

1. **The core loop never wrote the `.txt`.** It stored the transcript and stopped. The export is what
   the operator actually opens, so a job that finished without one had not finished. It is written only
   on a complete run, since an export over a hole reads as a whole sermon.
2. **Nothing started the worker.** Admission cannot be the trigger — there is nothing to transcribe yet
   — and nothing polls for work. The upload is the handoff, and the media record is saved before the
   starter is called, because the worker's first act is to read it.

A third gap surfaced while writing reconciliation: **the worker never recorded its pid**, so every
running job would have looked abandoned after a web restart and been marked `INTERRUPTED` out from
under a live process.

### The liveness probe was verified, not assumed

Reconciliation must not overwrite the record of a worker that outlived its parent — the normal case,
since workers are separate processes. That needs a liveness check, and on Windows most signal values
passed to `os.kill` reach `TerminateProcess`, which made the POSIX idiom look dangerous.

Measured on CPython 3.12 / Windows: **`os.kill(pid, 0)` is special-cased** — a live process survives it,
a dead pid raises `OSError`, and `os.kill(pid, 9)` on the same platform kills with exit code 9. So the
idiom holds here. A recycled pid reads as alive; accepted on a single-operator machine against a
stronger check nobody maintains.

### Task 5.18 was already done, and 5.15 named something that does not exist

`adapters/web/schemas.py` was created in slice 5a, so the refactor had nothing to extract.

5.15 called for spawning a `Supervisor`. There is no such object: the web process spawns a worker
process per job at upload, and there is nothing to supervise between them — the watchdog that *would*
supervise a running chunk is slice 7b. What 5.15 actually needed was the composition root, and that is
what landed.

- [x] 5.13 RED: `GET /api/jobs/{id}` status test — chunk-derived progress + ETA surfaced over HTTP.
- [x] 5.14 GREEN: status route reading `derive_progress` output; read-only, asserted by test.
- [x] 5.15 GREEN: `runtime/app.py` composition root + `runtime/settings.py`; lifespan verifies ffmpeg and
      reconciles `TRANSCRIBING` + no live PID → `INTERRUPTED`. **No `Supervisor`** — see above.
- [x] 5.16 RED: E2E test — real HTTP + real filesystem + **real ffmpeg** + fake ASR: upload → poll → `.txt`.
- [x] 5.17 GREEN: the two wiring gaps were closed before the E2E, which then passed first run.
- [x] 5.18 REFACTOR: **not applicable** — `adapters/web/schemas.py` already existed from slice 5a.

---

## Slice 6: Per-Job Speaker Mode + Engine Selection + Diarization Rejection (~300 lines)

Closes: `media-ingest` Per-Job Speaker Mode Input, Per-Job ASR Engine Selection, Reject Incompatible
Engine/Speaker-Mode Combination at Admission (all 3 scenarios); `speech-transcription` Reject Speaker-Mode
Jobs the Adapter Cannot Satisfy (defense-in-depth half). **Stays a single, un-split slice per explicit
constraint** — it is almost entirely validation logic reusing existing pieces (the fake `TranscriptionPort` already
raises `DiarizationUnsupported` on `speaker_mode=MULTI`, built in slice 1), so it does not carry the adapter/I/O
tax that forced other slices to split. No uncertainty margin applied — directly comparable to slice 1's use-case
test ratio.

- [x] 6.1 RED: speaker-mode-omitted test — defaults to single-voice.
- [x] 6.2 GREEN: schema default + domain default.
- [x] 6.3 RED: multi-speaker-declared test — job record stores `speaker_mode=multi-speaker`.
- [x] 6.4 GREEN: propagate through `AdmitJob`.
- [x] 6.5 RED: engine-not-selected test — `422`, no job created.
- [x] 6.6 GREEN: required-field validation in the `POST /api/jobs` schema.
- [x] 6.7 RED: engine-selected test — job record stores `engine_choice=local`.
- [x] 6.8 GREEN: propagate through `AdmitJob`.
- [x] 6.9 RED: incompatible-combination test — `speaker_mode=multi` against `diarization=UNSUPPORTED`
      rejects before job creation; error names the missing capability, suggests switch-engine-or-drop-mode.
- [x] 6.10 GREEN: `AdmitJob` capability check via `engine_resolver.resolve(engine).capabilities()`.
- [x] 6.11 RED: zero-chunks-processed test — a multi-hour fixture with an incompatible combination never
      reaches chunk dispatch; no billable/local-model call recorded by the fake.
- [x] 6.12 GREEN: confirm rejection strictly precedes `ingest_media`/`transcribe_job` invocation.
- [x] 6.13 RED: compatible-combination test — `diarization=AVAILABLE` + multi-speaker admits normally.
- [x] 6.14 GREEN: confirm existing path unaffected.
- [x] 6.15 RED: port-level defense-in-depth test — a fake `diarization=UNSUPPORTED` adapter refuses
      (names the capability) if asked to transcribe with `speaker_mode=multi`, simulating an admission bypass.
- [x] 6.16 GREEN: guard clause at the top of every adapter's `transcribe()` (fakes now; real adapters
      inherit it in slices 7a/8a).
- [x] 6.17 REFACTOR: extract the compatibility check into one `usecases/admit_job.py` helper reused by
      the schema-level and port-level checks; suite green.

---

## Slice 7a-i: Local ASR Adapter Construction (~450 lines)

Closes: `speech-transcription` TranscriptionPort Contract (local, single-speaker path, core adapter). First real
ASR adapter — no slice 1 comparable; calibrated as a first-of-its-kind adapter unit. Real-engine work is
`localmodel`-marked, excluded from the default run — the contract-test body is still authored/committed code and
counted here.

- [x] 7.1 RED: `localmodel`-marked contract test — real `faster-whisper` adapter satisfies the shared
      single-speaker contract body.
- [x] 7.2 GREEN: `adapters/asr/local/faster_whisper_adapter.py` implementing `TranscriptionPort`;
      `capabilities()` still returns `DiarizationSupport.UNSUPPORTED` (diarization lands slice 9a), real
      `max_chunk_bytes=None`, real `max_chunk_duration_s`.

## Slice 7a-ii: Shared Contract Module + Resolver Registration (~250 lines)

Closes: `speech-transcription` Contract Parity and Declared Divergence (local half, registration). Depends on
7a-i.

- [x] 7.3 RED: shared `tests/contract/` module, parametrized to include the local adapter alongside the
      existing fake, `localmodel`-marked, excluded from the default run.
- [x] 7.4 GREEN: register the adapter in `runtime/engine_resolver.py` for `EngineChoice.LOCAL`.

### Measured, split, and one defect found

Measured 504 lines against the ~250 estimate (**2.0x**), so split at the natural task seam — the two
halves are independent and each is green alone:

| Unit | Task | Lines |
| --- | --- | --- |
| Shared contract module + fake and local call sites | 7.3 | 341 |
| Lazy resolver registration | 7.4 | 163 |

**Defect found and fixed (pre-existing, from 7a-i).** `tests/unit/adapters/asr/local/test_faster_whisper_adapter.py`
imported the adapter at module level, and the adapter imports `faster_whisper` at module level. pytest
imports every test module during collection, *before* it filters on markers — so on any checkout without
the optional local-ASR extras, `pytest -m "not paid and not localmodel"` failed at collection. That is
precisely the run that is supposed to need none of those extras. Proven by simulating a bare checkout
with an import blocker, then fixed with `pytest.importorskip` above the adapter import, and re-verified:
890 tests collect cleanly with `faster_whisper` unavailable.

The same hazard is why 7.4's registration is a factory that imports on call. `runtime/engine_resolver.py`
is imported by the composition root, and therefore by most of the suite; a module-level adapter import
there would have re-introduced the defect one layer down. An `ast` test asserts the absence, following
`tests/test_architecture.py` — an absence cannot be demonstrated by running something and watching it
not happen.

**Deviation.** 7.4 says "register the adapter"; the worker still passes `resolver=None` and exits 3, so
nothing constructs it in production yet. Wiring `runtime/worker.py` to build a real resolver needs a
model-size setting that no task has specified, so it is deliberately left out rather than invented here.
`production_factories(local_model_size=...)` is the seam that wiring will use.

## Slice 7a-iii: Non-Speech Classification (VAD) (~250 lines)

Closes: `speech-transcription` Non-Speech Segment Classification (local real-engine half). Depends on 7a-i;
mirrors the capability-axis calibration slice 1b established (~450–500 lines per axis; this unit is narrower
because the domain/port/fake scaffolding for the axis already shipped in 1b — only real-engine wiring is new).

- [x] 7.4a RED: `localmodel`-marked classification test — the local adapter declares
      `non_speech_classification=AVAILABLE` and marks a music-only fixture segment as `MUSIC`, not `SPEECH`.
- [x] 7.4b GREEN: enable the engine's voice-activity filter and the decoder guards that break degenerate
      repetition loops (`no_speech_threshold`, `compression_ratio_threshold`, `condition_on_previous_text`
      disabled); map their output onto `SegmentKind`.

### Measured on estimate, and the filter's second half

Measured **315 lines** against the ~250 estimate (**1.26x**) — the first unit in this change to land
inside its raw nominal estimate, and the fourth in a row to land inside the 800-line budget since the
smaller-target discipline started. Test share 57% (181 of 315), against the 65–80% the slice-7 forecast
expected: the adapter half was larger than usual here because the tiling below is production logic, not
scaffolding.

**Filtering non-speech is only half of the task, and the RED proved it.** Before this unit the chord
fixture came back as an **empty tuple** — Whisper's own `no_speech`/`logprob` guard had already discarded
the whole musical range, and `vad_filter=True` alone would have made that worse, not better. That
satisfies "no fabricated `SPEECH`" while violating the spec's separate *"Classification never discards
audio"* scenario, and it would have silently removed exactly the ranges slice 11's clip rendering has to
aim at. So the voice-activity pass runs twice over the same decoded samples: once inside the decode, to
starve the hallucination, and once alongside it (`get_speech_timestamps`), to restore the filtered ranges
to the result with their timestamps. `_tile` fills every remaining hole so the chunk always comes back
whole.

**Two non-speech kinds, decided by which detector said what.** A hole the voice-activity pass found no
voice in is `MUSIC`. A hole it *did* find voice in, but the decoder produced no text for, is `UNCERTAIN`
— a real disagreement between two detectors, and claiming to know which was right is the silent
degradation this axis exists to stop. The same reasoning sets the kind for decoded text carrying a high
`no_speech_prob`: `UNCERTAIN`, not `MUSIC`, because `without_music` drops `MUSIC` outright for every
message-facing consumer, so a misjudged sentence would vanish from the export rather than arrive marked.

## Slice 7a-iv: Hallucination Containment (~250 lines)

Closes: `speech-transcription` Non-Speech Segment Classification (hallucination-containment scenario). Depends
on 7a-iii's decoder guards.

- [x] 7.4c RED: `localmodel`-marked hallucination-containment test — a music-only fixture produces no
      `SPEECH`-classified segment carrying fabricated text (the Spanish subtitle-boilerplate failure).
- [x] 7.4d **GREEN (new — closes a gap in this revision)**: tune the 7.4b decoder guards (or add a targeted
      post-filter) until 7.4c's fixture produces no fabricated `SPEECH` text; the original task list left
      7.4c's RED with no paired GREEN.

### Measured on estimate; the guard that was doing nothing

Measured **266 lines** against the ~250 estimate (**1.06x**), test share 66%. Second unit in a row inside
its raw nominal estimate.

**The fixture had to be found, not assumed.** A plain tone, a chord, pink noise and pure silence were all
tried first and all fail to provoke anything: the voice-activity pass rejects them, the decoder never runs,
and a containment test built on any of them passes while proving nothing. The fixture that works is
harmonically rich, echoed and formant-shaped — it gets *past* the VAD, so the decoder actually runs. A
fourth test (`test_the_fixture_still_provokes_the_decoder`) exists purely to fail if the fixture ever goes
inert, because the other three would otherwise stay green forever after proving nothing.

**Two measurements ran across 30 fixture variants, and they point opposite ways:**

| Signal | Measured | Consequence |
| --- | --- | --- |
| `no_speech_prob` on every loop | 0.68 – 0.86 | Always above the 0.6 the adapter maps on, so **the SPEECH half of 7.4c was already closed by 7.4b** |
| `compression_ratio` on those same loops | 1.00 – 2.33 | **Never once reaches Whisper's 2.4 threshold.** The guard nominally responsible for breaking repetition loops catches none of them |

So `no_speech_prob` was carrying the entire containment alone, and 7.4d's real work was the second half the
task's own wording anticipated ("or add a targeted post-filter"). `_is_degenerate_loop` drops the invented
*text* — never the range, which stays addressable footage — and only when **both** conditions hold: the
engine declared the window non-speech *and* what it wrote there is a degenerate loop. Either alone would be
wrong. On probability alone it would discard real sentences the engine merely doubted; on repetition alone
it would silence a preacher saying "no, no, no, no" for emphasis, which is speech and belongs in the
transcript.

**Defect found and fixed (introduced by 7a-iii, one unit earlier).** `_tile` reports every non-speech range
with empty text, and `render_message_text` marked UNCERTAIN unconditionally — so the export gained a bare
`[?] ` line per silence, a marker marking nothing, once per gap across a three-hour recording. Empty-text
segments are ranges, not lines; the domain renderer now skips them. Caught by writing the export assertion
in 7.4c rather than by reading the diff.

**Not proven here, and it is the case that matters most.** Every fixture above is synthesised with ffmpeg,
and no synthetic signal reached `no_speech_prob` ≤ 0.6. Real singing is a real voice and very plausibly
does, which would classify sung lyrics as `SPEECH` and put them in the message. That is the project's
stated normal case, it cannot be reproduced without real media, and CLAUDE.md forbids committing media.
Closing it needs a fixture the operator supplies from an actual recording.

Which is what `scripts/try_local_asr.py` is for, and the reason a `scripts/` directory appears here. It is
a development tool — outside the spec, outside `mypy src tests` — that runs a window of a real recording
through `local_transcriber`, the same lazily-imported factory the resolver uses, and prints each segment's
kind and timestamps, the per-kind totals, the share of the window covered, and the `transcript.txt` that
would be delivered. It talks to the adapter directly because `runtime/worker.py` still passes
`resolver=None` and exits 3; wiring that needs a model-size setting no task has specified yet.

---

## Slice 7b-i: Watchdog Core (~625 lines)

Closes: `transcription-jobs` Per-Chunk Timeout (uninterruptible-inference case). Split from 7a because the
watchdog is a process-supervision subsystem (`multiprocessing`, mtime polling, kill), not an ASR concern — rev 2
under-scoped this as a sub-task of "Local ASR Adapter" when it is really independent infrastructure.

- [x] 7.5 RED: supervisory watchdog test — no progress past `chunk_timeout_s` kills the worker process,
      chunk recorded `FAILED(TIMEOUT)`.
- [x] 7.6 GREEN: `runtime/supervisor.py` watchdog watching `results/` mtime.

### Came in under estimate because the signal already existed

Measured **572 lines** against the ~625 estimate (**0.92x**), test share 65%. Third unit in a row inside its
estimate. The estimate assumed `multiprocessing` and mtime-polling machinery; almost none of it was needed,
for the reason below.

**Deviation from 7.6's wording: the heartbeat, not `results/` mtime.** `multi-operator-access` shipped a
heartbeat the worker writes at the top of every chunk iteration, and a job reaches TRANSCRIBING only after
extraction and planning have finished — so for a job in that state the age of the heartbeat *is* how long
the current chunk has been running, which is exactly what a per-chunk timeout is defined over. Polling
`results/` mtime would reach around `TranscriptStoragePort` into the filesystem from the composition root
to rebuild a signal the port already publishes, and it would measure from the moment a chunk *finished*
rather than the moment the current one *started*.

**The second clock is the part that is easy to leave out.** Two conditions must hold together: the
heartbeat is stale, *and* the job has been in TRANSCRIBING for longer than the timeout. The worker does not
refresh its heartbeat during extraction, and extracting a three-hour recording outlasts a thirty-minute
chunk timeout comfortably — so the first sweep after a long extraction would kill a job that had only just
started working, turning the input this project exists for into the case it cannot process. There is a test
named for that alone.

**Scope held to the core.** The sweep is not yet wired into the lifespan supervisor, matching the unit's
stated rollback boundary of `runtime/supervisor.py` alone; it ships as a seam the way `purge_job_artifacts`
did. `chunk_timeout_s` joins `Settings` as `ONEVOICECUT_CHUNK_TIMEOUT_SECONDS`, per design.md — an
operator's knob, unlike the two-hour liveness bound, because it depends on hardware, model size and chunk
length.

**Noted for 7b-ii.** `supervisor.py` imports `process_is_alive` and `LivenessProbe` from `app.py`, which is
backwards: `app.py` is the FastAPI factory that also happens to hold the supervision helpers. Moving them
into `supervisor.py` with a re-export is a refactor, and 7b-ii is the refactor unit.

## Slice 7b-ii: Shared Adapter-Construction Refactor (~375 lines)

Closes: no new spec scenario — pure refactor. Depends on 7b-i (and, per the task's own text, is written against
the cloud adapter that lands in slice 8a; the apply phase may need to defer this unit until 8a is merged, the
same way task 4.20 discovered it had nothing to extract).

- [x] 7.7 REFACTOR: **not applicable at this point in the chain** — nothing shared exists to extract yet
      (see below). Re-open as part of slice 8a-ii, where the second factory first appears.

### Task 7.7 has nothing to extract either, and the note it carried is already closed

Two separate things were parked on this unit, and neither survives inspection.

**The adapter-construction/secret-read helper has no second caller.** `runtime/engine_resolver.py` reads no
environment at all — not one `os.environ`, no API key, no secret. Construction inputs arrive as arguments
(`production_factories(local_model_size=..., local_device=...)`), and the env reads that produce them live in
`runtime/worker.py` and `runtime/settings.py`, which is where a composition root's own environment belongs.
There is exactly one factory, `local_transcriber`. Extracting a helper shared with the cloud adapter cannot be
done before the cloud adapter exists, and doing it against a single caller would invent the abstraction rather
than discover it — the same trap 4.20 avoided. The work belongs to **8a-ii**, the unit that registers the
second factory and therefore is the first point at which "shared" means anything.

**The liveness note is already done, closed by slice 7c-ii rather than deferred.** 7b-i noted that
`supervisor.py` imported `process_is_alive` and `LivenessProbe` from `app.py`, which was backwards. They now
live in `supervisor.py` (`supervisor.py:64` and `:67`), and `app.py` re-exports `LivenessProbe` for its own
signatures. 7c-ii's wiring forced the move: `app.py`'s lifespan needed the watchdog sweep, and the sweep
needed the probe, so the cycle had to be broken to wire anything at all.

That leaves 7b-ii with an empty body. Marking it complete is the honest record — the alternative, leaving one
open checkbox in slice 7 for a refactor whose subject lands two slices later, reads as unfinished work rather
than as a decision.

## Slice 7c: Runtime Wiring (~500 lines) — NEW IN THIS REVISION

Closes no new spec scenario. It closes two **orphaned deviations** instead: work that earlier units
deliberately deferred and that no task anywhere in this document then owned. A search of all 215 open tasks
found nothing covering either, and both leave the delivered system unable to do the thing it is for.

- 7a-ii's deviation: "the worker still passes `resolver=None` and exits 3, so nothing constructs it in
  production yet. Wiring `runtime/worker.py` to build a real resolver needs a model-size setting that no task
  has specified." The setting still did not exist.
- 7b-i's deviation: the watchdog sweep ships as a seam, unwired. Slice 5c's own note had already punted the
  watchdog to "slice 7b", and 7b turned out to be the core (7b-i) plus an adapter refactor (7b-ii) — neither
  of which wires anything.

- [x] 7.8 RED: worker-entrypoint test — with no injected resolver, `main` builds one from its environment;
      with nothing configured it exits 3 naming the variable, before the job record is touched.
- [x] 7.9 GREEN: `LOCAL_MODEL_SIZE_ENV` + `configured_resolver()` in `runtime/worker.py`;
      `production_factories(local_model_size=None)` registers nothing rather than defaulting a size.
- [x] 7.10 RED **(defect found end-to-end)**: construction-time device-proof test — an engine that loads but
      cannot compute must fail as `EngineUnavailable` at construction, not as `TranscriptionFailed` on the
      first chunk.
- [x] 7.11 GREEN: `FasterWhisperTranscriber._prove` decodes one second of silence in the constructor;
      `ONEVOICECUT_LOCAL_DEVICE` selects the device and is named in the refusal.
- [x] 7.12 RED: lifespan test — the watchdog sweeps on a timer for the app's lifetime and is cancelled with it.
- [x] 7.13 GREEN: `WatchdogConfig` + a second supervised task in `runtime/app.py`'s lifespan.
- [x] 7.14 RED: reaping test — a worker that exits without claiming its job fails it with a reason the
      operator can read; one that dies mid-job leaves it INTERRUPTED; one that wrote its own outcome is
      left alone.
- [x] 7.15 GREEN: `WorkerProcesses` keeps the handles `_popen` used to drop, and `reap_exited_workers`
      runs at the head of every drain sweep.

### The defect the wiring exposed, which no unit test could have

Measured **431 lines** for 7.8–7.11 against the ~500 estimate (**0.86x**), test share 76%.

Wiring the worker was ten lines. What it bought was the first real end-to-end run — real HTTP, real ffmpeg,
real faster-whisper — and that run **hung**, then left the job stuck in TRANSCRIBING with a dead pid and an
empty `results/`.

The cause is a promise the adapter's own docstring made and did not keep: *"The engine loads in the
constructor, not on the first chunk … so a missing resource is an error before the run starts."* Loading the
weights is not proof. CTranslate2 allocates the model on the selected device and returns happily, then
resolves its compute libraries lazily on the first `encode()`. This machine has a GPU and no usable cuBLAS,
so `device="auto"` picked CUDA, construction succeeded, and `Library cublas64_12.dll is not found` surfaced
inside inference.

**The reason it is worth a task rather than a note: the failure is content-dependent.** A chunk the
voice-activity filter rejects never reaches the encoder, so it "succeeds". The first smoke run — a two-tone
chord, no speech — completed green on the broken device. The second, over audio with voice activity in it,
died. The same build transcribes music and dies on a sermon, and which one an operator meets first is luck.

`_prove` decodes one second of silence in the constructor. It never falls back to CPU: that is the same job
twenty times slower, chosen by nobody, and the identical silent substitution the resolver already refuses
between engines. The refusal names `ONEVOICECUT_LOCAL_DEVICE` so the message carries its own remedy.

Verified end to end after the fix, both branches: `auto` on this machine refuses at engine resolution with
the job left QUEUED and never claimed; `cpu` completes, and the transcript carries two UNCERTAIN segments
covering 100% of the window with the decoder's `"No, no, no…"` loop stripped — 7a-iii and 7a-iv holding
through the real pipeline rather than only against fixtures.

**Flagged here, closed by 7.14–7.15 below.** An unusable engine left the job QUEUED with no
operator-visible reason: the worker's exit code and its message went to the server's stderr, and `_popen`
neither waited for the child nor read its status.

### 7.12–7.13: the wiring settled 7b-ii's noted refactor by forcing it

Measured **555 lines** for 7.12–7.13, test share 48% — low for this project because roughly half the `src`
churn is a *move*, not new code. Slice 7c as a whole ran **986 lines against the ~500 estimate (1.97x)**,
which the ~500 never covered because it did not anticipate the device defect. Split at the natural seam, both
units land well inside the 800-line budget (431 and 555) and each is green alone.

**The import cycle decided the refactor 7b-i had only noted.** `app.py` needed `watchdog_once`, and
`supervisor.py` needed `process_is_alive` — a cycle. The direction that resolves it is the one 7b-i already
called backwards: liveness (`HEARTBEAT_STALE_AFTER_S`, `LivenessProbe`, `process_is_alive`,
`worker_is_alive`) moved into `supervisor.py`, where process supervision belongs, and `app.py` re-exports
each name so every existing caller and test is undisturbed. That closes the follow-up 7b-i left for 7b-ii,
which is now only about adapter construction.

**Two supervised tasks, not one sweep with a branch.** They answer different questions on clocks three
orders of magnitude apart — the drain asks "is there a free slot" every five seconds, the watchdog asks "has
this chunk stopped moving" every sixty against a thirty-minute timeout. Folding them together would tie that
judgement to the drain's cadence, and a drain sweep that raised would take the per-chunk timeout down with
it.

**Discovered by the RED, and worth stating: reconcile and the watchdog do not overlap.** The first fixture
had no heartbeat, so startup reconcile claimed the job as INTERRUPTED before the first sweep and the watchdog
correctly ignored it. The two divide by the question asked — reconcile asks whether a worker *exists*, the
watchdog asks whether an existing one is still *moving*. Only a heartbeat fresh against the two-hour liveness
bound and stale against the per-chunk timeout reaches the sweep at all, which is exactly the hung-worker case
and nothing else.

**One defect fixed in passing.** `Settings.chunk_timeout_s` derived the env name
`ONEVOICECUT_CHUNK_TIMEOUT_S`, while design.md documents `ONEVOICECUT_CHUNK_TIMEOUT_SECONDS` — added in
7b-i and wrong from the start. An operator setting the documented variable and watching it silently do
nothing is the worst of both, so both names are accepted via `AliasChoices`.

### 7.14–7.15: the exit code nobody was reading

Measured **441 lines** against no prior estimate (this unit did not exist until 7c-i's own end-to-end run
produced it), test share 66%.

`_popen` launched and walked away, discarding the one fact only a parent can observe. Two failures came out
of that, and both were met in the same session:

- An unusable engine makes the worker print its reason to the server's stderr and exit non-zero. The record
  stays QUEUED, so an operator with a browser watches a job that will never move and can read nothing about
  why. Verified over real HTTP before and after: `['queued']` forever became `['queued', 'failed']` with a
  reason on the record.
- A worker that dies *after* claiming leaves a worker-bound record with a dead pid. Only startup reconcile
  clears that, so the job is stranded until somebody restarts the server — the exact state the 7c-i smoke run
  left behind. The watchdog does not cover it either: it requires a *live* pid, because its question is
  whether a running worker is still moving.

**Classification is by what the record says, not by the exit code**, because the record is what the next
reader acts on. QUEUED means nothing else will ever write it → FAILED, naming the status. Worker-bound means
it claimed and died mid-flight → INTERRUPTED, the resumable off-ramp, which is what reconcile decides at boot,
now reached continuously. Terminal means the worker wrote its own account before exiting → left alone,
because replacing it with the parent's inference from an exit code loses the better answer.

Keeping the handle also stops finished workers becoming zombies on POSIX until the web process exits, which
never-waiting quietly guaranteed.

**Still not surfaced: the worker's own message.** The record says which status the worker exited with and
points at the server log; it does not carry the engine's actual complaint. Capturing the child's stderr means
pipe management and a deadlock risk if that pipe fills during a three-hour job, which is a larger decision
than this unit.

---

## Slice 8a-i: Cloud Adapter Construction (~450 lines)

Closes: `speech-transcription` TranscriptionPort Contract (cloud, core adapter). Real-engine work is
`paid`-marked, excluded from the default run. First real HTTP-client ASR adapter — calibrated as a
first-of-its-kind adapter unit, no slice 1 comparable.

- [x] 8.1 RED: `paid`-marked contract test — real cloud adapter satisfies the shared single-speaker
      contract body.
- [x] 8.2 GREEN: `adapters/asr/cloud/*_adapter.py` implementing `TranscriptionPort` with an HTTP client +
      in-call timeout; `capabilities()` returns real `max_chunk_bytes=25_000_000` (still
      `DiarizationSupport.UNSUPPORTED`), reads `CLOUD_ASR_API_KEY` at construction.

### The provider the task list had already chosen without naming it

The spec is deliberately provider-neutral, so the choice looked open. It was not: **8.2's own text fixes it**.
`max_chunk_bytes=25_000_000` is OpenAI's documented per-request cap and nobody else's — Deepgram and
AssemblyAI accept payloads three orders of magnitude larger — and "still `DiarizationSupport.UNSUPPORTED`"
matches the proposal's own row, *"OpenAI's Whisper API does not diarize at all"*. Both other candidates
diarize as a paid add-on, so an adapter built on either would have had to declare `AVAILABLE` and satisfy
slice 9's speaker path four slices early.

The model is `whisper-1` rather than a newer transcription model, and that is not a cost decision. It is the
one that still supports `response_format=verbose_json`, which is the only way the API returns **per-segment
timestamps**. The port's central promise is chunk-local timestamps; a model answering with a bare string
cannot satisfy it however good the text is.

### Deviations from 8.1/8.2, and why

- **The key is a constructor argument, not an environment read inside the adapter.** 8.2 says the adapter
  "reads `CLOUD_ASR_API_KEY` at construction". It is *checked* at construction — the refusal is
  `EngineUnavailable` naming the variable, raised before the job rather than on the first request — but the
  adapter never touches `os.environ`. It knows only the variable's *name*, for its own message. That is the
  precedent `LOCAL_DEVICE_ENV` already set in the local adapter, and the reason it exists is that an adapter
  reading the environment depends on the composition root that wires it. **The read itself lands in 8a-ii**,
  with the resolver registration, which is where the local half's read already lives.
- **Unit tests were added beyond the task list, and they are the point.** 8.1's contract test is `paid`, so it
  is excluded from the default run — leaving the adapter with *zero* executable coverage in the suite that
  actually gates every commit. `httpx.MockTransport` closes that: a real `httpx.Client`, real request
  construction, real response parsing, no socket and no bill. 31 tests in the default run, 7 `paid`.
- **Part of 8a-iv landed here, unavoidably.** Task 8.5a owns the cloud classification declaration, but the
  shared contract body asserts the *relationship* between the declaration and the behaviour — an adapter
  declaring `UNSUPPORTED` must be shown to return `UNCERTAIN` segments. 8.1 cannot pass without both halves,
  so the adapter declares `ClassificationSupport.UNSUPPORTED` and emits `UNCERTAIN` unconditionally now.
  **8a-iv keeps its real work**: confirming against the live API that the declaration still matches observed
  behaviour, which is exactly the thing a mock cannot answer.
- **The byte cap refuses before the upload, which reads like 8a-iii.** It is not: 8a-iii is about the
  *planner* sizing chunks against the real value. This is the adapter refusing a chunk that arrived oversized
  anyway. Learning the cap from a 413 costs the whole 25 MB transfer, per chunk, on a job with thousands of
  them — so the guard is worth its four lines here rather than waiting.

### Two things the code found that the plan did not

**Chunks are FLAC, not WAV.** `adapters/ffmpeg/argv.py` normalizes every chunk to 16 kHz mono FLAC and
`chunk_path` writes `{index:04d}.flac`. The API infers the codec from the submitted filename, and FLAC is on
its supported list — so this works, but only as long as the two stay in step. The content type is therefore
derived from the path suffix rather than hard-coded, and the map is commented as coupled to `argv.py`.

**This is the first adapter that can honour `timeout_s`.** The local one documents that it cannot: CTranslate2's
decode loop is uninterruptible from Python, so the supervisory watchdog is the only enforcement that exists
there. An HTTP call has a budget the client enforces itself, so `request.timeout_s` becomes the httpx read
budget and a breach raises `ChunkTimeout` — the subclass that exists specifically to *not* be retried. The
watchdog stops being the sole backstop and becomes the second one. A job with no budget still gets a
`FALLBACK_TIMEOUT_S` ceiling, because `None` would otherwise mean a hung socket holds a worker open until the
watchdog kills the process minutes later having produced nothing.

### Measured cost, and the split

**909 lines against the ~450 estimate (2.0x)** — adapter 300, unit tests 519, `paid` contract 90. Test share
67%, in line with the project's measured ratio.

Delivered as **two units** rather than one, because 909 is over the 800 budget: the adapter with its
default-run proof (819), then the `paid` contract test (90). Both are green alone. The seam was chosen for
that reason and not by line count — every *other* candidate split leaves a half that ships a stated invariant
violated. Carving failure translation out, for instance, would have made the first unit leak `httpx`
exceptions upward for one commit, and "an adapter never leaks a provider exception" is a convention this
repo enforces rather than suggests.

`close()` exists on the adapter and nothing calls it yet. One adapter is built per job, so its connection
pool outlives the job that opened it — a real leak, but wiring the lifetime belongs to the resolver in
**8a-ii**, not here.

## Slice 8a-ii: Resolver Registration (~150 lines)

Closes: `speech-transcription` Contract Parity and Declared Divergence (cloud half, registration). Depends on
8a-i.

- [x] 8.3 GREEN: register in `engine_resolver.py` for `EngineChoice.CLOUD`.
- [x] 8.3a REFACTOR **(inherited from 7.7)**: with a second factory finally present, extract whatever
      construction/secret-read shape the two branches actually share — and extract nothing if they share
      nothing. `local_transcriber` takes a model size and a device; the cloud factory takes a key and an
      endpoint, so a common helper is a hypothesis to test here, not a conclusion. Suite green.

### 8.3a: the hypothesis was wrong, and something smaller was right

7.7 imagined "adapter-construction/secret-read logic shared with the cloud adapter". With both factories
finally visible, **that helper still does not exist**. The two construct from disjoint inputs —
`local_transcriber` takes a model size and a device and defers a heavy optional import;
`cloud_transcriber` takes a key and a model name and defers nothing heavy at all — and neither reads a
secret, because the read is a composition-root act that happens in `worker.py`. A helper over those two
would have been parameter plumbing wearing a function's name.

What *is* shared is a **rule**, not code: a missing required value registers no engine rather than a broken
one. It is now stated once in `production_factories`' docstring and applied per engine in three lines. Naming
a rule is not the same as extracting a function, and this is the third refactor task in this change to end
that way (4.20, 7.7, now 8.3a).

The extraction that did survive is one 7.7 never mentioned: **`_configured(name)` in `worker.py`** — read an
environment variable, treat blank as absent. Three call sites, discovered rather than invented, and it
carries a reason that only became visible in 8a-i: stripping is not tidiness. A key read out of a file
carries a newline, and a newline in an HTTP header value is header injection that the client rejects
outright.

### Three things this wiring changed that 8.3 did not ask for

- **The unconfigured-build refusal now names both variables.** It predates the cloud engine and told every
  operator to set a faster-whisper model size — including one holding an API key with no intention of ever
  running a local model, who was sent to fix the wrong thing with complete confidence. A message whose whole
  job is carrying its own remedy has to carry the right one.
- **`run_job` resolves the engine before it builds the extractor**, which is what its docstring already
  claimed ("the engine is resolved *before* any work starts"). Keyword arguments evaluate left to right, so
  the extractor was in fact being built first, and an extraction failure could preempt the engine's own
  refusal — the refusal that costs a model load, a device proof or a key check to obtain.
- **`run_job` releases the adapter in a `finally`.** 8a-i flagged the unclosed connection pool as a leak;
  that was overstated. One worker process builds one adapter and exits, so the pool dies with the process
  either way. What it actually is: the difference between releasing a socket deliberately and leaving it to
  interpreter shutdown, and the failure path is where it earns its keep. `TranscriptionPort` still declares
  no `close` — the local engine holds nothing releasable and would implement one empty — so a
  `runtime_checkable` protocol finds it on adapters that have one.

### The variable keeps the task list's name, against the project's own convention

`CLOUD_ASR_API_KEY` has no `ONEVOICECUT_` prefix, and every other variable in this system does
(`ONEVOICECUT_DATA_DIR`, `ONEVOICECUT_LOCAL_MODEL_SIZE`, `ONEVOICECUT_LOCAL_DEVICE`,
`ONEVOICECUT_OPERATOR_TOKENS`, `ONEVOICECUT_CHUNK_TIMEOUT_SECONDS`). It is kept because 8.2 named it and the
adapter ships it in its own refusal, so the two have to agree — but an unprefixed secret name on a shared
machine is likelier to collide with something else's, and renaming it is a one-line change in two files if
that is ever preferred.

### Measured cost

**449 lines against the ~150 estimate (3.0x)** — `src` 128, tests 321. Well inside the 800 budget, so one
unit. The overrun is scope, not sprawl: the estimate covered task 8.3 alone, and this unit also closed 8.3a,
the refusal message, the resolution ordering and the release path. Test share 72%.

Cloud-only and local-only builds are both now first-class: `configured_resolver` returns a resolver when
*either* engine is configured, and only a build with neither can run nothing.

## Slice 8a-iii: Real Byte-Cap Validation (~300 lines)

Closes: `speech-transcription` Cloud Adapter Request-Size Handling (real cap). Slice 2a already implemented the
byte-cap-aware planning formula against a fake `max_chunk_bytes=25_000_000`; this unit supplies the real value
only. Depends on 8a-i/8a-ii.

- [x] 8.4 RED: within-limit test — a plan sized against the real 25MB cap never exceeds it on submission.
- [x] 8.5 GREEN: ~~`paid`-marked~~ assertion confirming the slice-2a planner logic already holds against the
      real capability value. **It did not hold.** See below.

### The `paid` marking was wrong, and dropping it is the point

8.5 asked for a `paid`-marked assertion, which assumed the real capability value could only be read from a
live adapter. It cannot only be read that way — `capabilities()` talks to nobody. The cloud adapter checks its
key locally at construction and sends nothing until `transcribe`, so the declared cap is free to ask for.

Marking these `paid` would have moved the project's entire byte-cap safety check *out of the run that gates
every commit*, to buy nothing. They run in the default suite instead, and the ffmpeg half runs `integration`
— also free, also default.

### What 8.4 was actually for: two numbers nobody had connected to anything

Slice 2a proved the byte-cap formula against a hand-typed `25_000_000` and `FLAC_BYTES_PER_SECOND = 16_000`,
the latter commented "sits near this rate". Both were reasonable and neither was measured or bound to
anything. So:

**The literal is now the declaration.** The planner tests read `max_chunk_bytes` off the real adapter. Before,
the literal in the test and the constant in the adapter were two independent facts that happened to agree —
the day one moved, the other kept passing while production planned chunks the engine would refuse.

**The bitrate is now measured, and it is three times the assumption.** `tests/integration/test_flac_bitrate.py`
encodes through `argv.py`'s own `_audio_encoding()` — not a copy of it — and measures. Measured on the real
extraction path (mp4 with AAC audio → `build_extract_argv`): **48,484 B/s**, at `sample_fmt=s32`,
`bits_per_raw_sample=24`.

The cause is worth naming: **normalization does not pin the FLAC sample format**, so it follows whatever the
source decodes to. AAC decodes to float and lands as 24-bit FLAC; a 16-bit source lands as 16-bit and measures
**15,426 B/s** for the same content. A factor of three, decided by the input.

Nothing is broken by that on its own — a higher bitrate simply shortens the stride, which is the formula
working. But it does correct a slice-2a claim: `test_realistic_flac_bitrate_is_not_constrained_by_the_cap`
asserts the 600 s duration target wins, and at its assumed 16 KB/s it does. At the rate the pipeline actually
produces for incompressible audio, the cap binds instead. Both are true; the difference is entirely the
bitrate, which is why it is now measured rather than believed.

### The defect: no chunk is one stride long, and the cap was sized as if it were

`_stride_for` computed the byte budget against the **stride**. But every chunk carries the overlap tail, and
the chunk that absorbs a short final one grows by nearly `min_chunk_s` instead — both appended *after* the
stride has been chosen. The difference was charged to the 0.9 headroom, and `plan_chunks` said so in a
comment: "which the byte cap's 0.9 headroom already covers at any realistic bitrate."

It covers it up to about **71 KB/s** — `(cap × 0.1) / (overlap + min_chunk_s)` — a limit stated nowhere and
enforced by nothing. Above it the guarantee simply fails. The RED found it at 100 KB/s: a plan whose largest
chunk came to **25.4 MB against a 25 MB cap**.

The fix is arithmetic rather than a bigger headroom: the stride now reserves `max(overlap_s, min_chunk_s)`
outright. `max`, not the sum — a chunk never pays for both, because one that absorbed a tail clamps to the end
of the track and carries no overlap past it, and reserving the sum would shorten every stride to buy a case
that cannot happen.

Today's margin was never breached in production — 48.5 KB/s sits below 71 KB/s — but by a factor of 1.47, on
a bitrate the pipeline does not pin. That is not a margin anyone chose.

Two consequences, both recorded rather than hidden:

- **`ChunkTooLarge`'s message was corrected.** It said "even for a one-second chunk", which stopped being true
  once the reserve became mandatory: at 1 MB/s a one-second chunk is 1 MB and fits comfortably. It now names
  the real floor — the shortest chunk a plan can produce is `appended_s` long before any stride is added.
- **Two slice-2a expectations moved.** `test_byte_cap_shortens_the_stride` 450 s → 420 s, and
  `test_merged_chunk_still_respects_the_byte_cap` retuned its track length so a tail under `min_chunk_s` still
  exists at the new stride. That second test was already aiming at this exact defect and passed only because
  50 KB/s happens to sit under the 71 KB/s limit.

### Left open deliberately: pinning the sample format

Adding `-sample_fmt s16` to `_audio_encoding()` would halve the worst-case byte rate and, as far as the ASR is
concerned, lose nothing — Whisper resamples to 16 kHz float, and 24-bit depth on 16 kHz speech carries no
information it uses. It also halves what every job stores on disk.

Not done here, because it changes the audio the engine sees, and that is a transcript-quality decision rather
than a byte-cap one. The system is correct without it now that the reserve is explicit. Recorded so the choice
is made deliberately rather than inherited from whatever ffmpeg picked.

### Measured cost

**331 lines against the ~300 estimate (1.1x)** — `src` 42, tests 289. Test share 87%, the highest in this
change, which is what a validation unit should look like: the code change is nine lines of arithmetic and the
value is in what proves it.

## Slice 8a-iv: Classification Declaration (Cloud) (~300 lines)

Closes: `speech-transcription` Non-Speech Segment Classification (cloud real-engine half). Depends on 8a-i.

- [x] 8.5a RED: `paid`-marked classification-declaration test — the cloud adapter declares its **real**
      `non_speech_classification` value for the chosen provider. A raw Whisper-API-style adapter exposing no
      VAD control MUST declare `UNSUPPORTED` and return `UNCERTAIN` segments; a provider with server-side VAD
      MAY declare `AVAILABLE`. Assert the declaration matches observed behavior — do not assume parity with
      the local adapter, and do not infer it from the adapter's diarization support.
- [x] 8.5b GREEN: implement the declared behavior for the chosen provider. **Landed in 8a-i**, unavoidably.

### 8.5b was already closed, and 8.5a's real subject turned out to be *why*

The GREEN half could not wait for this slice. The shared contract body asserts the **relationship** between
the declaration and the behaviour — an adapter declaring `UNSUPPORTED` must be shown to return `UNCERTAIN`
segments — so 8.1 could not have passed in 8a-i without both halves being present. Splitting them across
slices was never possible; the plan assumed a seam that the contract's own design forbids.

That leaves 8.5a with the part a mock genuinely cannot supply: not *that* the declaration is `UNSUPPORTED`,
but that it is the **right** declaration for this provider. Two `paid` tests, 9 in that module now:

- **Music never comes back marked as speech**, against a chord-over-pink-noise fixture — harmonically dense,
  speech-free, and the shape that makes a Whisper-family decoder invent. `speech_segments` selects the LLM
  window on that field and `without_music` drops on it, so this is the assertion that protects both.
- **The declaration still matches what the provider offers.** `UNSUPPORTED` is a claim about *OpenAI*, not
  about our code: it says this API exposes no voice-activity control, so the adapter has established nothing
  about whether it heard the preacher or the band. If that ever stops being true — a VAD parameter, a
  segment-level content class — the adapter should be classifying rather than blanket-marking. Pinning it
  makes flipping it a deliberate act with a paid test behind it, rather than something inherited from 8a-i's
  constraints.

### Two deliberate choices in how it asserts

**The provider's behaviour is recorded, not asserted.** A test demanding that the API hallucinate over a chord
would be pinning a provider defect, and would go red the day OpenAI fixed it — reporting an improvement as a
regression. What is asserted strictly is *our* answer: whatever comes back is `UNCERTAIN`.

**It does not subclass the contract case.** Inheriting `TestOpenAiWhisperCloudEngine` would re-run the entire
contract body against the new fixture and bill every one of those calls a second time, to re-prove what the
sine fixture already proved.

### Unverified, and that is stated rather than implied

Every assertion here is `paid`-marked and **has never been executed** — there is no `CLOUD_ASR_API_KEY` on
this machine. The ffmpeg fixture *was* verified independently (3.000 s, 16 kHz mono, 96 KB), so the test will
reach the API rather than dying on its input; what the API then says is unproven. This is the same standing
gap CLAUDE.md already records for real singing against the local engine, now on the cloud side too.

**106 lines**, one file. No `src` change — there was nothing left to implement.

---

## Slice 8b-i: Split-and-Retry (~450 lines)

Closes: `speech-transcription` Cloud Adapter Request-Size Handling (recovery half). Narrow recovery-path
addition to two already-shipped files (`plan_chunks.py`, `transcribe_job.py`), independently revertible without
touching the cloud adapter itself.

- [x] 8.6 RED: `ChunkTooLarge` split-and-retry test — an oversized actual chunk triggers a half-split
      re-slice instead of a failed job.
- [x] 8.7 GREEN: ~~`plan_chunks.py`/~~`transcribe_job.py` — catch `ChunkTooLarge`, split, re-slice, retry.

### `plan_chunks.py` was not touched, and must not be

8.7 named both files. Only `transcribe_job.py` changed, because **the split must not reach the plan**.
Chunk results are indexed against the *persisted* plan, and `_plan` already refuses to re-plan an existing job
for precisely this reason: "a plan that differed by one chunk would silently re-map every completed result
onto the wrong range." A recovery path that grew the plan by a chunk would corrupt every resume after it.

So the split happens **inside** one planned chunk. The halves are sliced and transcribed separately and their
segments come back as **one** `ChunkResult` at the original index. `pending_chunks` compares results to the
plan by index and sees exactly what it expected; resume never learns a split happened. That is also why a
split chunk is not re-run on restart — proven, because the property is easy to assert and easy to lose.

### Three ways to get this wrong, all silent

- **Times.** Each half answers in times local to *itself*. Concatenating them unchanged puts the second
  half's segments back at zero, on top of the first half's. The result stitches cleanly, reads as a whole
  sermon, and aims every clip cut from its back half at the wrong minute. `_merge` offsets by
  `half.start_s - planned.start_s`, and it exists for nothing else.
- **The seam.** No overlap is added between halves — a decision, not an omission. The stitcher dedupes by the
  **plan's** overlap and these halves are not in the plan, so an overlap here would duplicate text with
  nothing left to remove it. A word clipped once at one seam is the cheaper defect, and a correctly planned
  job never enters this path.
- **A failed half.** One bad half fails the whole chunk. Keeping the good half would store a result covering
  less audio than its planned range claims, and nothing downstream compares the two — the missing half would
  read as a pause, which is the same "hole that reads as a whole transcript" failure `_stitch` already guards
  against at the job level.

### Two properties that stop it being a worse bug than the one it fixes

**It never retries at the same size.** `ChunkTooLarge` is a measurement of those exact bytes, so a second
identical request is the retry loop's one guaranteed waste — and against a cloud engine, a billed one. It is
handled outside `_transcribe_chunk`'s attempt loop entirely, which is also why it never spends the
`max_attempts` budget that transient failures need.

**It terminates.** `DEFAULT_MAX_SPLIT_DEPTH = 3` bounds one chunk to at most eight pieces. Halving something
the engine will never accept is an infinite loop inside a job already measured in hours, and nothing
downstream would ever report why. Exhausting the depth produces a normal `FAILED` chunk result naming the
size and the duration it could not get under — chunk-level isolation still applies, so the other chunks are
kept and the operator learns which one and by how much.

Sub-slices are written to derived sibling paths (`0007-00.flac`, `0007-01.flac`, …) rather than to
`chunk_path(index)`. Both halves reusing the chunk file would have the second overwrite the first, and on the
real extractor the second slice would be reading a destination it is simultaneously writing. They stay in the
same directory because every path handed to the extractor is resolve-checked against the job directory before
a spawn.

### The fixture arithmetic caught the same off-by-an-overlap twice

The first draft of these tests assumed a 600 s target produces 600 s chunks. It does not: chunk 0 is
**[0, 605]**, because it carries the overlap tail. That is the identical mistake slice 8a-iii found inside the
byte cap itself — *no chunk is one stride long* — this time made by the test rather than by the code. Worth
recording because it is now the second time the overlap has been dropped from an estimate of a chunk's size,
which suggests it is genuinely easy to forget rather than a one-off slip.

### Measured cost

**622 lines against the ~450 estimate (1.4x)** — `src` 198, tests 424. Test share 68%. Inside the 800 budget,
so one unit. No new dependency, no port change, no domain change: the recovery path is entirely inside the
use case that already owned the loop, which is what makes it revertible on its own.

## Slice 8b-ii: In-Call-Timeout Construction Refactor (~150 lines)

Closes: no new spec scenario — pure refactor. Depends on 8b-i and slice 8a's adapter existing.

- [x] 8.8 REFACTOR: unify in-call-timeout construction between local/cloud resolver branches; suite green.
      **The branches had nothing to unify — but looking for it found a defect.**

### There is no per-branch timeout construction, and there never was

8.8 assumed each resolver branch builds its adapter with a timeout. Neither does.
`local_transcriber` takes a model size and a device; `cloud_transcriber` takes a key and a model name. The
budget does not travel through construction at all — it arrives per call on `TranscriptionRequest.timeout_s`,
which `transcribe_job` fills in. Nothing to extract, for the same reason as 4.20, 7.7 and 8.3a: the
abstraction the plan anticipated was avoided rather than built.

What the task pointed at was real anyway, one layer down.

### The defect: the operator's timeout reached the watchdog and stopped there

`settings.py` said of `chunk_timeout_s`: *"the same value is passed to adapters that can honour a timeout
in-call."* **It was not.** It reached `WatchdogConfig` and nowhere else. The worker is a separate process that
reads its own environment — model size, device, API key — and this was the one setting it never read, so
`transcribe_job` ran on its hardcoded `DEFAULT_CHUNK_TIMEOUT_S` whatever the operator had configured.

The consequence is specific. An operator setting six minutes got a watchdog that kills a stalled worker at
six minutes and a cloud adapter that went on waiting thirty — so the in-call budget slice 8a-i added
*precisely so the watchdog would stop being the only backstop* could never fire first. The external kill
still happens, and it is the blunter of the two: it takes down the whole process, loses the chunk in flight,
and leaves INTERRUPTED for somebody to resume.

Worth noting how it hid. Both enforcement paths worked, and the configured value was honoured by the one an
operator would think to test. Only the *interaction* was wrong, and nothing observes an interaction between
two processes.

### The unification that did exist: the variable's name

`chunk_timeout_s` is read under two spellings, because the derived name (`..._CHUNK_TIMEOUT_S`) is not the
documented one (`..._CHUNK_TIMEOUT_SECONDS`) — `settings.py` already carried an `AliasChoices` and a comment
saying an operator setting the documented variable and watching it do nothing is the worst of both.

Two *programs* now read that variable. A third spelling drifting into the worker would be a setting that
silently applies to one enforcement path and not the other — the same failure the alias was added to prevent,
one level up. So the names live once, in `CHUNK_TIMEOUT_ENV_NAMES`, consumed by both `AliasChoices` and the
worker, with a test asserting the two readers agree on a value. **That is the whole of what 8.8 asked for that
turned out to exist.**

The worker refuses a bad value rather than defaulting (`EXIT_UNUSABLE`, naming the variable, before the
record is touched). The web process already declines to boot on one via `gt=0`; silently substituting thirty
minutes in the worker would enforce a budget nobody asked for, in the one process where it actually applies.
Precedence matches `AliasChoices` — documented name wins — because two enforcement paths disagreeing about
which spelling wins would be worse than either default.

### Measured cost

**330 lines against the ~150 estimate (2.2x)** — `src` 76, tests 254. The estimate covered a refactor that did
not exist; what was delivered is a defect fix with the wiring test that proves it, which is a different and
larger thing. Test share 77%.

---

**Slice 8 is closed.** 8a-i, 8a-ii, 8a-iii, 8a-iv, 8b-i, 8b-ii — cloud adapter, registration, byte-cap
validation, classification evidence, split-and-retry, timeout wiring.

---

## Slice 9a-i: Local Diarization Capability Probe (~460 lines)

Closes: `speech-transcription` Contract Parity and Declared Divergence (diarization scenario, local capability
half). Flips the local adapter's declaration from `UNSUPPORTED`/`REQUIRES_SETUP` toward `AVAILABLE`. No new
domain dataclasses (`TranscriptSegment.speaker` already exists from slice 1).

- [x] 9.1 RED: `localmodel`-marked test — local adapter declares `AVAILABLE` when `pyannote.audio`/WhisperX
      is installed and the licence accepted, `REQUIRES_SETUP` otherwise.
- [x] 9.2 GREEN: extend `faster_whisper_adapter.capabilities()` to probe install state; add diarization
      sub-adapter. **The probe module landed; the pipeline sub-adapter belongs to 9a-ii.**

### `REQUIRES_SETUP` is the value this unit is actually about

The local adapter has declared `UNSUPPORTED` since slice 7a, which was true as a statement about the build and
false as a statement about the engine. `UNSUPPORTED` means *never* — it is what the cloud adapter declares,
permanently, because OpenAI's API returns no speaker labels and offers no way to ask for them. The local engine
can diarize; this machine simply is not set up for it. An operator told `unsupported` goes looking for a
different engine, and the only other engine in this system is the one that genuinely cannot.

Both values still refuse a speaker-mode job — `_validate_compatibility` admits only `AVAILABLE` — so nothing
about the dangerous failure changes. What changes is that the refusal now points at a package instead of at a
dead end.

### The probe is in its own module, and not for tidiness

`faster_whisper_adapter` imports the engine at module level, so anything living there can only be *read* on a
machine carrying the optional ASR extras. This is a question about which extras a machine carries — a test of
it that could only run on a machine already holding half a gigabyte of wheels would be a test of the machine.

`adapters/asr/local/diarization.py` imports nothing heavier than `ports.capabilities`, so its 14 tests run in
the **true** default suite on any checkout, extras or not. The adapter's own declaration is still asserted
`localmodel`, because reading `capabilities()` means constructing the adapter, which loads CTranslate2 weights.

### The gotcha the probe exists to contain

`importlib.util.find_spec("pyannote.audio")` **does not return `None`** when `pyannote` is absent. It raises
`ModuleNotFoundError`, because resolving a dotted name imports the parent package first. Written the obvious
way, this probe crashes `capabilities()` on precisely the machines it was written to describe — and
`capabilities()` is read on every planning pass. Measured here, not assumed: it is what the first call on this
machine actually did.

`find_spec` rather than a `try: import` for a second reason — importing `pyannote.audio` pulls in torch, which
is hundreds of megabytes and several seconds on a call whose whole purpose is to be cheap enough to make
before deciding anything. `ValueError` is caught alongside `ImportError`, for a module present in
`sys.modules` with no spec.

### A probe, not a proof — stated because this project already learned the difference

Slice 7c watched CTranslate2 load happily onto a device it could not compute on, which is why `_prove` now
decodes a second of silence in the constructor. Install state is the same kind of claim: an importable package
and a present credential say the setup is *plausible*, not that the pipeline will build.

The proof is deliberately **not** here. Building a pyannote pipeline downloads gated weights, and
`capabilities()` is read by callers that only wanted the byte cap. It belongs with the diarizing call in
**9a-ii**, where a job that actually asked for speakers pays for it once — the same shape as `_prove` sitting
with the decode rather than with the declaration.

### The licence half, and where the token lives

`pyannote.audio`'s models are gated: the package installs freely and the weights refuse to download until
someone has accepted the terms on their own account. So the probe requires both — a build with the code and no
credential can no more diarize than one with neither, and declaring `AVAILABLE` on the strength of an import
would admit a speaker-mode job that dies on its first chunk.

`HUGGING_FACE_TOKEN` is a **constructor argument**, never an environment read inside the adapter. Third time
this split has been applied (`LOCAL_DEVICE_ENV`, `CLOUD_ASR_API_KEY`, now this): the composition root reads the
variable, the adapter knows only its *name* so a refusal carries its own remedy. `worker.py` reads it through
the same `_configured` helper 8a-ii extracted.

### Left as it was, on purpose

`requirements-diarization.txt` is **still empty**. Pinning a version this machine has never installed is how a
requirements file breaks somebody's checkout; `requirements-local-asr.txt` got its `faster-whisper==1.2.1` from
a real install, and this one should get its pin the same way — in 9a-ii, from a machine that has actually built
the pipeline.

Which means the `AVAILABLE` branch is proven only as arithmetic: the decision function is tested both ways in
the default suite, but no machine here has ever seen the adapter declare it. The `REQUIRES_SETUP` branch is
this machine's live state and is verified end to end.

### Measured cost

**317 lines against the ~460 estimate (0.69x)** — `src` 127, tests 190. Under, and the reason is the same one
that kept it verifiable: the pipeline construction the estimate anticipated is 9a-ii's, and pulling the
decision out into a pure function made most of it testable without any of it.

## Slice 9a-ii: Diarizing Call + Speaker Labels (~460 lines)

Closes: `speech-transcription` Reject Speaker-Mode Jobs the Adapter Cannot Satisfy (positive path, local). The
rejection path itself was already proven in slice 6. Depends on 9a-i.

- [ ] 9.3 RED: diarizing-adapter-receives-multi-speaker-job test — returned segments include a speaker
      label per segment, namespaced `c{chunk_index:02d}/S{speaker:02d}`.
- [ ] 9.4 GREEN: implement the diarization call + namespaced label assignment.

### Blocked, and not on anything that can be written

`pyannote.audio`'s models are gated on Hugging Face: the weights do not download until a **human accepts the
terms on their own account**. That is not a dependency an implementer can install past — no token exists to
configure until someone has clicked through, and no amount of code substitutes for it.

Writing the pipeline blind was considered and rejected. 8a-iv already ships assertions that have never
executed, which is defensible for a handful of declarations checked against a documented API; a diarization
integration is a different proposition — several hundred lines against a library whose call shape, return type
and failure modes could not be run once. It would look finished and be unverified in every detail that
matters, which is the failure mode this project spends its docstrings warning about.

**9b-ii runs ahead of it instead**, which is legitimate: the seam is a use-case change with no dependency on
the local adapter at all. Slice 9a-i already flipped the declaration to `REQUIRES_SETUP`, so the honest
refusal an operator meets today is in place.

**To unblock**: accept the `pyannote/speaker-diarization-3.1` terms on a Hugging Face account, install
`pyannote.audio`, set `HUGGING_FACE_TOKEN`, then pin `requirements-diarization.txt` from that real install the
way `requirements-local-asr.txt` was pinned.

---

## Slice 9b-i: Cloud Diarization Declared Divergence (~350 lines)

Closes: `speech-transcription` Contract Parity and Declared Divergence (diarization scenario, cloud half).
Independently revertible from the local adapter's diarization support (slice 9a).

- [x] 9.5 RED: cloud diarization test (`paid`-marked) — asserts the declared divergence per provider
      (e.g. flips to `AVAILABLE`, or a Whisper-API-based adapter stays `UNSUPPORTED` and still refuses).
- [x] 9.6 GREEN: implement or explicitly document the divergence for the chosen cloud provider.

### Already closed by 8a-i, for the third time in this change

The shared contract body asserts each adapter against **its own declaration** —
`test_it_honours_its_own_diarization_declaration` requires `DiarizationUnsupported` from anything not
declaring `AVAILABLE`. The `paid`-marked cloud contract module runs that body, so 9.5 has been executing since
8a-i and 9.6 was implemented in the same commit.

Same shape as 8.5b and 7.7: the plan anticipated a seam the contract's design forbids. A cloud adapter cannot
be built at all without answering the diarization axis, because the contract tests the relationship rather
than the value.

**The divergence, stated once for the record**: OpenAI's transcription API returns no speaker labels and
exposes no way to request them, so the cloud adapter declares `UNSUPPORTED` — *never*, not "not yet". The
local adapter now declares `REQUIRES_SETUP` (9a-i), which is the point of having three values rather than a
boolean: the two engines are unavailable for genuinely different reasons, and only one is fixable by
installing something.

## Slice 9b-ii: `SpeakerResolver` Seam (~300 lines)

Closes: cross-chunk speaker identity (discovered-risk seam, see Open-Question Tracking). Introduces the
`SpeakerResolver` seam the design flagged as a discovered risk.

- [x] 9.7 RED: `SpeakerResolver` seam test — stitcher accepts a no-op default resolver passing namespaced
      labels through unchanged; a stub resolver substitutes without touching the stitching algorithm.
- [x] 9.8 GREEN: `usecases/stitch_transcript.py` — inject `SpeakerResolver` protocol, default no-op impl.
      **Answers the new cross-chunk speaker identity question later**.

### The problem the seam holds open

Diarization runs per chunk and has to — a three-hour sermon is not held in memory at once — so its labels are
namespaced, and `S01` in chunk 0 has no relationship to `S01` in chunk 1 beyond both being the second voice
their own chunk happened to notice. Across 87 chunks the same preacher collects 87 identities. A transcript
labelled that way **looks precise while saying nothing**, which is this project's recurring worst case rather
than a new one.

Nobody knows yet what should decide it. Voice embeddings are the obvious answer, they are not free, and no
measurement exists of whether the accuracy is worth the cost on this material. What *is* knowable now is where
the answer goes: the stitcher is the first and only place holding every chunk's labels at once.

### The shape is the decision

A resolver returns a **mapping between labels**, never segments. It may rename a speaker; it may not move a
boundary, drop a phrase or reorder anything. Overlap reconciliation took a slice of its own to get right, and
a seam that let a future speaker-identity experiment reach into it would put that at risk to answer an
unrelated question — two tests assert times and text are identical with and without a substituting resolver,
one of them over a genuinely contested window.

Three smaller decisions, each with a test:

- **Asked once, with every label the finished transcript carries.** Not per chunk — that could not answer a
  cross-chunk question by construction — and not per segment, because a resolver paying for voice embeddings
  should pay once.
- **Not asked at all when nothing is labelled.** Every single-speaker job produces `speaker=None` throughout,
  which is most jobs, and on the implementation everyone expects the cost of being asked is a model load.
- **Labels it omits pass through unchanged.** A partial answer is legitimate: a resolver confident about the
  preacher and unsure about a guest can say so instead of guessing to stay well-formed. An empty mapping is
  therefore identical to no resolver, so one that declines to decide degrades to today's behaviour.

`SpeakerResolver` lives in the use case rather than in `ports/`. The five ports are adapters this system
already knows it needs; this is a seam whose implementation nobody has designed, and promoting it to a sixth
port before one exists would commit to a boundary shape on no evidence.

### Measured cost

**345 lines against the ~300 estimate (1.15x)** — `src` 71, tests 274. Test share 79%. The default resolver
renames nothing, so every pre-existing stitcher test passes untouched: the seam is provably free until someone
fills it.

## Slice 9b-iii: Admission Coverage + Capability-Probing Refactor (~250 lines)

Closes: regression coverage for slice 6 admission now that engines can declare `AVAILABLE` diarization. Depends
on 9a and 9b-i.

- [x] 9.9 GREEN: extend slice-6's admission tests to also cover now-`AVAILABLE` engines admitting normally.
      **They already did — and asking why found the guard unwired.**
- [x] 9.10 REFACTOR: consolidate the two adapters' capability-probing pattern; suite green.

### 9.9's literal ask was already satisfied. Its intent was not.

Slice 6 already covers `AVAILABLE` admitting normally — `test_compatible_combination_admitted`, the route-level
`_client(storage, AVAILABLE)` case, and `_validate_compatibility(AVAILABLE, MULTI)` not raising. It was written
against a fake before any real engine could declare it, which is the right way round.

So the coverage question became: does that guard run at all? **It did not.**
`build_dependencies` never set `WebDependencies.capabilities`, so it defaulted to `None` and `admit_job`
skipped the guard on the one path an operator actually uses. Built in slice 6, thoroughly tested, never
connected — the same shape as 8b-ii's timeout, and the third defect in this change found by asking what
happens to a correct component at the composition root.

**What it cost.** An interview-mode job is admitted, queued, and given a worker. ffmpeg extracts the audio from
a three-hour recording. The chunk plan is written. TRANSCRIBING begins — and *then* the adapter's own
`_validate_compatibility` raises on the first chunk. The operator learns at the end of the expensive part what
was knowable before it started, and the extraction is thrown away. Moving that discovery to the front was
slice 6's entire purpose.

### Why it could not be wired, which is what 9.10 turned out to be about

The callable's type promised a whole `TranscriptionCapabilities`. Assembling one in the *web* process means
constructing an adapter, and constructing the local one loads CTranslate2 weights — inside an HTTP request, on
a process that may not have the ASR extras installed at all. The guard was unwireable as typed.

`admit_job` reads exactly one field of it. So the dependency is now `Callable[[EngineChoice],
DiarizationSupport]`, and the honest answer becomes cheap: both engines can state their diarization support
without being built. The cloud one from a module constant (`DIARIZATION`, hoisted out of `capabilities()`
for exactly this); the local one from `diarization.py`, which imports nothing heavier than `ports.capabilities`.
A test asserts the composition root answers `LOCAL` **in the default suite**, where no extras are installed —
which is the assertion that nothing is being constructed.

**That is the consolidation 9.10 asked for**, and it is a real one rather than the fifth negative result:
`declared_diarization` calls the adapters' own definitions instead of restating them. A composition root that
computed this its own way would be a second answer to "can this build diarize", and its failure mode is
admission accepting a job the adapter refuses three hours later — precisely the defect being closed. A test
pins the two together.

### Measured cost

**239 lines against the ~250 estimate (0.96x)** — `src` 86 across five files, tests 153 new plus 47 lines of
churn in slice 6's three admission modules, whose `_caps(...)` helpers collapsed to the value they always
wrapped.

---

## Slice 10a-i: Fake Port + MAP Windowing (~400 lines)

Closes: `script-generation` Map-Reduce Summarization (windowing half). **No new domain dataclasses** —
`GenerationResult`, `ClipCandidate`, `ScriptVariant`, and `TextGenerationPort` all already exist from slice 1
(confirmed by reading `src/onevoicecut/domain/generation.py` and `src/onevoicecut/ports/text_generation.py`).
Pure use-case logic over an already-built port — the cheapest remaining category of work.

- [x] 10.1 RED: fake `TextGenerationPort`-based test — `complete()` call shape only, no summary logic yet.
- [x] 10.2 GREEN: `tests/fakes/text_generation.py` — new fake conforming to the existing port.
- [x] 10.3 RED: `tests/unit/usecases/test_generate_artifacts_map.py` — a transcript exceeding
      `map_window_tokens` windows by estimated char/4 budget, 200-token overlap, rendered with segment ids.
- [x] 10.4 GREEN: `usecases/generate_artifacts.py` MAP phase.

### Coverage and progress, because every failure here reads perfectly

A dropped window is a passage of the sermon the model never saw. A duplicated one is a point made twice.
Neither leaves a mark in the artifact an operator would notice, so the assertions are about the two properties
that cannot be checked by reading the output: **every segment reaches at least one window**, and **every window
admits at least one segment the previous one did not**. The second is termination — a transcript longer than
the budget would otherwise window forever, inside a job already measured in hours.

`MapWindow` carries its ids alongside its text because 10a-iii rejects any id the model returns that the
window did not contain. A window whose manifest and text disagreed would either reject a valid citation or
admit an invented one, and a test asserts the two are the same set.

### Two places a token budget cannot be honoured exactly, for the same reason

Segments are indivisible, so a budget expressed in tokens meets inputs it cannot cut:

- **A segment larger than the whole window** gets a window of its own, over budget. It cannot be made to fit
  and it must not be dropped, so it is handed on and `ContextLengthExceeded` deals with it — which is what
  10a-iv's halving retry exists for.
- **An overlap budget smaller than one segment** still carries one. Carrying nothing would be a hard boundary,
  which is exactly what overlap exists to avoid: a thought split there is summarised twice as two
  half-thoughts with nothing left to reconcile them. Found by the RED, not reasoned about in advance — the
  fixture's segments cost 32 tokens against a 20-token overlap and no overlap was produced at all. Zero
  requested is still zero given, so hard boundaries remain available to a caller that wants them.

### The fake records before it fails, deliberately

A test asserting "it retried three times" reads `prompts`, and a fake that logged only successful calls could
not tell three attempts from one. `fail_times` exists so a retry test can assert *recovery* rather than
surrender: fail once, then answer, and check the caller came back.

### Discovered here, and it decides 10a-ii

`speech_segments` takes `SPEECH` only. Since 8a-i the cloud adapter declares `ClassificationSupport.UNSUPPORTED`
and emits `UNCERTAIN` unconditionally — correctly, because it has no voice-activity control and `SPEECH` is a
claim it has not earned.

**So every cloud transcript filters to nothing.** Measured, not argued: a 200-segment cloud transcript through
`speech_segments` yields 0 segments and `map_windows` yields 0 windows. Task 10.4b says "filter to
`kind == SPEECH` **before** windowing", which would make an empty summary the guaranteed outcome of every job
run on the cloud engine.

This is the open question `speech_segments`' own docstring parks for slice 10a, and CLAUDE.md records as
undecided — *"excluding risks an empty summary on a non-classifying engine; marking risks the model ignoring
the marker."* It is no longer a risk. The cloud adapter shipped, so one branch of it is now certain, and
**10a-ii cannot be implemented as written without deciding it.**

### Measured cost

**605 lines against the ~400 estimate (1.5x)** — `src` 164, tests 441 (including the 75-line fake). Test share
73%. No domain change and no port change: `GenerationResult`, `ClipCandidate`, `ScriptVariant` and
`TextGenerationPort` all shipped in slice 1, exactly as the slice header predicted.

## Slice 10a-ii: Speech-Only Windowing (~300 lines)

Closes: `script-generation` Map-Reduce Summarization (speech-only scenario). Depends on 10a-i.

- [x] 10.4a RED: speech-only-windowing test — a transcript mixing `SPEECH` and `MUSIC` segments produces MAP
      windows containing no `MUSIC` (or `UNCERTAIN`) content, so lyrics never reach the model as message text.
- [x] 10.4b GREEN: filter to `kind == SPEECH` **before** windowing, reusing the slice-1b helper so "speech only"
      keeps one definition.
- [x] 10.4c RED: music-heavy-transcript test — when most of a transcript is non-speech, the summary derives
      only from the remaining speech and the system does not substitute non-speech content to fill it.
- [x] 10.4d **NEW — the decision 10.4b forced**: refuse a job at admission when its engine declares
      `non_speech_classification=UNSUPPORTED`, rather than delivering an empty summary.

### The open question is closed, and the answer needed a second guard

`speech_segments`' docstring had parked it since slice 1b and CLAUDE.md recorded it as undecided: does MAP
windowing exclude `UNCERTAIN`, or mark it the way the `.txt` export does? **Excluded.** A model will not honour
an inline marker the way a reader does — hand it a marked chorus and it may summarise the worship set as the
preacher's argument, fluently and confidently, with nothing in the artifact saying so.

That answer had a consequence the cloud adapter turned from risk into certainty. It declares
`non_speech_classification=UNSUPPORTED` and marks every segment `UNCERTAIN` — correctly, because it has no
voice-activity control and `SPEECH` is a claim it has not earned. Measured rather than argued: a 200-segment
cloud transcript through `speech_segments` yields **0 segments**, and `map_windows` yields **0 windows**.

So excluding alone would have made *every cloud job* finish COMPLETED with a blank summary and nothing saying
why — which looks like success, and is the silent degradation this project refuses everywhere else. The
refusal moves to admission instead: `ClassificationUnsupported`, before an id is minted, naming the
declaration. Same shape as the diarization guard beside it, on the second and independent axis, which is
exactly what having two axes was for.

**Order between the two guards is a choice.** Speaker mode is something the operator asked for and can
withdraw; classification is a property of the engine they picked. A job failing both is told about the
retractable one first, because that is the cheaper fix to try.

**Recorded tension, deliberately not resolved here:** the refusal blocks the whole job, so a cloud transcript
— which is still useful, since `render_message_text` keeps `UNCERTAIN` marked — becomes unobtainable too. The
alternative is admitting the job and refusing only the script artifacts. That is a product decision about what
a job *is*, and the proposal's stated stopping point is the script artifact, so refusing the job matches it.
Reversible in one place if that reading changes.

### The filter never renumbers

Ids are resolved against the real `Transcript`, music included. A window numbering its own survivors 0,1,2
would point every citation at the wrong moment of the sermon — the exact failure ids exist to prevent — so
`_windows_over` works over `(index, segment)` pairs and `speech_windows` filters the pairs rather than the
segments. A test asserts a transcript of `[MUSIC, MUSIC, SPEECH]` yields the single id `2`, and another
asserts `speech_windows` and `map_windows` agree exactly on a transcript with nothing to filter — the filter
is the only difference between them, not a second windowing algorithm.

"Speech only" keeps one definition: `is_speech` was extracted in `domain/transcript.py` and both
`speech_segments` and the windowing use it.

### `DeclaredSupport`, and why the guard's type grew back

9b-iii narrowed the admission callable from whole capabilities to `DiarizationSupport`, because that was the
only field read and the wider type could not be answered without constructing an engine. A second field is
read now, so it returns `DeclaredSupport` — **exactly the two axes both adapters can state from constants and
a `find_spec`**. The principle is unchanged: depend on what is read, and only on what can be answered cheaply.
The engine id and the byte caps still need a constructed adapter, and still are not asked for.

`adapters/asr/local/diarization.py` became `declarations.py` in the same move: it now holds both axes, and the
name should say what it holds. Seven import sites, mechanical.

### Measured cost

**584 lines against the ~300 estimate (1.9x)** — the estimate covered 10.4a–c, and this unit also carried
10.4d, the module rename, and a second round of churn through slice 6's three admission modules. `src` 253
across 19 files, tests 331 new.

## Slice 10a-iii: Segment-ID Validation + REDUCE Fold (~400 lines)

Closes: `script-generation` Map-Reduce Summarization (REDUCE half). Depends on 10a-i.

- [x] 10.5 RED: segment-id-rejection test — a model response referencing an id absent from its window is
      rejected.
- [x] 10.6 GREEN: id-validation against the real `Transcript`.
- [x] 10.7 RED: REDUCE test — partial summaries fold sequentially into one final summary without a single
      call exceeding practical context.
- [x] 10.8 GREEN: REDUCE phase.

### Checked against the window, never against the transcript

The obvious implementation validates a cited id against the whole `Transcript`, and it is wrong. A model could
then cite a moment it was never shown — from a different window, or from the far end of the sermon — and the
citation would validate. Each response is checked against **the window that produced it**, which is why
`MapWindow` has carried its `segment_ids` since 10a-i.

**One bad id refuses the whole response**, rather than being dropped while the prose is kept. A model that
fabricated a reference may well have fabricated the sentence around it, and summary text is not checkable the
way an id is. Discarding the one piece of evidence and keeping the unverifiable part is the worse of the two
failures, and it is the shape of silent degradation this project refuses everywhere else.

Four malformed-answer cases are refused as `GenerationFailed` rather than escaping as something a caller
cannot catch: prose instead of JSON, a missing `summary`, non-integer ids (`"s0001"` is what a model returns
when it echoes the rendered form back), and an invented id. The refusal names the id, because an operator
debugging a refused job needs to know the model made up `s0099`.

### The fold is sequential, and refuses before it spends

Eighty-seven partials do not fit in a context window any more than the transcript did, so folding everything
in one call would re-create the problem windowing was invented to solve. They fold two at a time — running
summary plus the next partial — and the running summary stays bounded because the model is asked for at most
`max_output_tokens` each time.

A single partial is returned untouched: nothing to reconcile, and paying a model to rephrase one summary buys
nothing. An oversized fold raises **before** the call rather than after it — spending a billed request to be
told what the estimate already knew is the one avoidable cost here, and 10a-iv turns that refusal into a
halving retry.

### The prompts are the one Spanish thing in the module

`_MAP_INSTRUCTION` and `_FOLD_INSTRUCTION` are in Spanish because the source material is. Everything else
here — names, docstrings, the response shape — stays English, and the prompts are the single place the
material's language legitimately shows through.

### A test fixture was wrong, and the code was right

The first `test_one_call_per_window` scripted a reply citing id `0` and reused it across three windows. The
second window refused it, correctly: an id from another window is exactly what the validation exists to catch.
The fixture now cites nothing.

### Measured cost

**404 lines against the ~400 estimate (1.01x)** — `src` 147, tests 257. Test share 64%.

## Slice 10a-iv: Context-Length Retry + Token-Estimation Refactor (~300 lines)

Closes: `script-generation` Map-Reduce Summarization (recovery half). Depends on 10a-iii.

- [x] 10.9 RED: `ContextLengthExceeded` retry test — window halves and retries.
- [x] 10.10 GREEN: implement halving retry.
- [x] 10.11 REFACTOR: extract token-estimation helper; suite green. **Already extracted in 10a-i.**

### The same recovery as 8b-i, one layer up

`chars/4` is deliberately crude — it is what keeps a provider-specific tokenizer out of the core — and the
price is that it is sometimes wrong in the expensive direction. The provider says so by raising
`ContextLengthExceeded`, and the answer is the shape slice 8b-i already built for oversized audio chunks:
halve, retry each half, bound the recursion, lose nothing.

The two properties that mattered there matter here for the same reasons. **Coverage**: every id in the
original window must still reach the model in one of the halves, because a dropped half is a passage of the
sermon nobody summarised and the summary reads exactly as well without it. **Termination**: a window of one
segment cannot be halved into anything, so it fails loudly rather than recursing forever, and the refusal
names the segment — an operator's only lever is the transcript, and knowing which segment is the immovable one
is the difference between acting and guessing.

**One thing differs, and it is a difference from 8b-i rather than from windowing.** A split audio chunk had to
come back as *one* `ChunkResult`, because chunk results are indexed against the persisted plan. A split window
comes back as **two partials**, and that is fine: REDUCE folds however many arrive, which is the mechanism
that existed for this all along. Nothing downstream counts partials.

**The halves do not overlap**, which is the opposite of what windowing does and for a reason worth stating.
Windowing overlaps to protect a thought split across a boundary it is creating. Here the boundary already
exists — re-sending shared segments would pay twice for text the model has seen and return two partials that
repeat each other.

`_halve` splits at a segment boundary by splitting the rendered lines, which stay aligned with `segment_ids`
because a window is rendered one line per segment. That is what lets a half be rebuilt without the transcript
that produced it, and it is now named: `SEGMENT_SEPARATOR`.

### 10.11 was closed by 10a-i

`estimate_tokens` has been the single definition since the MAP windowing landed — `CHARS_PER_TOKEN` appears
exactly twice in the module, as the constant and as its one use, and windowing, the fold budget and this
retry path all call the same function. A test pins that count so a second inline `/4` cannot creep back in.

Fifth task in this change to be already-satisfied when reached (7.7, 8.5b, 8.8, 9.5/9.6, now 10.11). Four of
those were seams the design forbids; this one is simply a refactor that happened at the right time on its own.

### Measured cost

**282 lines against the ~300 estimate (0.94x)** — `src` 76, tests 206. **Sub-slice 10a is closed**: fake port,
MAP windowing, speech-only filtering, the admission guard it forced, id validation, the REDUCE fold, and the
halving retry.

---

## Slice 10b-i: Clip Candidate Ranking (~300 lines)

Closes: `script-generation` Clip Candidate Output. Builds directly on 10a's MAP/REDUCE infrastructure.

- [x] 10.12 RED: clip-candidate test — candidate carries `start_s`/`end_s` mapping into the source
      transcript plus a short script.
- [x] 10.13 GREEN: rank-by-score candidate selection, top `max_clip_candidates`.

### The model proposes; the transcript decides where

A moment arrives as segment ids plus a hook, a quote, a reason and a score. **The times are never taken from
the model.** They are read off the segments those ids resolve to — start of the earliest, end of the latest —
so a model citing 1 and 4 gets the stretch rather than two fragments that jump.

`Moment` has **no timestamp field at all**, and that absence is the design. Offering one would invite exactly
the fabrication the id scheme exists to prevent, and a test feeds a response carrying `start_s: 999.0` to
prove it is ignored rather than merely unused.

### The flat `segment_ids` was always a placeholder

10a-iii parsed `{"summary": str, "segment_ids": [int]}`, which was the id-validation half of a shape design.md
had described in full: *"partial summary text plus candidate moments referenced by segment id"*. This slice is
where the moments take their structure, so the flat list became `moments`, each carrying its own ids.

Every id rule 10a-iii established is unchanged — checked against the producing window, one invented id refuses
the whole response, non-integer ids refused rather than coerced. They live one level in now, and the churn is
29 lines in that slice's fixtures. Planned evolution rather than a correction: the two slices were always
going to meet here.

Three new refusals, all about being *comparable* rather than merely well-formed:

- **A moment citing nothing.** A clip without a time is not a clip, and dropping it silently would lose a
  moment the model may have thought was the best in the sermon.
- **A score outside 0..1.** Ranking is comparison, so the scale has to mean the same thing for every moment.
  One scored 87 alongside one scored 0.9 tops every list for no reason.
- **A missing hook, quote or rationale.** These reach the operator; a candidate with a blank one is a row they
  cannot act on.

### Determinism is the point of the tiebreak, not the tiebreak

Ties break on position, earliest first. Which rule wins matters less than that one exists: two runs over the
same transcript disagreeing about the top five would be two runs an operator cannot reason about, and nothing
in the artifact would say which they were reading.

`variants` comes back empty. One `complete()` call per (candidate, target) pair is 10b-iii's work, and an
empty tuple says "none yet" without pretending otherwise.

### Measured cost

**370 lines against the ~300 estimate (1.23x)** — `src` 144, tests 206 new plus 29 lines of fixture churn in
10a-iii. No domain change: `ClipCandidate` shipped in slice 1 with exactly the fields this fills.

## Slice 10b-ii: Musical-Range Eligibility (~250 lines)

Closes: `script-generation` Clip Candidate Output (musical-range scenario). Depends on 10b-i.

- [ ] 10.13a RED: musical-range-eligible test — a candidate whose time range covers `MUSIC` segments is NOT
      rejected on that basis; its timestamps resolve like any other candidate. Excluding music from the
      *message* must not leak into excluding it from *clips* — the singer's moment is often the best footage.
- [ ] 10.13b GREEN: confirm candidate resolution is `kind`-agnostic. **Leaves Q9 open**: candidates over
      non-speech ranges are permitted here; whether ranking should additionally *favor* them is a prompt/score
      change confined to this slice.

## Slice 10b-iii: N Script Variants (~350 lines)

Closes: `script-generation` N Script Variants Per Clip Candidate. Depends on 10b-i.

- [ ] 10.14 RED: multiple-variants test — a candidate carries `variants: tuple[ScriptVariant, ...]`
      without a schema change when count > 1.
- [ ] 10.15 GREEN: one `complete()` call per `(candidate, target)` pair, `target` sourced from
      `settings.script_targets`.
- [ ] 10.16 GREEN: ship `settings.script_targets` defaulting to `["generic"]`. **Answers Q3 later**.

## Slice 10b-iv: Scope-Boundary Assertion + Prompt Refactor (~250 lines)

Closes: `script-generation` Scope Boundary — No Rendering. Depends on 10a and 10b-iii.

- [ ] 10.17 RED: scope-boundary test — generation output is summary + candidates + variants only, no
      video file produced.
- [ ] 10.18 GREEN: assert `GenerationResult` shape excludes any media artifact.
- [ ] 10.19 REFACTOR: extract the prompt-template construction shared by MAP/REDUCE/variant calls; full
      default suite green end to end.

---

## Rev-4 Review Workload Forecast — Slices 11–13 (appended)

> Phase: `sdd-tasks` (rev 4 delta) · Inputs: `design.md` rev-4 section, `specs/subject-tracking/spec.md`
> (13 requirements), `specs/clip-rendering/spec.md` (10 requirements), the appended `transcript-artifacts`
> requirements (Word-Level Timing, Word-Level Timing Is Consistent With Overlap Stitching), the appended
> `audio-extraction` requirement (Media Probe Reports Frame Dimensions). **Nothing above this line is
> re-opened** — slices 1 through 10b stay exactly as checked/unchecked above; this section only appends.

`sdd-design` estimated slice 11 ~1,600 lines, slice 12 ~1,600–2,000, slice 13 ~2,800–3,500 — already
calibrated against the measured 4.0x multiplier, not nominal pre-overrun figures — and recommended a
seven-way split (11a/11b, 12a/12b, 13a/13b/13c). At an 800-line budget, seven units over 7,000–9,000 lines
averages ~1,000–1,285 each: over budget by construction. Every design-level unit is split further here so
that **no work unit exceeds 800 lines**, using the same two measured facts that drove every split since
slice 1b: overrun is 3.2x–5.1x with no slice under 3.2x, and the excess is test code every time (61–81%
measured, never the 56% originally assumed).

| Field | Value |
|-------|-------|
| Slices 1–10b (existing) | 40 work units, PR 1 → PR 40 (rev-5 re-baseline of slices 7a–10b; unchanged by this appendix) |
| Slices 11–13 (new) | **18 work units**, PR 41 → PR 58 (shifted from PR 24–41 by the rev-5 re-baseline above) |
| Estimated changed lines, slices 11–13 | ~9,000 (slice 11 ~2,025 · slice 12 ~2,075 · slice 13 ~4,900) |
| Per-unit 800-line budget risk | **Low** — every one of the 18 new work units is individually estimated at 300–650 lines, with margin |
| Aggregate 800-line budget risk | **High** by construction — this is why the appendix stays split into 18 work units |
| Chained PRs recommended | Yes |
| Suggested split | **18 work units**, PR 41 (slice 11a) → PR 58 (slice 13c-ii) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
800-line budget risk: Low
```

**Grand total across the whole change**: 58 work units, PR 1 → PR 58. Per-file forecasts and Suggested
Work Units tables for each of the three new slices live in `slice-11-tasks.md`, `slice-12-tasks.md`, and
`slice-13-tasks.md`, matching the shape `slice-6-tasks.md` established. The master table below is the
single row-per-unit index across all 18 new units; full RED/GREEN task detail follows in the per-slice
sections after it. PR numbers below reflect the rev-5 re-baseline (shifted by +17 from the original
PR 24–41, once slices 7a–10b were re-split into 25 work units instead of 8).

### Suggested Work Units (Slices 11–13, master index)

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|-----------------------|------------------|--------------------|
| 11a | `MediaProbe.frame`: `FrameSize` + two ffprobe guards + `FrameGeometryUnavailable` | PR 41 | `pytest tests/unit/domain/test_media.py tests/unit/adapters/ffmpeg/test_probe_frame.py -m "not paid and not localmodel"` | `pytest -m integration` — real ffmpeg fixture | `domain/media.py`, `adapters/ffmpeg/extractor.py` (`probe()` frame parsing) |
| 11b-i | `WordTiming` domain + capability + fake + contract invariant + `AdmitJob` warning | PR 42 | `pytest tests/unit/domain/test_transcript.py tests/unit/ports/test_capabilities.py tests/unit/usecases/test_admit_job.py tests/contract -m "not paid and not localmodel"` | N/A — fakes only | `domain/transcript.py`, `ports/capabilities.py`, `tests/fakes/transcription.py`, `usecases/admit_job.py` |
| 11b-ii | Stitcher word-timing lockstep | PR 43 | `pytest tests/unit/usecases/test_stitch_transcript.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/stitch_transcript.py` |
| 11b-iii | Storage codec backward-compatible decode | PR 44 | `pytest tests/unit/adapters/storage/test_filesystem_transcript_storage.py -m "not paid and not localmodel"` | `pytest -m integration` | `adapters/storage/serialization.py` |
| 12a-i | `domain/framing.py` entities + `__post_init__` invariant + `crop_size_for` | PR 45 | `pytest tests/unit/domain/test_framing.py -m "not paid and not localmodel"` | N/A — pure domain types | `domain/framing.py` |
| 12a-ii | `SubjectTrackerPort` + `DetectionSupport` + fake detector | PR 46 | `pytest tests/unit/ports/test_capabilities.py tests/unit/ports/test_subject_tracker.py -m "not paid and not localmodel"` | N/A — fake detector only | `ports/subject_tracker.py`, `ports/capabilities.py`, `tests/fakes/subject_tracker.py` |
| 12b-i | Trajectory stages 2–4: centres, smoothing, dead-zone | PR 47 | `pytest tests/unit/usecases/test_plan_trajectory.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/plan_trajectory.py` |
| 12b-ii | Trajectory stages 5–6 + confidence: clamp, fill, provenance, `LOW_CONFIDENCE` | PR 48 | `pytest tests/unit/usecases/test_plan_trajectory.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/plan_trajectory.py` |
| 13a-i | `VideoRenderPort` + `domain/rendering.py` + `ClipId` + `quality_of` + structural test | PR 49 | `pytest tests/unit/domain/test_ids.py tests/unit/domain/test_rendering.py tests/unit/ports/test_video_render.py tests/unit/domain/test_framing.py -m "not paid and not localmodel"` | N/A — pure types + arithmetic | `domain/rendering.py`, `domain/ids.py`, `ports/video_render.py`, `domain/framing.py` |
| 13a-ii | ASS subtitle escaping + cue building | PR 50 | `pytest tests/unit/adapters/ffmpeg/test_subtitles.py tests/unit/usecases/test_build_subtitle_cues.py -m "not paid and not localmodel"` | N/A — pure, no ffmpeg | `adapters/ffmpeg/subtitles.py`, `usecases/build_subtitle_cues.py` |
| 13a-iii | Filter-graph composition + `sendcmd` densification | PR 51 | `pytest tests/unit/adapters/ffmpeg/test_argv_composition.py tests/unit/adapters/ffmpeg/test_sendcmd.py -m "not paid and not localmodel"` | N/A — pure composition | `adapters/ffmpeg/argv.py`, `adapters/ffmpeg/sendcmd.py` |
| 13b-i | Real `VideoRenderPort` adapter + `render_clip` pre-spawn guards | PR 52 | `pytest tests/unit/adapters/ffmpeg/test_video_render.py tests/unit/usecases/test_render_clip.py -m "not paid and not localmodel"` | N/A — injected fake runner | `adapters/ffmpeg/video_render.py`, `usecases/render_clip.py` |
| 13b-ii | `ClipExport` storage (two new port methods) | PR 53 | `pytest tests/unit/ports/test_transcript_storage.py tests/unit/adapters/storage/test_filesystem_transcript_storage.py -m "not paid and not localmodel"` | `pytest -m integration` | `ports/transcript_storage.py`, `adapters/storage/filesystem_transcript_storage.py`, `tests/fakes/transcript_storage.py` |
| 13b-iii | `render_worker` entrypoint + refusal branches + low-confidence propagation | PR 54 | `pytest tests/unit/runtime/test_render_worker.py -m "not paid and not localmodel"` | `python -m onevoicecut.runtime.render_worker --job-id <fake-job> --clip-id <fake-clip>` against fakes | `runtime/render_worker.py` |
| 13b-iv | HTTP clip routes | PR 55 | `pytest tests/unit/adapters/web/test_clip_routes.py -m "not paid and not localmodel"` | Real HTTP client, fake render worker spawn | `adapters/web/routers/jobs.py`, `adapters/web/schemas.py` |
| 13b-v | Real ffmpeg render integration | PR 56 | `pytest tests/unit -m "not paid and not localmodel"` | `pytest -m integration` — real ffmpeg render of a tiny fixture | `tests/integration/test_render_clip.py` |
| 13c-i | Real vision-backed `SubjectTrackerPort` adapter | PR 57 | `pytest tests/unit -m "not paid and not localmodel"` | `pytest -m localmodel` — real weights | `adapters/vision/*_tracker_adapter.py` |
| 13c-ii | Real adapter contract test | PR 58 | `pytest tests/unit -m "not paid and not localmodel"` | `pytest -m localmodel` | `tests/contract/test_subject_tracker_contract.py` |

### Ordering (extends the rev-4 design table with sub-unit dependencies)

Every edge below is a **compile-time** dependency: the later unit names a type, function or setting the
earlier one creates. Independence is claimed only where no such name is shared. Every edge points backwards
in PR number, so the contiguous PR 41 → PR 58 sequence is unchanged.

| Unit | PR | Requires | Independent of |
| --- | --- | --- | --- |
| `11a` | 41 | — | the whole 11b track (zero shared files) |
| `11b-i` | 42 | — | `11a` |
| `11b-ii` | 43 | `11b-i` (`words`, `WordTimingSupport`) | `11a`, `11b-iii` |
| `11b-iii` | 44 | `11b-i` (`words`, `WordTimingSupport`) | `11a`, `11b-ii` |
| `12a-i` | 45 | `11a` (`FrameSize`) | the 11b track |
| `12a-ii` | 46 | `12a-i` (**`TimeSpan`**, which appears in `SubjectTrackerPort.detect`'s signature) | the 11b track |
| `12b-i` | 47 | `12a-i`, `12a-ii` | — |
| `12b-ii` | 48 | `12b-i` | — |
| `13a-i` | 49 | `12a-i` (`CropRect` for `quality_of`, `TrackingConfidence` on `RenderedClip`) | `11b` track |
| `13a-ii` | 50 | `13a-i` (`SubtitleCue`, `SubtitleTimingSource`, `CaptionCoverage`), **`11b-i`** (`TranscriptSegment.words`) | `13a-iii` |
| `13a-iii` | 51 | `13a-i` (`ClipId`, `OutputSpec`), `12a-i` (`CropTrajectory`) | `13a-ii` |
| `13b-i` | 52 | `13a-i`, `13a-ii`, `13a-iii` | — |
| `13b-ii` | 53 | **`13a-i`** (`ClipExport`, `ClipState` are the values it round-trips) | `13a-ii`, `13a-iii`, `13b-i` |
| `13b-iii` | 54 | `11a` (`probe.frame`, `FrameGeometryUnavailable` — `13b.20`), `12a-ii` (`SubjectTrackerPort.detect`, `DetectionSupport.AVAILABLE`, `TrackingUnavailable` — `13b.18`, `13b.22`), **`12b-i`** (`build_trajectory` itself — `13b.18`'s happy path calls it), **`12b-ii`** (`TrackingConfidence.LOW_CONFIDENCE`, which only exists once stage 6 assigns origins — `13b.24`, `13b.25`), `13a-i`, `13a-ii`, `13b-i`, `13b-ii` | — |
| `13b-iv` | 55 | `13b-ii`, `13b-iii` | — |
| `13b-v` | 56 | `13b-i`, `13b-iii` | — |
| `13c-i` | 57 | `12a-ii` (the port), **`13b-i`** (`max_clip_seconds`, introduced with `render_clip`'s guards and used by `13c.3`'s span refusal) | `13b-ii` … `13b-v` |
| `13c-ii` | 58 | `13c-i` | `13b-ii` … `13b-v` — it inherits 13c-i's edge to `13b-i`, so it is *not* independent of the whole 13b track |

Three independences the earlier revision declared are **false and have been corrected above**: `12a-ii` is
not independent of `12a-i`, `13b-ii` is not independent of `13a-i`, and `13c-i` is not independent of the
whole 13b track. Two prerequisites were missing entirely. `13a-ii` needs `11b-i`, which is what
`design.md`'s Rev-4 Slice Ordering table already said when it recorded 11b as blocking *"the subtitle half
of 13a"*. And `13b-iii` needs **the whole 12b track plus `11a` and `12a-ii`** — a revision of this table
named no 12b unit as a prerequisite of anything, which made the trajectory pipeline, the entire point of
slice 12, read as a dead end in a table that declares itself exhaustive. `13b-iii` is the single consumer
of `build_trajectory`; every other unit consumes only the `CropTrajectory` *type*, which is `12a-i`.

---

## Slice 11a: `MediaProbe.frame` — Frame Dimensions (~525 lines)

Closes: `audio-extraction` Media Probe Reports Frame Dimensions (all 3 scenarios). Small and independent —
no storage change, no codec change, no migration, because the renderer re-probes rather than persisting
`MediaProbe`. Unblocks slice 12a-i.

- [ ] 11a.1 RED: `tests/unit/domain/test_media.py` — `FrameSize(width, height)` frozen; `MediaProbe.frame`
      defaults to `None`; construction with a `FrameSize` round-trips.
- [ ] 11a.2 GREEN: `domain/media.py` — add `FrameSize`; `MediaProbe.frame: FrameSize | None = None`.
- [ ] 11a.3 RED: `tests/unit/domain/test_errors.py` — `FrameGeometryUnavailable` derives from `DomainError`.
- [ ] 11a.4 GREEN: `domain/errors.py` — add `FrameGeometryUnavailable`.
- [ ] 11a.5 RED: `tests/unit/adapters/ffmpeg/test_probe_frame.py` — an ffprobe JSON fixture with a normal
      video stream decodes `MediaProbe.frame` matching width/height.
- [ ] 11a.6 GREEN: `adapters/ffmpeg/extractor.py` — extend `probe()` to read the selected video stream's
      `width`/`height`.
- [ ] 11a.7 RED: attached-cover-art fixture (`disposition.attached_pic == 1`) — probe returns `frame=None`,
      not the artwork's square dimensions.
- [ ] 11a.8 GREEN: skip streams with `disposition.attached_pic == 1` before selecting the video stream.
- [ ] 11a.9 RED: rotation fixture — a stream with `side_data_list` rotation of ±90° reports `FrameSize`
      with width/height swapped (display geometry, not coded geometry).
- [ ] 11a.10 GREEN: read rotation from `side_data_list`; swap width/height when it is ±90°.
- [ ] 11a.11 RED: no-video-stream fixture (audio-only source) — `MediaProbe.frame is None`.
- [ ] 11a.12 GREEN: confirm the guard chain falls through to `None` when no eligible video stream survives.
- [ ] 11a.13 RED: `integration`-marked test against a real ffmpeg-synthesized fixture (`-f lavfi`,
      matching the 3a precedent) — confirms both guards against the real binary's JSON shape, not only a
      hand-written fixture; skips when ffmpeg is absent.
- [ ] 11a.14 GREEN: fix any gap 11a.13 exposes between the hand-written fixtures and real ffprobe output.
- [ ] 11a.15 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 11b-i: `WordTiming` Domain + Capability + `AdmitJob` Warning (~625 lines)

Closes: `transcript-artifacts` Word-Level Timing (adapter-carries-timing, non-supporting-never-fabricates
scenarios); `speech-transcription` Capability Declaration (word-timing axis). First of three 11b units —
gates 11b-ii and 11b-iii.

- [ ] 11b.1 RED: `tests/unit/domain/test_transcript.py` — `WordTiming(start_s, end_s, text)` frozen;
      `TranscriptSegment.words` defaults to `()`; all 15 existing construction sites still compile
      unchanged.
- [ ] 11b.2 GREEN: `domain/transcript.py` — add `WordTiming`; `TranscriptSegment.words: tuple[WordTiming,
      ...] = ()`.
- [ ] 11b.3 RED: `tests/unit/ports/test_capabilities.py` — `WordTimingSupport` has exactly
      `{unsupported, available}`; `TranscriptionCapabilities.word_timing` is required, no default.
- [ ] 11b.4 GREEN: `ports/capabilities.py` — add `WordTimingSupport(StrEnum)` + the capability field;
      update every existing capability construction site (fakes + tests) to supply it.
- [ ] 11b.5 RED: non-supporting-fake test — a fake `TranscriptionPort` declaring `word_timing=UNSUPPORTED`
      always returns `words=()`, never a fabricated entry.
- [ ] 11b.6 GREEN: `tests/fakes/transcription.py` — word-timing-aware fake, mirroring the 1b
      classification-fake shape; script-driven word fixtures for the supporting fake.
- [ ] 11b.7 RED: supporting-fake test — a fake declaring `word_timing=AVAILABLE` returns one `WordTiming`
      per word, each with its own `start_s`/`end_s`, satisfying `"".join(w.text) == segment.text`.
- [ ] 11b.8 GREEN: implement the supporting fake path.
- [ ] 11b.9 RED: `tests/contract/` — add the word-timing invariant assertion, parametrized over every
      registered adapter (fakes now; real adapters inherit it once they ship word timing).
- [ ] 11b.10 GREEN: wire the assertion into the shared contract body.
- [ ] 11b.11 RED: `tests/unit/usecases/test_admit_job.py` — admitting a job against an engine whose
      capabilities declare `word_timing=UNSUPPORTED` produces a warning (not a rejection) naming the
      missing capability.
- [ ] 11b.12 GREEN: `usecases/admit_job.py` — surface the warning alongside the existing diarization
      compatibility check, without blocking admission.
- [ ] 11b.13 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 11b-ii: Stitcher Word-Timing Lockstep (~475 lines)

Closes: `transcript-artifacts` Word-Level Timing Is Consistent With Overlap Stitching. Depends on 11b-i.

- [ ] 11b.14 RED: `tests/unit/usecases/test_stitch_transcript.py` — a boundary word carrying word-level
      timing on both sides of the cut appears exactly once in the stitched output, with its original
      timing.
- [ ] 11b.15 GREEN: `usecases/stitch_transcript.py` — `_shift` carries `words` through the existing
      time-shift; `_split_words` partitions by word start, deriving segment `start_s`/`end_s`/`text` from
      surviving words.
- [ ] 11b.16 RED: orphaned-entry test — no `WordTiming` entry survives for a word whose text was dropped
      as a duplicate.
- [ ] 11b.17 GREEN: wire `_split_words` into `_clip_before`/`_clip_after`, gated on `segment.words` being
      non-empty.
- [ ] 11b.18 RED: empty-survivor-set test — a straddling segment whose every word lands on the discarded
      side drops the segment entirely.
- [ ] 11b.19 GREEN: confirm the existing drop-when-empty branch now fires for a genuinely empty result.
- [ ] 11b.20 RED: regression test — a word-less transcript (`words=()` throughout) stitches
      byte-identically to the pre-retrofit shipped behavior.
- [ ] 11b.21 GREEN: confirm the empty-words branch is a no-op change from today's time-truncation path.
- [ ] 11b.22 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 11b-iii: Storage Codec Backward-Compatible Decode (~400 lines)

Closes: `transcript-artifacts` Word-Level Timing (storage round-trip scenario). Depends on 11b-i.

- [ ] 11b.23 RED: `tests/unit/adapters/storage/test_filesystem_transcript_storage.py` — a pre-slice-11
      fixture payload with no `"words"` key decodes with `segment.words == ()`.
- [ ] 11b.24 GREEN: `adapters/storage/serialization.py` — `_word_timings()` helper; absent key → `()`.
- [ ] 11b.25 RED: malformed-payload tests — `"words": "hello"` and `"words": [{"start_s": true, ...}]`
      both raise `CorruptedRecord`.
- [ ] 11b.26 GREEN: `_objects`/`_number`/`_text` validation applied to each word entry.
- [ ] 11b.27 RED: round-trip test — a segment persisted with a non-empty `words` tuple is retrieved with
      the same tuple; a segment persisted with `()` is retrieved with `()`.
- [ ] 11b.28 GREEN: confirm the encoder needs no change (`asdict` already recurses); wire `_word_timings()`
      into `_segment()`.
- [ ] 11b.29 REFACTOR: suite green, `mypy src tests` clean; confirm no shipped `results/*.json` fixture in
      the test suite regresses.

---

## Slice 12a-i: `domain/framing.py` Entities + Trajectory Invariant (~450 lines)

Closes: `subject-tracking` CropTrajectory Domain Object. Depends on slice 11a's `FrameSize`. Gates 12a-ii
(which needs `TimeSpan`) and, with it, 12b-i.

- [ ] 12a.1 RED: `tests/unit/domain/test_framing.py` — construct `TimeSpan`, `CropRect`, `CropKeyframe`
      (timestamp + rect + `KeyframeOrigin`), `CropTrajectory`, `TrackingConfidence`, `TrajectoryPolicy`;
      all frozen, `FrozenInstanceError` on mutation.
- [ ] 12a.2 GREEN: `domain/framing.py` — the six entity types + `KeyframeOrigin(StrEnum)` (`TRACKED`,
      `INTERPOLATED`, `FALLBACK_CENTER`).
- [ ] 12a.3 RED: `CropTrajectory.__post_init__` invariant test — a trajectory whose keyframes carry
      mismatched rect `width`/`height` raises `ValueError`.
- [ ] 12a.4 GREEN: implement the invariant check in `__post_init__` — the domain's first `__post_init__`
      invariant.
- [ ] 12a.5 RED: keyframe-inspection test — a keyframe exposes its timestamp, rect, and exactly one origin.
- [ ] 12a.6 GREEN: confirm the constructed type already satisfies this.
- [ ] 12a.8 RED: `crop_size_for` pinned test — 3840×2160 → `(1214, 2160)` and 1920×1080 → `(606, 1080)`,
      the two authoritative numbers; a source narrower than 9:16 swaps the derivation axis; and the
      postcondition `crop_w <= frame.width and crop_h <= frame.height`, both even and **non-negative**,
      holds over odd frame widths and heights too — the property stage 5's clamp depends on. The property
      asserts non-negativity, **not** positivity: `even(v) == 0` for `v < 2`, so `FrameSize(1920, 1)`
      yields `(0, 0)`, which is correct output for a degenerate frame and is refused at the render-worker
      boundary by `13b.20`, never repaired here.
- [ ] 12a.7 GREEN: `domain/framing.py` — `even(value) = 2 * floor(value / 2)` (round **down**, no tie case)
      and `crop_size_for(frame, policy)` module function (pipeline stage 1), with no clamping and no
      re-evening step.
- [ ] 12a.9 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 12a-ii: `SubjectTrackerPort` + Fake Detector (~525 lines)

Closes: `subject-tracking` SubjectTrackerPort Contract, Capability Declaration, A Miss Is Reported Never
Guessed. Depends on 12a-i for `TimeSpan`, which appears in `detect`'s signature; both gate 12b-i.

- [ ] 12a.10 RED: `tests/unit/ports/test_capabilities.py` — `DetectionSupport` has exactly `{unsupported,
      requires_setup, available}`; `TrackerCapabilities(tracker_id, detection)`.
- [ ] 12a.11 GREEN: `ports/capabilities.py` — add `DetectionSupport(StrEnum)`, `TrackerCapabilities`.
- [ ] 12a.12 RED: `tests/unit/ports/test_subject_tracker.py` — `BoundingBox`, `SubjectDetection(at_s, box,
      confidence)` construct; `box=None` is an explicit miss, distinguishable from a low-confidence hit
      (`box` set, low `confidence`).
- [ ] 12a.13 GREEN: `ports/subject_tracker.py` — the two dataclasses + `SubjectTrackerPort(Protocol)` with
      `capabilities()`/`detect()`.
- [ ] 12a.14 RED: fake-detector test — the fake returns a detection or explicit miss for every sampled
      point in a requested span, and none outside it.
- [ ] 12a.15 GREEN: `tests/fakes/subject_tracker.py` — script-driven fake, mirroring
      `FakeTranscriptionPort`'s shape.
- [ ] 12a.16 RED: `domain/errors.py` — `TrackingUnavailable`/`DetectionFailed` derive from `DomainError`.
- [ ] 12a.17 GREEN: add both errors.
- [ ] 12a.18 REFACTOR: suite green, `mypy src tests` clean; confirm `tests/test_architecture.py` still
      passes with the two new port/domain modules.

---

## Slice 12b-i: Trajectory Pipeline — Centres, Smoothing, Dead-Zone (~525 lines)

Closes: `subject-tracking` Smoothing, Dead-Zone (both scenarios). Depends on 12a-i and 12a-ii; gates 12b-ii.

- [ ] 12b.1 RED: `tests/unit/usecases/test_plan_trajectory.py` — a hit's `box` maps to a desired centre; a
      miss contributes no centre.
- [ ] 12b.2 GREEN: `usecases/plan_trajectory.py` — stage 2 (centres) over the fake detector's output.
- [ ] 12b.3 RED: jitter test — a small frame-to-frame oscillation around a stable position does not
      reproduce in the smoothed output.
- [ ] 12b.4 GREEN: stage 3 — centred moving average over `smoothing_window_s`, computed over the tracked
      subsequence only (misses excluded, not zero-filled).
- [ ] 12b.5 RED: sub-threshold-movement test — displacement within `dead_zone_fraction * frame.width` does
      not move the committed crop.
- [ ] 12b.6 GREEN: stage 4 — forward-hysteresis dead-zone.
- [ ] 12b.7 RED: supra-threshold-movement test — displacement beyond the dead-zone shifts the crop to
      follow the subject.
- [ ] 12b.8 GREEN: confirm the commit branch of stage 4.
- [ ] 12b.9 RED: stage-ordering regression test — smoothing then dead-zone (not the reverse) does not
      twitch at the threshold on synthetic jitter data.
- [ ] 12b.10 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 12b-ii: Trajectory Pipeline — Clamp, Fill, Confidence (~575 lines)

Closes: `subject-tracking` Clamping to Frame Edges, Interpolation Across Detection Gaps, Fallback to
Center (both scenarios), Keyframe Provenance Is Marked, Mostly-Fallback Trajectory Reported (both
scenarios), Trajectory Arithmetic Is Testable With No Model Weights. Depends on 12b-i; gates 13b-iii, the
only consumer of `build_trajectory`'s output.

- [ ] 12b.11 RED: edge-clamp test — a detection near the frame edge produces a crop rect clamped fully
      inside the frame.
- [ ] 12b.12 GREEN: stage 5 — `x = min(max(x, 0), frame.width - crop_w)`, same for `y`; applied last among
      position stages.
- [ ] 12b.13 RED: bounded-short-gap test — a no-detection run bounded by `TRACKED` keyframes on both sides,
      `<= max_gap_s`, fills with `INTERPOLATED` keyframes moving continuously between the bounding rects.
- [ ] 12b.14 GREEN: stage 6a — linear interpolation over a bounded gap.
- [ ] 12b.15 RED: leading-gap and over-long-gap tests — a leading run and a run exceeding `max_gap_s` both
      fill with `FALLBACK_CENTER`, never `INTERPOLATED`.
- [ ] 12b.16 GREEN: stage 6b — centred-rect fallback for every run not eligible for interpolation.
- [ ] 12b.17 RED: provenance-query test — every keyframe in a full-clip trajectory reports exactly one of
      the three origins, matching what actually produced it.
- [ ] 12b.18 GREEN: confirm origin tagging across all three producing paths.
- [ ] 12b.19 RED: confidence test — a predominantly-`FALLBACK_CENTER` trajectory is `LOW_CONFIDENCE`; a
      predominantly-`TRACKED`-or-`INTERPOLATED` trajectory is not.
- [ ] 12b.20 GREEN: compute `fallback_ratio` once on the finished trajectory; threshold against
      `policy.max_fallback_ratio`.
- [ ] 12b.21 RED: no-vision-weights marker check — the full `test_plan_trajectory.py` module runs under
      the default suite's marker filter with zero `localmodel` imports.
- [ ] 12b.22 GREEN: confirm (structural — no production change expected).
- [ ] 12b.23 REFACTOR: extract the shared "run of misses" segmentation helper used by stage 6a/6b; suite
      green.

---

## Slice 13a-i: `VideoRenderPort` + Rendering Domain Types + Quality Arithmetic (~525 lines)

Closes: `clip-rendering` VideoRenderPort Contract (type-level), Rendered Content Originates Only From the
Source, Output Quality Declaration. Depends on 12a-i (`CropRect`, `TrackingConfidence`). Gates 13a-ii and
13a-iii, which name the types it creates; all three gate 13b-i.

- [ ] 13a.1 RED: `tests/unit/domain/test_ids.py` — `ClipId`/`make_clip_id` generate and validate against
      the same ULID regex as `JobId`.
- [ ] 13a.2 GREEN: `domain/ids.py` — `ClipId` `NewType` + `make_clip_id`.
- [ ] 13a.3 RED: `tests/unit/domain/test_rendering.py` — `OutputSpec`, `OutputQuality`, `OutputQualityKind`,
      `SubtitleCue`, `SubtitleTimingSource`, `CaptionCoverage` (exactly `{confirmed_speech,
      includes_unverified, none}`), `RenderedClip` (carrying all four declarations), `ClipExport`,
      `ClipState` construct and stay frozen.
- [ ] 13a.4 GREEN: `domain/rendering.py` — the nine new types.
- [ ] 13a.5 RED: `tests/unit/ports/test_video_render.py` — `RenderRequest`, `RenderedFile` construct;
      `RenderCapabilities`/`RenderSupport` mirror the `DiarizationSupport` shape.
- [ ] 13a.6 GREEN: `ports/video_render.py` — the two dataclasses + `VideoRenderPort(Protocol)`;
      `ports/capabilities.py` — `RenderSupport(StrEnum)`, `RenderCapabilities`.
- [ ] 13a.7 RED: `domain/errors.py` — `RenderFailed`, `ClipRangeInvalid` derive from `DomainError`.
- [ ] 13a.8 GREEN: add both errors.
- [ ] 13a.9 RED: structural test — `ast`-parse `ports/video_render.py`/`domain/rendering.py` and assert no
      field type can carry an external file handle, URL, or binary payload (mirrors the shipped "no
      `UploadFile` import" test).
- [ ] 13a.10 GREEN: confirm by construction (no production change expected).
- [ ] 13a.11 RED: `tests/unit/domain/test_framing.py` — `quality_of(crop, target)` pinned: a 4K-derived
      crop (1214×2160, width×height, from `12a.8`) vs a 1080-wide target → `NATIVE`, factor `0.89`; a
      1080p-derived crop (606×1080) vs the same target → `UPSCALED`, factor `1.78`.
- [ ] 13a.12 GREEN: `domain/framing.py` — `quality_of(crop, target)` module function.
- [ ] 13a.13 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 13a-ii: ASS Subtitles + Cue Building (~525 lines)

Closes: `clip-rendering` Subtitle Burn-In From Structured Transcript (both scenarios), Cue Eligibility Is
Decided by `SegmentKind` and Coverage Is Declared (all 4 scenarios), Missing Word Timing Is Declared Not
Silently Degraded (both scenarios); threat-matrix row **ASS subtitle content injection**.
Depends on 13a-i (`SubtitleCue`, `SubtitleTimingSource`, `CaptionCoverage`) and on **11b-i**
(`TranscriptSegment.words`). Independent of 13a-iii; gates 13b-i and 13b-iii.

- [ ] 13a.14 RED: `tests/unit/adapters/ffmpeg/test_subtitles.py` — hostile strings (`{\an8}`, a lone `}`, a
      lone `\`, `\r\n`, a 5,000-char run) each emit a single dialogue line with no override block, `\`
      escaped, CR/LF stripped, intended breaks only as `\N`.
- [ ] 13a.15 GREEN: `adapters/ffmpeg/subtitles.py` — the escaping function + `.ass` file generation from
      `tuple[SubtitleCue, ...]`.
- [ ] 13a.16 RED: `tests/unit/usecases/test_build_subtitle_cues.py` — a multi-second `SPEECH` segment with
      `words` splits into cues at word boundaries, none exceeding `max_cue_chars`; plus the eligibility
      rule: a `MUSIC` segment in the span produces no cue while keeping its timestamps in the transcript,
      and an `UNCERTAIN` segment does produce cues, carrying no `UNCERTAIN_MARKER` in the cue text.
- [ ] 13a.17 GREEN: `usecases/build_subtitle_cues.py` — eligibility via `without_music(...)` over the
      segments overlapping the requested span, minus any segment whose text is empty once stripped, then
      word-boundary cue splitting; one selector, reused by both declarations.
- [ ] 13a.18 RED: word-less-segment test — a segment with `words=()` yields one cue at segment times, never
      an evenly-distributed guess.
- [ ] 13a.19 GREEN: implement the segment-level fallback branch.
- [ ] 13a.20 RED: declaration tests — (a) timing source: a clip whose every **eligible** segment carries
      `words` returns `SubtitleTimingSource.WORD_LEVEL`, any eligible segment lacking `words` degrades the
      whole clip to `SEGMENT_LEVEL`, and a span with zero eligible segments returns `SEGMENT_LEVEL` rather
      than a vacuous `WORD_LEVEL`; (b) caption coverage: all-`SPEECH` → `CONFIRMED_SPEECH`, any
      `UNCERTAIN` → `INCLUDES_UNVERIFIED`, all-`MUSIC` span → zero cues and `NONE`; (c) totality — a
      whitespace-only segment is not eligible, every eligible segment yields at least one cue, and
      therefore `NONE` is declared whenever the cue set is empty (no `CONFIRMED_SPEECH` or
      `INCLUDES_UNVERIFIED` clip carries zero cues).
- [ ] 13a.21 GREEN: `build_subtitle_cues` returns `(cues, timing_source, coverage)`, both declarations
      computed from **one basis** — the actual eligible segments in range, never the cues and never
      `capabilities().word_timing` — with cue construction total over that same set.
- [ ] 13a.22 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 13a-iii: Filter-Graph Composition + `sendcmd` Densification (~575 lines)

Closes: `clip-rendering` Single Native ffmpeg Pass, Crop Trajectory Applied As Given; threat-matrix row
**ffmpeg filter-graph composition**. Depends on 13a-i (`ClipId`, `OutputSpec`) and 12a-i
(`CropTrajectory`). Independent of 13a-ii; gates 13b-i.

- [ ] 13a.23 RED: `tests/unit/adapters/ffmpeg/test_argv_composition.py` — the render argv contains one
      `-filter_complex` chaining `sendcmd`→`crop`→`scale`→`subtitles`, `-ss` before `-i`, absolute
      source/dest paths, and exactly one ffmpeg invocation per render.
- [ ] 13a.24 GREEN: `adapters/ffmpeg/argv.py` — `build_render_argv()` extending the shipped
      prefix/containment helpers.
- [ ] 13a.25 RED: bare-relative-filename test — the composed graph references `<clip_id>.cmds`/`.ass` by
      bare filename with **no directory prefix and no path separator**, never an absolute path, regardless
      of a job-directory path containing `:`, `'`, `,`, or `\`; the composer reports the job's `render/`
      subdirectory as the `cwd` the graph resolves against.
- [ ] 13a.26 GREEN: confirm the graph composer never interpolates the job-directory path, nor a `render/`
      prefix, into the filter string.
- [ ] 13a.27 RED: non-ULID-`clip_id` test — a `clip_id` failing the ULID regex is refused before the graph
      is composed.
- [ ] 13a.28 GREEN: validate `clip_id` against `domain/ids.py`'s regex before composition.
- [ ] 13a.29 RED: `tests/unit/adapters/ffmpeg/test_sendcmd.py` — a `CropTrajectory` sampled at
      `sample_hz=4` densifies to `command_hz=25` commands via linear interpolation, introducing no new
      `INTERPOLATED`/`FALLBACK_CENTER` origin (the densifier is origin-blind).
- [ ] 13a.30 GREEN: `adapters/ffmpeg/sendcmd.py` — the densifying command-file writer.
- [ ] 13a.31 RED: no-recompute test — densified rects never leave the frame (proven by convexity), and no
      smoothing/dead-zone/clamp parameter is referenced by the densifier module.
- [ ] 13a.32 GREEN: confirm by construction (`ast`-parse `sendcmd.py`, assert no import of
      `plan_trajectory`'s policy type).
- [ ] 13a.33 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 13b-i: Real `VideoRenderPort` Adapter + Render Guards (~575 lines)

Closes: `clip-rendering` VideoRenderPort Contract (adapter half), Clip Cut From Source Time Range Only;
threat-matrix row **render resource exhaustion** (guard half). Depends on 13a-i/ii/iii.

- [ ] 13b.1 RED: `tests/unit/adapters/ffmpeg/test_video_render.py` — the adapter spawns exactly one
      process via an injected `RenderProcessRunner` with `cwd` set to the job's `render/` subdirectory
      (`resolve_inside`-checked against the job directory), which is the directory it wrote `.cmds` and
      `.ass` into, so the graph's bare filenames resolve; argv matches `build_render_argv()`'s output.
- [ ] 13b.2 GREEN: `adapters/ffmpeg/video_render.py` — implements `VideoRenderPort`; declares its own
      `RenderProcessRunner` protocol.
- [ ] 13b.3 RED: `tests/unit/usecases/test_render_clip.py` — `0 <= start_s < end_s <= probe.duration_s`
      and `end_s - start_s <= max_clip_seconds` violations each raise `ClipRangeInvalid` before any spawn.
- [ ] 13b.4 GREEN: `usecases/render_clip.py` — the pre-spawn guard clauses.
- [ ] 13b.5 RED: timeout test — a render exceeding `max(60.0, 20 * clip_duration_s)` surfaces as
      `RenderFailed`, never a raw `TimeoutExpired`.
- [ ] 13b.6 GREEN: implement the timeout translation in the adapter.
- [ ] 13b.7 RED: `FfmpegUnavailable` test — the adapter reuses the shipped PATH check before spawning.
- [ ] 13b.8 GREEN: wire the shared PATH-check helper (from `adapters/ffmpeg/extractor.py`) into the render
      adapter.
- [ ] 13b.9 REFACTOR: extract the subprocess-invocation helper shared with `extractor.py`, now three call
      sites; suite green.

---

## Slice 13b-ii: `ClipExport` Storage (~400 lines)

Closes: `clip-rendering` Clip Export to Job Directory (both scenarios). Depends on 13a-i — `ClipExport`
and `ClipState` are the values it round-trips. Independent of 13a-ii, 13a-iii and 13b-i.

- [ ] 13b.10 RED: `tests/unit/ports/test_transcript_storage.py` — `TranscriptStoragePort` declares
      `save_clip_export`/`load_clip_exports`.
- [ ] 13b.11 GREEN: `ports/transcript_storage.py` — add the two methods.
- [ ] 13b.12 RED: `tests/unit/adapters/storage/test_filesystem_transcript_storage.py` —
      `save_clip_export`/`load_clip_exports` round-trip a `ClipExport` through `render/{clip_id}.json`;
      `ClipState` transitions persist.
- [ ] 13b.13 GREEN: `adapters/storage/filesystem_transcript_storage.py` — implement both methods, reusing
      the shipped atomic-write helper.
- [ ] 13b.14 RED: no-external-service test — the export path makes no network call (structural — no
      `httpx`/socket import in the storage module).
- [ ] 13b.15 GREEN: confirm by construction.
- [ ] 13b.16 GREEN: `tests/fakes/transcript_storage.py` — add the fake implementation for both methods.
- [ ] 13b.17 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 13b-iii: `render_worker` Entrypoint (~575 lines)

Closes: `clip-rendering` Crop Trajectory Applied As Given (orchestration half), Low-Confidence Trajectory
Is Not Delivered as an Ordinary Success; `subject-tracking` Detection is scoped to the clip (orchestration
half). Depends on 11a (`probe.frame`, `FrameGeometryUnavailable`), 12a-ii (`SubjectTrackerPort`,
`TrackingUnavailable`), 12b-i (`build_trajectory`), 12b-ii (`LOW_CONFIDENCE`), 13a-i, 13a-ii, 13b-i,
13b-ii. It is the only unit that calls the trajectory pipeline rather than merely naming its types.

- [ ] 13b.18 RED: `tests/unit/runtime/test_render_worker.py` — the happy path calls `probe`→`detect`→
      `build_trajectory`→`load_transcript`→`build_subtitle_cues`→`render`→`quality_of` in order, against
      fakes, and writes a `RENDERED` `ClipExport`.
- [ ] 13b.19 GREEN: `runtime/render_worker.py` — headless entrypoint `python -m
      onevoicecut.runtime.render_worker --job-id <id> --clip-id <id>`.
- [ ] 13b.20 RED: frame-geometry-refusal test — two cases, both writing a `FAILED` `ClipExport` naming
      `FrameGeometryUnavailable` and never calling the tracker: (a) `probe.frame is None`; (b) a degenerate
      frame such as `FrameSize(1920, 1)`, for which `crop_size_for` returns a non-positive dimension
      (`12a.8`'s property) — refused here so `quality_of` never divides by a zero crop width.
- [ ] 13b.21 GREEN: implement the first `alt` branch from the design's sequence diagram.
- [ ] 13b.22 RED: tracking-unavailable-refusal test — when `capabilities().detection != AVAILABLE`, the
      worker writes `FAILED(TrackingUnavailable)` naming remediation, and never calls `detect()`.
- [ ] 13b.23 GREEN: implement the second `alt` branch.
- [ ] 13b.24 RED: low-confidence-propagation test — a `LOW_CONFIDENCE` trajectory produces a
      `RenderedClip.tracking` that is not silently reported as ordinary success.
- [ ] 13b.25 GREEN: propagate `TrackingConfidence` from `build_trajectory`'s output, and
      `SubtitleTimingSource`/`CaptionCoverage` from `build_subtitle_cues`'s output, onto the assembled
      `RenderedClip` — all four declarations computed above the port.
- [ ] 13b.26 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 13b-iv: HTTP Clip Routes (~525 lines)

Closes: `clip-rendering` Clip Export to Job Directory (HTTP surface). Depends on 13b-ii, 13b-iii.

- [ ] 13b.27 RED: `tests/unit/adapters/web/test_clip_routes.py` — `POST /api/jobs/{id}/clips
      {candidate_index, variant}` against a job not `COMPLETED` returns `409`; against a `COMPLETED` job
      returns `202 {clip_id}` and writes a `PENDING` `ClipExport` before responding.
- [ ] 13b.28 GREEN: `adapters/web/routers/jobs.py` — the `POST .../clips` route + `adapters/web/schemas.py`
      request/response models.
- [ ] 13b.29 RED: spawn test — admitting a clip request spawns `render_worker` with the same mechanism
      used for the transcription worker, and the HTTP response returns before the render completes.
- [ ] 13b.30 GREEN: wire the spawn call, mirroring the shipped upload-triggers-worker pattern.
- [ ] 13b.31 RED: `GET /api/jobs/{id}/clips/{clip_id}` — returns `{state, quality, subtitle_timing,
      captions, tracking}` read-only; a test enforces it writes nothing.
- [ ] 13b.32 GREEN: the status-read route over `load_clip_exports`.
- [ ] 13b.33 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 13b-v: Real ffmpeg Render Integration (~425 lines)

Closes: `clip-rendering` VideoRenderPort Contract (integration proof), Single Native ffmpeg Pass
(integration proof); threat-matrix row **render resource exhaustion** (timeout half). Depends on 13b-i,
13b-iii.

- [ ] 13b.34 RED: `integration`-marked test — a real ffmpeg render of a tiny synthesized fixture (`-f
      lavfi`, matching the 3a precedent) produces a 9:16 file whose commanded crop matches the trajectory
      and whose frame carries visible burned-in text; skips when ffmpeg is absent.
- [ ] 13b.35 GREEN: fix any real-argv/filter-graph gap the integration test exposes.
- [ ] 13b.36 RED: `integration`-marked test — graph composition under a real job-directory path containing
      `:` (Windows drive letter) succeeds because aux files are referenced by bare filename.
- [ ] 13b.37 GREEN: confirm/fix.
- [ ] 13b.38 RED: `integration`-marked timeout test — a deliberately hung ffmpeg process is killed at the
      computed timeout and surfaces as `RenderFailed`.
- [ ] 13b.39 GREEN: confirm/fix the real timeout wiring.
- [ ] 13b.40 REFACTOR: suite green (`integration` included where ffmpeg is present), `mypy src tests`
      clean; update `README.md` if a render dependency needs stating.

---

## Slice 13c-i: Real Vision-Backed `SubjectTrackerPort` Adapter (~475 lines)

Closes: `subject-tracking` Detection is scoped to the clip (real-adapter half); threat-matrix row
**vision adapter decode**. Depends on 12a-ii's port and on 13b-i for `max_clip_seconds`, which `13c.3`'s
span refusal reads. Independent of 13b-ii through 13b-v.

- [ ] 13c.1 RED: `localmodel`-marked test — the real adapter decodes only the requested clip span,
      in-process, with no subprocess pipe of raw frames.
- [ ] 13c.2 GREEN: `adapters/vision/*_tracker_adapter.py` — sequential in-process decode over the span,
      downscaled to ≤640px, evaluating every Nth frame.
- [ ] 13c.3 RED: `localmodel`-marked test — a span longer than `max_clip_seconds` is refused before any
      decode.
- [ ] 13c.4 GREEN: the pre-decode span guard.
- [ ] 13c.5 RED: `localmodel`-marked test — `capabilities().detection` reports `REQUIRES_SETUP` when the
      vision weights/`requirements-vision.txt` extra is absent, `AVAILABLE` once installed.
- [ ] 13c.6 GREEN: implement the capability probe.
- [ ] 13c.7 REFACTOR: register the adapter in a tracker-resolver mirroring
      `runtime/engine_resolver.py`'s shape; suite green.

---

## Slice 13c-ii: Real Adapter Contract Test (~300 lines)

Closes: `subject-tracking` Real Detection Adapter Is Isolated Behind the `localmodel` Marker, A Miss Is
Reported Never Guessed (real-adapter half). Depends on 13c-i.

- [ ] 13c.8 RED: `tests/contract/test_subject_tracker_contract.py`, `localmodel`-marked — the real adapter
      satisfies the shared contract body (return shape, clip-local `at_s`, miss distinguishable from
      low-confidence hit) alongside the fake.
- [ ] 13c.9 GREEN: register the real adapter in the contract-test parametrization.
- [ ] 13c.10 RED: never-synthesized-centre test — on a fixture clip with a genuinely absent subject, the
      real adapter's misses carry `box=None`, never a centered guess.
- [ ] 13c.11 GREEN: confirm/fix if the adapter's own occlusion handling produces a low-confidence box
      instead of an explicit miss.
- [ ] 13c.12 REFACTOR: full default suite green; confirm zero `localmodel`-marked test executes outside
      `pytest -m localmodel`.
