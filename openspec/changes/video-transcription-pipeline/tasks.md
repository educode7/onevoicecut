# Tasks: Video Transcription Pipeline

> Phase: `sdd-tasks` (rev 4 — non-speech audio) · Artifact store: hybrid (mirror of Engram `sdd/video-transcription-pipeline/tasks`)
> Inputs: `proposal.md` rev 3, `design.md`, all seven `specs/*/spec.md`, `openspec/config.yaml`, and **slice 1's actual code on
> disk** (`src/transcribe/`, `tests/`) as the calibration source for this revision.
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

Read from `src/transcribe/{domain,ports,usecases}` and `tests/{unit,fakes,test_architecture.py}` on disk:

| Unit | Measured cost | Evidence |
| --- | --- | --- |
| One frozen dataclass (construction test + separate `FrozenInstanceError` test) | ~20–30 lines total (prod + test) | `domain/chunking.py` (53 lines, 4 dataclasses + 1 enum) + `test_chunking.py` (102 lines, 8 tests) ≈ 31/entity |
| One `Protocol` port method (protocol line + fake implementation line) | ~7–9 lines combined | `ports/transcript_storage.py` (39 lines / 12 methods) + `tests/fakes/transcript_storage.py` (63 lines / 12 methods) ≈ 8.5/method |
| One use-case orchestrating 3–4 ports, single scenario | ~110–115 lines (prod + test) | `usecases/ingest_media.py` (63) + `test_ingest_media_walking_skeleton.py` (49) = 112 |
| One `ast`-based architecture walker (one-time, already paid) | 59 lines | `tests/test_architecture.py` |

**The decisive finding**: every domain dataclass the design specifies (`SourceMedia`, `AudioTrack`, `MediaProbe`,
`JobRecord`, `ChunkPlan`, `PlannedChunk`, `AudioChunk`, `ChunkResult`, `TranscriptSegment`, `Transcript`,
`ClipCandidate`, `ScriptVariant`, `GenerationResult`) **and all five ports already exist on disk**, built in slice 1.
Confirmed by reading `src/transcribe/domain/generation.py` and `src/transcribe/domain/transcript.py` directly —
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
| Per-unit 400-line budget risk | **Low** — every one of the 21 remaining work units is individually estimated at 145–350 lines, with margin, not at the ceiling |
| Aggregate 400-line budget risk | **High** by construction — this is why the change stays split into 23 work units total |
| Chained PRs recommended | Yes |
| Suggested split | **23 work units**, PR 1 (slice 1, done) → PR 23 |
| Delivery strategy | auto-chain |
| Chain strategy | **stacked-to-main** (resolved this session — no longer pending) |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
400-line budget risk: Low
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
| 1 (DONE) | Bootstrap + walking skeleton | PR 1 | `pytest tests/unit tests/test_architecture.py -m "not paid and not localmodel"` | N/A — fake-only skeleton | `src/transcribe/{domain,ports,usecases/ingest_media.py}`, `tests/`, config files |
| 1b-i (DONE) | `SegmentKind` + `ClassificationSupport`: domain field and capability declaration | PR 2 | `pytest tests/unit/domain/test_transcript.py tests/unit/ports/test_capabilities.py -m "not paid and not localmodel"` | N/A — pure domain/port types | `domain/transcript.py`, `ports/capabilities.py` (+ minimal fake field) |
| 1b-ii (DONE) | Classification test doubles + speech-only message export | PR 3 | `pytest tests/unit/ports/test_transcription_classification.py tests/unit/usecases/test_ingest_media_walking_skeleton.py -m "not paid and not localmodel"` | N/A — fakes only | `tests/fakes/`, `usecases/ingest_media.py` export filter |
| 2a | Chunk planning: stride/overlap/byte-cap/tail-merge | PR 4 | `pytest tests/unit/usecases/test_plan_chunks.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/plan_chunks.py` |
| 2b | Overlap stitching: match/fallback/straddling-segment | PR 5 | `pytest tests/unit/usecases/test_stitch_transcript.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/stitch_transcript.py` |
| 3a | ffmpeg extraction: probe/extract + argv/path safety | PR 6 | `pytest tests/unit/adapters/ffmpeg/test_argv_composition.py tests/unit/adapters/ffmpeg/test_extract.py -m "not paid and not localmodel"` | `pytest -m integration` (real ffmpeg, skips if absent) | `adapters/ffmpeg/extractor.py` (probe/extract only) |
| 3b | ffmpeg slicing + PATH check + integration + README | PR 7 | `pytest tests/unit/adapters/ffmpeg/test_slice.py -m "not paid and not localmodel"` | `pytest -m integration` slicing fixture | `adapters/ffmpeg/extractor.py` (slice), `README.md` |
| 4a | Real filesystem `TranscriptStoragePort` adapter (CRUD + atomic write + single-writer) | PR 8 | `pytest tests/unit/adapters/storage/test_filesystem_transcript_storage.py -m "not paid and not localmodel"` | `pytest -m integration` — crash-simulated atomic write | `adapters/storage/filesystem_transcript_storage.py` |
| 4b | `transcribe_job` core loop: happy path/failure isolation/retry/timeout/propagation | PR 9 | `pytest tests/unit/usecases/test_transcribe_job.py -m "not paid and not localmodel"` | N/A — fakes only, no real subprocess yet | `usecases/transcribe_job.py` |
| 4c | Resume + derived progress | PR 10 | `pytest tests/unit/usecases/test_{resume_job,progress}.py -m "not paid and not localmodel"` | N/A — fakes only | `usecases/{resume_job,progress}.py` |
| 4d | Worker entrypoint + purge seam + chunk-loop refactor | PR 11 | `pytest tests/unit -m "not paid and not localmodel"` | `python -m transcribe.runtime.worker --job-id <fake-job>` against fakes | `runtime/worker.py`, `usecases/purge_job_artifacts.py` |
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

