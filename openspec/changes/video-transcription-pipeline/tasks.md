# Tasks: Video Transcription Pipeline

> Phase: `sdd-tasks` (rev 4 — non-speech audio) · Artifact store: hybrid (mirror of Engram `sdd/video-transcription-pipeline/tasks`)
> Inputs: `proposal.md` rev 3, `design.md`, all seven `specs/*/spec.md`, `openspec/config.yaml`, and **slice 1's actual code on
> disk** (`src/onevoicecut/`, `tests/`) as the calibration source for this revision.
> **Rev 4 delta**: proposal Open Question 8 was answered after slice 1 shipped — source footage routinely contains a singer
> alongside the speaker, or music under and between spoken passages. Music is normal input, not a defect. This adds slice 1b
> (`SegmentKind`) ahead of chunk planning, plus classification/containment tasks in 2b, 7a, 8a, 10a and 10b. The stacked chain
> grows from 21 to 22 work units and every PR number after PR 1 shifts by one. No previously-checked slice-1 task is modified.
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
| **Estimate reliability after nine measured slices** | Range 3.2x–5.1x, **mean ≈ 4.0x, no slice under 3.2x**. This is no longer estimation noise — it is a fixed multiplier. Treat every remaining number in this document as `estimate × 4`, and note that the two categories that ran worst (first-of-its-kind adapter, first end-to-end assembly) both still lie ahead for the render work in slices 11-13 |
| Per-unit 800-line budget risk | **Low** — every one of the 21 remaining work units is individually estimated at 145–350 lines, with margin, not at the ceiling |
| Aggregate 800-line budget risk | **High** by construction — this is why the change stays split into 23 work units total |
| Chained PRs recommended | Yes |
| Suggested split | **23 work units**, PR 1 (slice 1, done) → PR 23 |
| Delivery strategy | auto-chain |
| Chain strategy | **stacked-to-main** (resolved this session — no longer pending) |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
800-line budget risk: Low
```

**Is this one change or should it split?** Twenty-three stacked PRs across ten domains is large but each unit is
independently revertible, dependency order is linear (1b → 2 → 4 → {5,6} → {7,8} → 9 → 10a → 10b), and every unit ends
green on the default suite. Nothing here requires two teams working concurrently or two independent release
cadences — it is one coherent hexagonal build-out, not two products. **Recommendation: keep it one change**, delivered
as a long stacked-PR chain, not split into separate OpenSpec changes. If the user wants a narrower blast radius per
change instead, the natural split point is: **Change A** = bootstrap + core pipeline (slices 1–6, ingest through
diarization gate, no real ASR yet) and **Change B** = engines + generation (slices 7–10b, real ASR adapters +
summarization). That split is offered, not assumed.

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
| 7a | Local ASR adapter + shared contract test | PR 16 | `pytest tests/unit -m "not paid and not localmodel"` (adapter is `localmodel`-marked) | `pytest -m localmodel` — real `faster-whisper`, real weights | `adapters/asr/local/faster_whisper_adapter.py`, `tests/contract/` |
| 7b | Supervisory watchdog | PR 17 | `pytest tests/unit/runtime/test_supervisor.py -m "not paid and not localmodel"` | `pytest -m localmodel` real timeout-kill scenario | `runtime/supervisor.py` |
| 8a | Cloud ASR adapter + real byte cap + contract test | PR 18 | `pytest tests/unit -m "not paid and not localmodel"` (adapter is `paid`-marked) | `pytest -m paid` — real API key, real billed call | `adapters/asr/cloud/*_adapter.py` |
| 8b | `ChunkTooLarge` split-and-retry | PR 19 | `pytest tests/unit/usecases/test_transcribe_job_split_retry.py -m "not paid and not localmodel"` | `pytest -m paid` oversized-chunk scenario | `usecases/plan_chunks.py`/`transcribe_job.py` split-retry branch only |
| 9a | Local diarization (capability probe + call) | PR 20 | `pytest tests/unit -m "not paid and not localmodel"` | `pytest -m localmodel` real diarization | `adapters/asr/local/` diarization branch |
| 9b | Cloud diarization + `SpeakerResolver` seam | PR 21 | `pytest tests/unit/usecases/test_stitch_transcript_resolver.py -m "not paid and not localmodel"` | `pytest -m paid` real cloud diarization | `adapters/asr/cloud/` diarization branch, `usecases/stitch_transcript.py` resolver seam |
| 10a | Map-reduce summarization | PR 22 | `pytest tests/unit/usecases/test_generate_artifacts_map.py -m "not paid and not localmodel"` | `pytest -m paid` real LLM call | `usecases/generate_artifacts.py` MAP/REDUCE |
| 10b | Clip candidates + N script variants | PR 23 | `pytest tests/unit/usecases/test_generate_artifacts_variants.py -m "not paid and not localmodel"` | `pytest -m paid` real LLM call | `usecases/generate_artifacts.py` candidate/variant phase |

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

## Slice 7a: Local ASR Adapter + Contract Test (~260 lines)

Closes: `speech-transcription` TranscriptionPort Contract (local), Contract Parity and Declared Divergence
(local half, single-speaker path). Real-engine work is `localmodel`-marked, excluded from the default run — but
the contract-test body is still authored/committed code and counted here. First real ASR adapter — no slice 1
comparable, +15% uncertainty margin applied.

- [ ] 7.1 RED: `localmodel`-marked contract test — real `faster-whisper` adapter satisfies the shared
      single-speaker contract body.
- [ ] 7.2 GREEN: `adapters/asr/local/faster_whisper_adapter.py` implementing `TranscriptionPort`;
      `capabilities()` still returns `DiarizationSupport.UNSUPPORTED` (diarization lands slice 9a), real
      `max_chunk_bytes=None`, real `max_chunk_duration_s`.
- [ ] 7.3 RED: shared `tests/contract/` module, parametrized to include the local adapter alongside the
      existing fake, `localmodel`-marked, excluded from the default run.
- [ ] 7.4 GREEN: register the adapter in `runtime/engine_resolver.py` for `EngineChoice.LOCAL`.
- [ ] 7.4a RED: `localmodel`-marked classification test — the local adapter declares
      `non_speech_classification=AVAILABLE` and marks a music-only fixture segment as `MUSIC`, not `SPEECH`.
- [ ] 7.4b GREEN: enable the engine's voice-activity filter and the decoder guards that break degenerate
      repetition loops (`no_speech_threshold`, `compression_ratio_threshold`, `condition_on_previous_text`
      disabled); map their output onto `SegmentKind`.
- [ ] 7.4c RED: `localmodel`-marked hallucination-containment test — a music-only fixture produces no
      `SPEECH`-classified segment carrying fabricated text (the Spanish subtitle-boilerplate failure).

## Slice 7b: Supervisory Watchdog (~250 lines)

Closes: `transcription-jobs` Per-Chunk Timeout (uninterruptible-inference case). Split from 7a because the
watchdog is a process-supervision subsystem (`multiprocessing`, mtime polling, kill), not an ASR concern — rev 2
under-scoped this as a sub-task of "Local ASR Adapter" when it is really independent infrastructure.

- [ ] 7.5 RED: supervisory watchdog test — no progress past `chunk_timeout_s` kills the worker process,
      chunk recorded `FAILED(TIMEOUT)`.
- [ ] 7.6 GREEN: `runtime/supervisor.py` watchdog watching `results/` mtime.
- [ ] 7.7 REFACTOR: extract adapter-construction/secret-read logic shared with the cloud adapter (slice 8a)
      into a resolver helper; suite green.

---

## Slice 8a: Cloud ASR Adapter + Real Byte Cap + Contract Test (~260 lines)

Closes: `speech-transcription` TranscriptionPort Contract (cloud), Contract Parity and Declared Divergence
(cloud half), Cloud Adapter Request-Size Handling (real cap). Slice 2a already implemented the byte-cap-aware
planning formula against a fake `max_chunk_bytes=25_000_000`; this slice supplies the real value only.
Real-engine work is `paid`-marked, excluded from the default run. First real HTTP-client ASR adapter, +15%
uncertainty margin applied.

- [ ] 8.1 RED: `paid`-marked contract test — real cloud adapter satisfies the shared single-speaker
      contract body.
- [ ] 8.2 GREEN: `adapters/asr/cloud/*_adapter.py` implementing `TranscriptionPort` with an HTTP client +
      in-call timeout; `capabilities()` returns real `max_chunk_bytes=25_000_000` (still
      `DiarizationSupport.UNSUPPORTED`), reads `CLOUD_ASR_API_KEY` at construction.
- [ ] 8.3 GREEN: register in `engine_resolver.py` for `EngineChoice.CLOUD`.
- [ ] 8.4 RED: within-limit test — a plan sized against the real 25MB cap never exceeds it on submission.
- [ ] 8.5 GREEN: `paid`-marked assertion confirming the slice-2a planner logic already holds against the
      real capability value.
- [ ] 8.5a RED: `paid`-marked classification-declaration test — the cloud adapter declares its **real**
      `non_speech_classification` value for the chosen provider. A raw Whisper-API-style adapter exposing no
      VAD control MUST declare `UNSUPPORTED` and return `UNCERTAIN` segments; a provider with server-side VAD
      MAY declare `AVAILABLE`. Assert the declaration matches observed behavior — do not assume parity with
      the local adapter, and do not infer it from the adapter's diarization support.
- [ ] 8.5b GREEN: implement the declared behavior for the chosen provider.

## Slice 8b: `ChunkTooLarge` Split-and-Retry (~145 lines)

Closes: `speech-transcription` Cloud Adapter Request-Size Handling (recovery half). Smallest remaining unit — kept
separate because it is a narrow recovery-path addition to two already-shipped files (`plan_chunks.py`,
`transcribe_job.py`), independently revertible without touching the cloud adapter itself.

- [ ] 8.6 RED: `ChunkTooLarge` split-and-retry test — an oversized actual chunk triggers a half-split
      re-slice instead of a failed job.
- [ ] 8.7 GREEN: `plan_chunks.py`/`transcribe_job.py` — catch `ChunkTooLarge`, split, re-slice, retry.
- [ ] 8.8 REFACTOR: unify in-call-timeout construction between local/cloud resolver branches; suite green.

---

## Slice 9a: Local Diarization (~230 lines)

Closes: `speech-transcription` Reject Speaker-Mode Jobs the Adapter Cannot Satisfy (positive path, local),
Contract Parity and Declared Divergence (diarization scenario, local half). Flips the local adapter from
`UNSUPPORTED`/`REQUIRES_SETUP` to `AVAILABLE`; the rejection path itself was already proven in slice 6. No new
domain dataclasses (`TranscriptSegment.speaker` already exists from slice 1); +15% margin applied for the real
`pyannote`/WhisperX integration surface.

- [ ] 9.1 RED: `localmodel`-marked test — local adapter declares `AVAILABLE` when `pyannote.audio`/WhisperX
      is installed and the licence accepted, `REQUIRES_SETUP` otherwise.
- [ ] 9.2 GREEN: extend `faster_whisper_adapter.capabilities()` to probe install state; add diarization
      sub-adapter.
- [ ] 9.3 RED: diarizing-adapter-receives-multi-speaker-job test — returned segments include a speaker
      label per segment, namespaced `c{chunk_index:02d}/S{speaker:02d}`.
- [ ] 9.4 GREEN: implement the diarization call + namespaced label assignment.

## Slice 9b: Cloud Diarization + `SpeakerResolver` Seam (~210 lines)

Closes: Contract Parity and Declared Divergence (diarization scenario, cloud half). Introduces the
`SpeakerResolver` seam design flagged as a discovered risk (cross-chunk speaker identity) — split from 9a because
the cloud provider's diarization divergence and the stitcher-level resolver seam are independently revertible from
the local adapter's diarization support.

- [ ] 9.5 RED: cloud diarization test (`paid`-marked) — asserts the declared divergence per provider
      (e.g. flips to `AVAILABLE`, or a Whisper-API-based adapter stays `UNSUPPORTED` and still refuses).
- [ ] 9.6 GREEN: implement or explicitly document the divergence for the chosen cloud provider.
- [ ] 9.7 RED: `SpeakerResolver` seam test — stitcher accepts a no-op default resolver passing namespaced
      labels through unchanged; a stub resolver substitutes without touching the stitching algorithm.
- [ ] 9.8 GREEN: `usecases/stitch_transcript.py` — inject `SpeakerResolver` protocol, default no-op impl.
      **Answers the new cross-chunk speaker identity question later**.
- [ ] 9.9 GREEN: extend slice-6's admission tests to also cover now-`AVAILABLE` engines admitting normally.
- [ ] 9.10 REFACTOR: consolidate the two adapters' capability-probing pattern; suite green.

---

## Slice 10a: Map-Reduce Summarization (~320 lines)

Closes: `script-generation` Map-Reduce Summarization. **No new domain dataclasses** — `GenerationResult`,
`ClipCandidate`, `ScriptVariant`, and `TextGenerationPort` all already exist from slice 1 (confirmed by reading
`src/onevoicecut/domain/generation.py` and `src/onevoicecut/ports/text_generation.py`). This is the cheapest
remaining category of work: pure use-case logic over an already-built port. A modest +10% margin (not +15%)
applies only for the yet-to-be-built fake `TextGenerationPort` test double.

- [ ] 10.1 RED: fake `TextGenerationPort`-based test — `complete()` call shape only, no summary logic yet.
- [ ] 10.2 GREEN: `tests/fakes/text_generation.py` — new fake conforming to the existing port.
- [ ] 10.3 RED: `tests/unit/usecases/test_generate_artifacts_map.py` — a transcript exceeding
      `map_window_tokens` windows by estimated char/4 budget, 200-token overlap, rendered with segment ids.
- [ ] 10.4 GREEN: `usecases/generate_artifacts.py` MAP phase.
- [ ] 10.4a RED: speech-only-windowing test — a transcript mixing `SPEECH` and `MUSIC` segments produces MAP
      windows containing no `MUSIC` (or `UNCERTAIN`) content, so lyrics never reach the model as message text.
- [ ] 10.4b GREEN: filter to `kind == SPEECH` **before** windowing, reusing the slice-1b helper so "speech only"
      keeps one definition.
- [ ] 10.4c RED: music-heavy-transcript test — when most of a transcript is non-speech, the summary derives
      only from the remaining speech and the system does not substitute non-speech content to fill it.
- [ ] 10.5 RED: segment-id-rejection test — a model response referencing an id absent from its window is
      rejected.
- [ ] 10.6 GREEN: id-validation against the real `Transcript`.
- [ ] 10.7 RED: REDUCE test — partial summaries fold sequentially into one final summary without a single
      call exceeding practical context.
- [ ] 10.8 GREEN: REDUCE phase.
- [ ] 10.9 RED: `ContextLengthExceeded` retry test — window halves and retries.
- [ ] 10.10 GREEN: implement halving retry.
- [ ] 10.11 REFACTOR: extract token-estimation helper; suite green.

## Slice 10b: Clip Candidates + N Script Variants (~240 lines)

Closes: `script-generation` Clip Candidate Output, N Script Variants Per Clip Candidate, Scope Boundary —
No Rendering. Same no-new-dataclass calibration as 10a; builds directly on its MAP/REDUCE infrastructure.

- [ ] 10.12 RED: clip-candidate test — candidate carries `start_s`/`end_s` mapping into the source
      transcript plus a short script.
- [ ] 10.13 GREEN: rank-by-score candidate selection, top `max_clip_candidates`.
- [ ] 10.13a RED: musical-range-eligible test — a candidate whose time range covers `MUSIC` segments is NOT
      rejected on that basis; its timestamps resolve like any other candidate. Excluding music from the
      *message* must not leak into excluding it from *clips* — the singer's moment is often the best footage.
- [ ] 10.13b GREEN: confirm candidate resolution is `kind`-agnostic. **Leaves Q9 open**: candidates over
      non-speech ranges are permitted here; whether ranking should additionally *favor* them is a prompt/score
      change confined to this slice.
- [ ] 10.14 RED: multiple-variants test — a candidate carries `variants: tuple[ScriptVariant, ...]`
      without a schema change when count > 1.
- [ ] 10.15 GREEN: one `complete()` call per `(candidate, target)` pair, `target` sourced from
      `settings.script_targets`.
- [ ] 10.16 GREEN: ship `settings.script_targets` defaulting to `["generic"]`. **Answers Q3 later**.
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
| Slices 1–10b (existing) | 23 work units, PR 1 → PR 23 (unchanged by this appendix) |
| Slices 11–13 (new) | **18 work units**, PR 24 → PR 41 |
| Estimated changed lines, slices 11–13 | ~9,000 (slice 11 ~2,025 · slice 12 ~2,075 · slice 13 ~4,900) |
| Per-unit 800-line budget risk | **Low** — every one of the 18 new work units is individually estimated at 300–650 lines, with margin |
| Aggregate 800-line budget risk | **High** by construction — this is why the appendix stays split into 18 work units |
| Chained PRs recommended | Yes |
| Suggested split | **18 work units**, PR 24 (slice 11a) → PR 41 (slice 13c-ii) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
800-line budget risk: Low
```

**Grand total across the whole change**: 41 work units, PR 1 → PR 41. Per-file forecasts and Suggested
Work Units tables for each of the three new slices live in `slice-11-tasks.md`, `slice-12-tasks.md`, and
`slice-13-tasks.md`, matching the shape `slice-6-tasks.md` established. The master table below is the
single row-per-unit index across all 18 new units; full RED/GREEN task detail follows in the per-slice
sections after it.

### Suggested Work Units (Slices 11–13, master index)

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|-----------------------|------------------|--------------------|
| 11a | `MediaProbe.frame`: `FrameSize` + two ffprobe guards + `FrameGeometryUnavailable` | PR 24 | `pytest tests/unit/domain/test_media.py tests/unit/adapters/ffmpeg/test_probe_frame.py -m "not paid and not localmodel"` | `pytest -m integration` — real ffmpeg fixture | `domain/media.py`, `adapters/ffmpeg/extractor.py` (`probe()` frame parsing) |
| 11b-i | `WordTiming` domain + capability + fake + contract invariant + `AdmitJob` warning | PR 25 | `pytest tests/unit/domain/test_transcript.py tests/unit/ports/test_capabilities.py tests/unit/usecases/test_admit_job.py tests/contract -m "not paid and not localmodel"` | N/A — fakes only | `domain/transcript.py`, `ports/capabilities.py`, `tests/fakes/transcription.py`, `usecases/admit_job.py` |
| 11b-ii | Stitcher word-timing lockstep | PR 26 | `pytest tests/unit/usecases/test_stitch_transcript.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/stitch_transcript.py` |
| 11b-iii | Storage codec backward-compatible decode | PR 27 | `pytest tests/unit/adapters/storage/test_filesystem_transcript_storage.py -m "not paid and not localmodel"` | `pytest -m integration` | `adapters/storage/serialization.py` |
| 12a-i | `domain/framing.py` entities + `__post_init__` invariant + `crop_size_for` | PR 28 | `pytest tests/unit/domain/test_framing.py -m "not paid and not localmodel"` | N/A — pure domain types | `domain/framing.py` |
| 12a-ii | `SubjectTrackerPort` + `DetectionSupport` + fake detector | PR 29 | `pytest tests/unit/ports/test_capabilities.py tests/unit/ports/test_subject_tracker.py -m "not paid and not localmodel"` | N/A — fake detector only | `ports/subject_tracker.py`, `ports/capabilities.py`, `tests/fakes/subject_tracker.py` |
| 12b-i | Trajectory stages 2–4: centres, smoothing, dead-zone | PR 30 | `pytest tests/unit/usecases/test_plan_trajectory.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/plan_trajectory.py` |
| 12b-ii | Trajectory stages 5–6 + confidence: clamp, fill, provenance, `LOW_CONFIDENCE` | PR 31 | `pytest tests/unit/usecases/test_plan_trajectory.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/plan_trajectory.py` |
| 13a-i | `VideoRenderPort` + `domain/rendering.py` + `ClipId` + `quality_of` + structural test | PR 32 | `pytest tests/unit/domain/test_ids.py tests/unit/domain/test_rendering.py tests/unit/ports/test_video_render.py tests/unit/domain/test_framing.py -m "not paid and not localmodel"` | N/A — pure types + arithmetic | `domain/rendering.py`, `domain/ids.py`, `ports/video_render.py`, `domain/framing.py` |
| 13a-ii | ASS subtitle escaping + cue building | PR 33 | `pytest tests/unit/adapters/ffmpeg/test_subtitles.py tests/unit/usecases/test_build_subtitle_cues.py -m "not paid and not localmodel"` | N/A — pure, no ffmpeg | `adapters/ffmpeg/subtitles.py`, `usecases/build_subtitle_cues.py` |
| 13a-iii | Filter-graph composition + `sendcmd` densification | PR 34 | `pytest tests/unit/adapters/ffmpeg/test_argv_composition.py tests/unit/adapters/ffmpeg/test_sendcmd.py -m "not paid and not localmodel"` | N/A — pure composition | `adapters/ffmpeg/argv.py`, `adapters/ffmpeg/sendcmd.py` |
| 13b-i | Real `VideoRenderPort` adapter + `render_clip` pre-spawn guards | PR 35 | `pytest tests/unit/adapters/ffmpeg/test_video_render.py tests/unit/usecases/test_render_clip.py -m "not paid and not localmodel"` | N/A — injected fake runner | `adapters/ffmpeg/video_render.py`, `usecases/render_clip.py` |
| 13b-ii | `ClipExport` storage (two new port methods) | PR 36 | `pytest tests/unit/ports/test_transcript_storage.py tests/unit/adapters/storage/test_filesystem_transcript_storage.py -m "not paid and not localmodel"` | `pytest -m integration` | `ports/transcript_storage.py`, `adapters/storage/filesystem_transcript_storage.py`, `tests/fakes/transcript_storage.py` |
| 13b-iii | `render_worker` entrypoint + refusal branches + low-confidence propagation | PR 37 | `pytest tests/unit/runtime/test_render_worker.py -m "not paid and not localmodel"` | `python -m onevoicecut.runtime.render_worker --job-id <fake-job> --clip-id <fake-clip>` against fakes | `runtime/render_worker.py` |
| 13b-iv | HTTP clip routes | PR 38 | `pytest tests/unit/adapters/web/test_clip_routes.py -m "not paid and not localmodel"` | Real HTTP client, fake render worker spawn | `adapters/web/routers/jobs.py`, `adapters/web/schemas.py` |
| 13b-v | Real ffmpeg render integration | PR 39 | `pytest tests/unit -m "not paid and not localmodel"` | `pytest -m integration` — real ffmpeg render of a tiny fixture | `tests/integration/test_render_clip.py` |
| 13c-i | Real vision-backed `SubjectTrackerPort` adapter | PR 40 | `pytest tests/unit -m "not paid and not localmodel"` | `pytest -m localmodel` — real weights | `adapters/vision/*_tracker_adapter.py` |
| 13c-ii | Real adapter contract test | PR 41 | `pytest tests/unit -m "not paid and not localmodel"` | `pytest -m localmodel` | `tests/contract/test_subject_tracker_contract.py` |

### Ordering (extends the rev-4 design table with sub-unit dependencies)

`11a` and `11b-i/ii/iii` are mutually independent tracks. Within 11b, `11b-i` gates `11b-ii` and `11b-iii`
(both need the `words` field and `WordTimingSupport`), which are then independent of each other.
`12a-i`/`12a-ii` are independent of each other; `12a-i` needs slice 11a's `FrameSize`. Both gate `12b-i`,
which gates `12b-ii`. `13a-i`/`13a-ii`/`13a-iii` are independent of each other and gate `13b-i`. `13b-ii`
is independent of every other 13a/13b unit. `13b-iii` needs `13a-i`, `13a-ii`, `13b-i`, and `13b-ii`.
`13b-iv` needs `13b-ii` and `13b-iii`. `13b-v` needs `13b-i` and `13b-iii`. `13c-i`/`13c-ii` depend only on
`12a-ii`'s port and may run in parallel with the entire 13b track.

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

Closes: `subject-tracking` CropTrajectory Domain Object. Depends on slice 11a's `FrameSize`. Independent
of 12a-ii; both gate 12b-i.

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
- [ ] 12a.7 GREEN: `domain/framing.py` — `crop_size_for(frame, policy)` module function (pipeline stage 1).
- [ ] 12a.8 RED: `crop_size_for` pinned test — a 4K frame produces an even 9:16 crop width/height; a source
      narrower than 9:16 swaps the derivation axis.
- [ ] 12a.9 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 12a-ii: `SubjectTrackerPort` + Fake Detector (~525 lines)

Closes: `subject-tracking` SubjectTrackerPort Contract, Capability Declaration, A Miss Is Reported Never
Guessed. Independent of 12a-i; both gate 12b-i.

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
scenarios), Trajectory Arithmetic Is Testable With No Model Weights. Depends on 12b-i; gates slice 13a.

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
Source, Output Quality Declaration. Independent of 13a-ii/13a-iii; all three gate 13b-i.

- [ ] 13a.1 RED: `tests/unit/domain/test_ids.py` — `ClipId`/`make_clip_id` generate and validate against
      the same ULID regex as `JobId`.
- [ ] 13a.2 GREEN: `domain/ids.py` — `ClipId` `NewType` + `make_clip_id`.
- [ ] 13a.3 RED: `tests/unit/domain/test_rendering.py` — `OutputSpec`, `OutputQuality`, `OutputQualityKind`,
      `SubtitleCue`, `SubtitleTimingSource`, `RenderedClip`, `ClipExport`, `ClipState` construct and stay
      frozen.
- [ ] 13a.4 GREEN: `domain/rendering.py` — the eight new types.
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
      crop (2160×1214) vs a 1080-wide target → `NATIVE`, factor `0.89`; a 1080p-derived crop (1080×608) vs
      the same target → `UPSCALED`, factor `1.78`.
- [ ] 13a.12 GREEN: `domain/framing.py` — `quality_of(crop, target)` module function.
- [ ] 13a.13 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 13a-ii: ASS Subtitles + Cue Building (~525 lines)

Closes: `clip-rendering` Subtitle Burn-In From Structured Transcript (both scenarios), Missing Word Timing
Is Declared Not Silently Degraded (both scenarios); threat-matrix row **ASS subtitle content injection**.
Independent of 13a-i/13a-iii; gates 13b-i and 13b-iii.

- [ ] 13a.14 RED: `tests/unit/adapters/ffmpeg/test_subtitles.py` — hostile strings (`{\an8}`, a lone `}`, a
      lone `\`, `\r\n`, a 5,000-char run) each emit a single dialogue line with no override block, `\`
      escaped, CR/LF stripped, intended breaks only as `\N`.
- [ ] 13a.15 GREEN: `adapters/ffmpeg/subtitles.py` — the escaping function + `.ass` file generation from
      `tuple[SubtitleCue, ...]`.
- [ ] 13a.16 RED: `tests/unit/usecases/test_build_subtitle_cues.py` — a multi-second `SPEECH` segment with
      `words` splits into cues at word boundaries, none exceeding `max_cue_chars`.
- [ ] 13a.17 GREEN: `usecases/build_subtitle_cues.py` — word-boundary cue splitting over segments
      overlapping the requested span.
- [ ] 13a.18 RED: word-less-segment test — a segment with `words=()` yields one cue at segment times, never
      an evenly-distributed guess.
- [ ] 13a.19 GREEN: implement the segment-level fallback branch.
- [ ] 13a.20 RED: timing-source declaration test — a clip whose every overlapping speech segment carries
      `words` returns `SubtitleTimingSource.WORD_LEVEL`; any segment lacking `words` degrades the whole
      clip to `SEGMENT_LEVEL`.
- [ ] 13a.21 GREEN: compute the declaration from the actual segments in range, not from
      `capabilities().word_timing`.
- [ ] 13a.22 REFACTOR: suite green, `mypy src tests` clean.

---

## Slice 13a-iii: Filter-Graph Composition + `sendcmd` Densification (~575 lines)

Closes: `clip-rendering` Single Native ffmpeg Pass, Crop Trajectory Applied As Given; threat-matrix row
**ffmpeg filter-graph composition**. Independent of 13a-i/13a-ii; gates 13b-i.

- [ ] 13a.23 RED: `tests/unit/adapters/ffmpeg/test_argv_composition.py` — the render argv contains one
      `-filter_complex` chaining `sendcmd`→`crop`→`scale`→`subtitles`, `-ss` before `-i`, absolute
      source/dest paths, and exactly one ffmpeg invocation per render.
- [ ] 13a.24 GREEN: `adapters/ffmpeg/argv.py` — `build_render_argv()` extending the shipped
      prefix/containment helpers.
- [ ] 13a.25 RED: bare-relative-filename test — the composed graph references `<clip_id>.cmds`/`.ass` by
      bare filename, never an absolute path, regardless of a job-directory path containing `:`, `'`, `,`,
      or `\`.
- [ ] 13a.26 GREEN: confirm the graph composer never interpolates the job-directory path into the filter
      string.
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
      process via an injected `RenderProcessRunner` with `cwd` set to the job directory; argv matches
      `build_render_argv()`'s output.
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

Closes: `clip-rendering` Clip Export to Job Directory (both scenarios). Independent of every other
13a/13b unit.

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
half). Depends on 13a-i, 13a-ii, 13b-i, 13b-ii.

- [ ] 13b.18 RED: `tests/unit/runtime/test_render_worker.py` — the happy path calls `probe`→`detect`→
      `build_trajectory`→`load_transcript`→`build_subtitle_cues`→`render`→`quality_of` in order, against
      fakes, and writes a `RENDERED` `ClipExport`.
- [ ] 13b.19 GREEN: `runtime/render_worker.py` — headless entrypoint `python -m
      onevoicecut.runtime.render_worker --job-id <id> --clip-id <id>`.
- [ ] 13b.20 RED: frame-geometry-refusal test — when `probe.frame is None`, the worker writes a `FAILED`
      `ClipExport` naming `FrameGeometryUnavailable` and never calls the tracker.
- [ ] 13b.21 GREEN: implement the first `alt` branch from the design's sequence diagram.
- [ ] 13b.22 RED: tracking-unavailable-refusal test — when `capabilities().detection != AVAILABLE`, the
      worker writes `FAILED(TrackingUnavailable)` naming remediation, and never calls `detect()`.
- [ ] 13b.23 GREEN: implement the second `alt` branch.
- [ ] 13b.24 RED: low-confidence-propagation test — a `LOW_CONFIDENCE` trajectory produces a
      `RenderedClip.tracking` that is not silently reported as ordinary success.
- [ ] 13b.25 GREEN: propagate `TrackingConfidence` from `build_trajectory`'s output onto the assembled
      `RenderedClip`.
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
      tracking}` read-only; a test enforces it writes nothing.
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
**vision adapter decode**. Depends only on 12a-ii's port; independent of the entire 13b track.

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