## Slice 2b: Overlap Stitching (~230 lines)

Closes: `speech-transcription` Overlap Stitching. Split from planning because the matcher/fallback/straddling
algorithm is independently testable and independently revertible from the planner — a bug in stitching should
never require reverting planning.

- [ ] 2.7 RED: `tests/unit/usecases/test_stitch_transcript.py` — matched suffix/prefix overlap (≥4 tokens,
      accents preserved) cuts once, no duplication or loss.
- [ ] 2.8 GREEN: `usecases/stitch_transcript.py` — tokenize/match/cut per design algorithm.
- [ ] 2.9 RED: no-match fallback — overlap cuts at the snapped midpoint, never loses more than `overlap_s`.
- [ ] 2.10 GREEN: implement fallback branch.
- [ ] 2.11 RED: straddling-segment test — a segment crossing the cut truncates, drops if empty, never duplicated.
- [ ] 2.12 GREEN: implement truncation/drop.
- [ ] 2.12b RED: `kind`-preservation test — stitching carries each segment's `SegmentKind` through unchanged,
      including across a cut; the fallback branch (which fires routinely on musical overlap windows) never
      relabels a segment.
- [ ] 2.13 REFACTOR: consolidate the tokenizer helper shared by matcher and fallback; suite green.

---

## Slice 3a: ffmpeg Extraction + Argv/Path Safety (~200 lines)

Closes: `audio-extraction` Video to Normalized Audio; `project-bootstrap` ffmpeg Declared as a System Dependency;
threat-matrix row **ffmpeg subprocess argv**. First real subprocess adapter in the codebase — no slice 1 comparable,
+15% uncertainty margin applied.

- [ ] 3.1 RED: `tests/unit/adapters/ffmpeg/test_argv_composition.py` — hostile filenames (`;`, `--`,
      leading `-`, spaces) produce list-form argv, never a shell string.
- [ ] 3.2 GREEN: `adapters/ffmpeg/extractor.py` — `probe`/`extract` via `subprocess.run([...])`, never `shell=True`.
- [ ] 3.3 RED: path-outside-job-dir test — a resolved path escaping the job directory is rejected before spawn.
- [ ] 3.4 GREEN: add `Path.resolve()` containment check before every ffmpeg invocation.
- [ ] 3.5 RED: `integration`-marked test against a tiny checked-in fixture — extraction produces a
      16kHz mono FLAC `AudioTrack`; skips via `ffmpeg_available` fixture when ffmpeg is absent.
- [ ] 3.6 GREEN: wire the real ffmpeg extract command (`-nostdin -protocol_whitelist file`, explicit timeout).

## Slice 3b: ffmpeg Slicing + Runtime Check + Integration Fixture (~190 lines)

Closes: `audio-extraction` Chunk Slicing, ffmpeg Runtime Availability Check. Split from 3a because slicing depends
on the chunk plan (slice 2a), while probe/extract do not — keeping them together would create an artificial
dependency the design doesn't require.

- [ ] 3.7 RED: `integration`-marked slicing test — an N-chunk plan produces N `AudioChunk`s matching
      boundaries/overlap.
- [ ] 3.8 GREEN: implement `slice()` against the plan.
- [ ] 3.9 RED: ffmpeg-missing-from-PATH test — actionable error naming ffmpeg, not a raw subprocess exception.
- [ ] 3.10 GREEN: PATH check at first use; raise `FfmpegUnavailable` with remediation text.
- [ ] 3.11 Update `README.md` with ffmpeg install as a step distinct from `pip install -r requirements.txt`.
- [ ] 3.12 REFACTOR: share the subprocess-invocation helper between `probe`/`extract`/`slice`; suite green.

---

## Slice 4a: Filesystem `TranscriptStoragePort` Adapter (~340 lines)

Closes: `transcript-artifacts` Intermediate Chunk Result Persistence, Storage Location (Q5 assumption). **Gap fix
vs rev 2**: the port has 12 methods; rev 2's task list only explicitly RED-tested the atomic-write and
single-writer behaviors, leaving 8 CRUD methods "used by this slice" with no test task. Tasks 4.0a/4.0b close that
gap. First real filesystem adapter — no slice 1 comparable (only the fake existed), +15% uncertainty margin applied.

- [ ] 4.0a **RED (new)**: `tests/unit/adapters/storage/test_filesystem_transcript_storage.py` — `create_job`/
      `load_job`/`update_job`/`list_jobs` round-trip through real JSON files on disk; `save_chunk_plan`/
      `load_chunk_plan`, `save_transcript`/`load_transcript`, `save_artifacts`, `export_text` round-trip.
- [ ] 4.0b **GREEN (new)**: `adapters/storage/filesystem_transcript_storage.py` implementing all non-atomic
      `TranscriptStoragePort` methods against `{TRANSCRIBE_DATA_DIR}/jobs/{job_id}/`.
- [ ] 4.15 RED: atomic chunk-write test — a simulated crash between `.tmp` write and `os.replace` leaves
      the loader unaffected by a stale `.tmp`.
- [ ] 4.16 GREEN: `save_chunk_result` — `os.replace`, fsync-before-replace, stale `.tmp` ignored by loader.
- [ ] 4.17 RED: single-writer test — only the worker writes `job.json`; `control.json` cancellation flag
      is polled at chunk boundaries.
- [ ] 4.18 GREEN: `control.json` read path in the filesystem adapter.

## Slice 4b: `transcribe_job` Core Loop (~350 lines)

Closes: `transcription-jobs` Asynchronous Job Lifecycle, Chunk-Level Failure Isolation, Per-Chunk Timeout, Job
Record Carries Speaker Mode and Engine Choice (propagation half). Highest RED/GREEN-pair density of any remaining
slice (5 pairs) — this is the orchestration hub; kept separate from storage (4a) and resume/progress (4c) so a bug
in retry logic never forces reverting the storage adapter or vice versa.

- [ ] 4.1 RED: `tests/unit/usecases/test_transcribe_job.py` — job runs against fake ports through all
      chunks, `JobRecord` transitions `PENDING→…→COMPLETED`.
- [ ] 4.2 GREEN: `usecases/transcribe_job.py` orchestrating plan → slice → transcribe → persist per chunk.
- [ ] 4.5 RED: chunk-84-of-87 failure test — chunks 1-83 remain persisted/intact, job record not terminated.
- [ ] 4.6 GREEN: per-chunk error isolation in `transcribe_job.py`; `ChunkResult(state=FAILED)` recorded.
- [ ] 4.9 RED: transient-cloud-error retry test — only the failed chunk retries.
- [ ] 4.10 GREEN: bounded per-chunk retry in `transcribe_job.py`.
- [ ] 4.11 RED: per-chunk timeout test — a timed-out chunk marks `FAILED(TIMEOUT)`, job continues; a
      3-hour job within per-chunk timeouts is never terminated on elapsed time alone.
- [ ] 4.12 GREEN: `TranscriptionRequest.timeout_s` honored in-call (watchdog stub; real watchdog is slice 7b).
- [ ] 4.13 RED: job-record propagation test — `engine_choice=cloud, speaker_mode=multi-speaker` resolves
      to the matching fake adapter and a diarized request.
- [ ] 4.14 GREEN: `runtime/engine_resolver.py` stub (fakes only this slice) + propagation wiring.

## Slice 4c: Resume + Derived Progress (~170 lines)

Closes: `transcription-jobs` Resume From First Incomplete Chunk, Chunk-Level Progress Reporting. Pure-derivation
and work-set-selection logic layered on top of 4a/4b — smallest remaining slice in this family, kept separate so
progress-reporting changes never touch the resume state machine.

- [ ] 4.3 RED: `tests/unit/usecases/test_progress.py` — progress derived from `results/` listing vs
      `ChunkPlan`, never a mutable counter; ETA `None` until first chunk done.
- [ ] 4.4 GREEN: `usecases/progress.py` — pure derivation function.
- [ ] 4.7 RED: `tests/unit/usecases/test_resume_job.py` — resume after a simulated crash continues at the
      first `!= DONE` chunk; completed chunks untouched.
- [ ] 4.8 GREEN: `usecases/resume_job.py` — work-set = chunks where state != DONE.

## Slice 4d: Worker Entrypoint + Purge Seam + Refactor (~160 lines)

Closes: `transcript-artifacts` Retention Is Unbounded (seam only). Wiring/cleanup slice — depends on 4a/4b/4c all
landing first.

- [ ] 4.19 RED+GREEN: headless entrypoint `python -m transcribe.runtime.worker --job-id <id>` proven by
      an E2E-style test — real filesystem, fake engines.
- [ ] 4.20 REFACTOR: extract the chunk-loop state machine shared by `transcribe_job`/`resume_job`; suite green.
- [ ] 4.21 GREEN: add an unused `usecases/purge_job_artifacts.py` seam (`PurgeJobArtifacts(job_id, keep)`),
      no caller wired. **Answers Q6 later**.

---

## Slice 5a: Job Creation + Streaming Upload (~260 lines)

Closes: `media-ingest` Non-Blocking Upload Acceptance. First real HTTP surface and first real `MediaSourcePort`
implementation in the codebase (only the fake existed) — +15% uncertainty margin applied.

- [ ] 5.1 RED: `tests/unit/adapters/web/test_admit_job_route.py` — `POST /api/jobs` with valid JSON
      returns `201 {job_id}`; missing engine returns `422`.
- [ ] 5.2 GREEN: FastAPI app skeleton, `POST /api/jobs` route + Pydantic schema, `AdmitJob` wiring
      (single-speaker/no-diarization path only — MI5 rejection lands slice 6).
- [ ] 5.3 RED: `PUT /api/jobs/{id}/media` streams raw bytes to disk with constant memory (assert no
      `UploadFile`/multipart path exists).
- [ ] 5.4 GREEN: `adapters/web/routers/jobs.py` — `async for part in request.stream()` writer;
      `adapters/storage/media_source.py` (real, replacing the fake for this path).

## Slice 5b: Upload Security Threat-Matrix (~270 lines)

Closes: threat-matrix rows **Uploaded-file classification**, **HTTP routing/path params**, **Resource exhaustion
at ingest**; `media-ingest` Upload Size Limit. Every applicable adversarial row from the design's threat matrix
lands in this one slice — kept separate from 5a's happy path so a security-guard revert never removes the upload
feature itself.

- [ ] 5.5 RED: oversized-upload test — `Content-Length` precheck rejects before any bytes read; a lying
      header caught by a running byte counter aborts and deletes the partial file.
- [ ] 5.6 GREEN: implement both checks against `TRANSCRIBE_MAX_UPLOAD_BYTES`.
- [ ] 5.7 RED: hostile-filename test — `../../etc/passwd`-style filename never becomes a path component;
      stored path stays inside the job dir.
- [ ] 5.8 GREEN: filename treated as metadata only; storage path is `jobs/{ulid}/source{ext}` from a
      container allowlist.
- [ ] 5.9 RED: non-media-content-with-media-extension test — `ffprobe`-based validation rejects it, not
      the extension.
- [ ] 5.10 GREEN: `probe()` call (slice 3a) in the ingest path raises `UnsupportedContainer`.
- [ ] 5.11 RED: `job_id` path-traversal test per route (`..`, `/`, URL-encoded separators) rejected
      before filesystem access.
- [ ] 5.12 GREEN: route-level regex validation reusing `domain/ids.py`.

## Slice 5c: Status Route + App Wiring + E2E (~250 lines)

Closes: `transcription-jobs` Chunk-Level Progress Reporting (HTTP surface). Depends on 4c's progress derivation and
5a's route skeleton — this is the slice that proves the whole ingest→worker→poll loop end to end for the first
time.

- [ ] 5.13 RED: `GET /api/jobs/{id}` status test — chunk-derived progress + ETA surfaced over HTTP.
- [ ] 5.14 GREEN: status route reading `usecases/progress.py` output.
- [ ] 5.15 GREEN: wire `runtime/app.py` lifespan — spawn `Supervisor`, startup reconciliation
      (`TRANSCRIBING` + no live PID → `INTERRUPTED`), verify ffmpeg on startup.
- [ ] 5.16 RED: E2E test — real HTTP client + real filesystem + fake engines: upload → poll → `.txt`.
- [ ] 5.17 GREEN: close any wiring gap the E2E test exposes.
- [ ] 5.18 REFACTOR: extract shared Pydantic schemas into `adapters/web/schemas.py`; suite green.

---

## Slice 6: Per-Job Speaker Mode + Engine Selection + Diarization Rejection (~300 lines)

Closes: `media-ingest` Per-Job Speaker Mode Input, Per-Job ASR Engine Selection, Reject Incompatible
Engine/Speaker-Mode Combination at Admission (all 3 scenarios); `speech-transcription` Reject Speaker-Mode
Jobs the Adapter Cannot Satisfy (defense-in-depth half). **Stays a single, un-split slice per explicit
constraint** — it is almost entirely validation logic reusing existing pieces (the fake `TranscriptionPort` already
raises `DiarizationUnsupported` on `speaker_mode=MULTI`, built in slice 1), so it does not carry the adapter/I/O
tax that forced other slices to split. No uncertainty margin applied — directly comparable to slice 1's use-case
test ratio.

- [ ] 6.1 RED: speaker-mode-omitted test — defaults to single-voice.
- [ ] 6.2 GREEN: schema default + domain default.
- [ ] 6.3 RED: multi-speaker-declared test — job record stores `speaker_mode=multi-speaker`.
- [ ] 6.4 GREEN: propagate through `AdmitJob`.
- [ ] 6.5 RED: engine-not-selected test — `422`, no job created.
- [ ] 6.6 GREEN: required-field validation in the `POST /api/jobs` schema.
- [ ] 6.7 RED: engine-selected test — job record stores `engine_choice=local`.
- [ ] 6.8 GREEN: propagate through `AdmitJob`.
- [ ] 6.9 RED: incompatible-combination test — `speaker_mode=multi` against `diarization=UNSUPPORTED`
      rejects before job creation; error names the missing capability, suggests switch-engine-or-drop-mode.
- [ ] 6.10 GREEN: `AdmitJob` capability check via `engine_resolver.resolve(engine).capabilities()`.
- [ ] 6.11 RED: zero-chunks-processed test — a multi-hour fixture with an incompatible combination never
      reaches chunk dispatch; no billable/local-model call recorded by the fake.
- [ ] 6.12 GREEN: confirm rejection strictly precedes `ingest_media`/`transcribe_job` invocation.
- [ ] 6.13 RED: compatible-combination test — `diarization=AVAILABLE` + multi-speaker admits normally.
- [ ] 6.14 GREEN: confirm existing path unaffected.
- [ ] 6.15 RED: port-level defense-in-depth test — a fake `diarization=UNSUPPORTED` adapter refuses
      (names the capability) if asked to transcribe with `speaker_mode=multi`, simulating an admission bypass.
- [ ] 6.16 GREEN: guard clause at the top of every adapter's `transcribe()` (fakes now; real adapters
      inherit it in slices 7a/8a).
- [ ] 6.17 REFACTOR: extract the compatibility check into one `usecases/admit_job.py` helper reused by
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
`src/transcribe/domain/generation.py` and `src/transcribe/ports/text_generation.py`). This is the cheapest
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
