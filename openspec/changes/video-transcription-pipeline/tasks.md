# Tasks: Video Transcription Pipeline

> Phase: `sdd-tasks` · Artifact store: hybrid (mirror of Engram `sdd/video-transcription-pipeline/tasks`)
> Inputs: `proposal.md` rev 2, `design.md`, all seven `specs/*/spec.md`, `openspec/config.yaml`.
> **Deviation note**: this document exceeds the 530-word task-artifact budget. Ten slices, each expressed
> as explicit RED-before-GREEN pairs (Strict TDD, no exceptions), each task naming its file and the spec
> scenario it closes, plus a 400-line review-budget split and explicit open-question tracking, cannot
> compress below roughly 3.5–4.5k words without turning tasks back into vague prose the skill itself
> forbids ("no vague tasks like 'implement feature'"). Same tradeoff `design.md` already made and stated.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~3,110 (upper end of proposal's ~2,600–3,100 range; slice 10 split adds no new work, just a cut) |
| 400-line budget risk | High |
| Chained PRs recommended | Yes |
| Suggested split | 11 work units, PR 1 → PR 11 (slice 10 split into 10a/10b) |
| Delivery strategy | ask-on-risk |
| Chain strategy | pending — user decision required |

```text
Decision needed before apply: Yes
Chained PRs recommended: Yes
Chain strategy: pending
400-line budget risk: High
```

**Individually over budget**: Slice 10 (~450) — split into 10a (~230, map-reduce summarization) and 10b
(~220, clip candidates + N variants). **At the ceiling, no headroom**: Slice 1 (~380) and Slice 4 (~400)
— any scope creep here (an unplanned edge case, a fixture that grows) needs a further split (e.g. Slice 1
→ bootstrap-only vs domain+skeleton; Slice 4 → progress/failure vs resume/timeout) before it lands.
`chain_strategy` is not yet selected — the orchestrator must collect **Stacked PRs to main** vs **Feature
Branch Chain** vs **size:exception** from the user before `sdd-apply` starts slice 1.

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|-----------------------|------------------|--------------------|
| 1 | Bootstrap + walking skeleton (deps, pytest, domain, 5 ports, fakes, `.txt` export) | PR 1 | `pytest tests/unit tests/test_architecture.py -m "not paid and not localmodel"` | N/A — no real adapter exists yet; skeleton proven fake-only | `src/transcribe/{domain,ports,usecases/ingest_media.py}`, `tests/`, `pytest.ini`, `requirements*.txt` all removable together |
| 2 | Chunk planning + overlap stitching (fake-driven) | PR 2 | `pytest tests/unit/usecases/test_plan_chunks.py tests/unit/usecases/test_stitch_transcript.py -m "not paid and not localmodel"` | N/A — pure functions, no I/O | `usecases/plan_chunks.py`, `usecases/stitch_transcript.py` |
| 3 | ffmpeg extraction + slicing | PR 3 | `pytest tests/unit/adapters/ffmpeg -m "not paid and not localmodel"` | `pytest -m integration` against the checked-in fixture (real ffmpeg, skips if absent) | `adapters/ffmpeg/`, `README.md` ffmpeg section |
| 4 | Headless job model (progress, failure, resume, timeout, worker entrypoint) | PR 4 | `pytest tests/unit/usecases/test_{transcribe_job,resume_job,progress}.py -m "not paid and not localmodel"` | `python -m transcribe.runtime.worker --job-id <fake-job>` against fakes | `usecases/{transcribe_job,resume_job,progress,purge_job_artifacts}.py`, `runtime/worker.py` |
| 5 | Web upload + status | PR 5 | `pytest tests/unit/adapters/web -m "not paid and not localmodel"` | Real HTTP client E2E test: upload → poll → `.txt`, fake engines | `adapters/web/`, `runtime/app.py`, `adapters/storage/media_source.py` |
| 6 | Per-job speaker mode + engine selection + diarization rejection | PR 6 | `pytest tests/unit/usecases/test_admit_job.py -m "not paid and not localmodel"` | Same E2E harness as unit 5, extended with a rejection-path scenario | `usecases/admit_job.py` guard clause; revertible without touching units 1–5 |
| 7 | Local ASR adapter + watchdog | PR 7 | `pytest tests/unit -m "not paid and not localmodel"` (adapter itself is `localmodel`-marked) | `pytest -m localmodel` — real `faster-whisper`, real weights, manual/opt-in | `adapters/asr/local/`, `runtime/supervisor.py` watchdog |
| 8 | Cloud ASR adapter + real byte cap + split-retry | PR 8 | `pytest tests/unit -m "not paid and not localmodel"` (adapter itself is `paid`-marked) | `pytest -m paid` — real API key, real billed call, manual/opt-in | `adapters/asr/cloud/` |
| 9 | Opt-in diarization (both adapters) + `SpeakerResolver` seam | PR 9 | `pytest tests/unit -m "not paid and not localmodel"` | `pytest -m "localmodel or paid"` for the real diarization paths | `adapters/asr/{local,cloud}/` diarization branch, `usecases/stitch_transcript.py` resolver seam |
| 10a | Map-reduce summarization | PR 10a | `pytest tests/unit/usecases/test_generate_artifacts_map.py -m "not paid and not localmodel"` | `pytest -m paid` for a real LLM call | `usecases/generate_artifacts.py` MAP/REDUCE |
| 10b | Clip candidates + N script variants | PR 10b | `pytest tests/unit/usecases/test_generate_artifacts_variants.py -m "not paid and not localmodel"` | `pytest -m paid` for a real LLM call | `usecases/generate_artifacts.py` candidate/variant phase |

## Open-Question Tracking (answerable later, none block earlier slices)

| Question | Where it lands | Blocking behavior |
|---|---|---|
| Q3 — script-variant target networks/formats | Task 10.16 | Ships with `settings.script_targets = ["generic"]`; answering Q3 edits config only, reopens nothing |
| Q5 — storage location | Slices 4/5/9 filesystem `TranscriptStoragePort` adapter | Assumption baked in per design; a different answer changes the adapter + one setting, no task here reopens |
| Q6 — retention/cleanup | Task 4.21 | Ships an unused `PurgeJobArtifacts` seam; answering Q6 wires a trigger, doesn't restructure |
| New — cross-chunk speaker identity | Task 9.8 | Ships a no-op `SpeakerResolver` seam + namespaced `cNN/SNN` labels; real re-identification is future work pending a product decision |
| New — concurrency (one job at a time) | Slice 4/7 supervisor | Assumption, not blocking; a second job needs a supervisor semaphore change only |

## Slice 1: Bootstrap + Walking Skeleton (~380 lines)

Closes: `project-bootstrap` Dependency Manager Selection, Test Runner Configuration; `transcript-artifacts`
Plain-Text Export; `speech-transcription` TranscriptionPort Contract (fake path); `speech-transcription`
Capability Declaration (type-level).

- [x] 1.1 Create `.venv`; write `requirements.txt` (fastapi, uvicorn, pydantic, pydantic-settings, httpx,
      pinned `==`), `requirements-dev.txt` (pytest, pytest-asyncio, mypy), `requirements-local-asr.txt`,
      `requirements-diarization.txt` (both empty placeholders); install dev+core into `.venv`.
- [x] 1.2 Create `pytest.ini` with `--strict-markers`; register `integration`, `localmodel`, `paid` markers.
- [x] 1.3 Record the exact `test_command`/`build_command` values (below) for `sdd-apply` to write into
      `openspec/config.yaml` — this task does NOT edit the file.
      `test_command: .venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"`
      `build_command: .venv\Scripts\python.exe -m mypy src tests`
- [x] 1.4 Add mypy config targeting `src` and `tests`.
- [x] 1.5 RED: `tests/unit/test_bootstrap.py::test_placeholder` — deliberately failing assertion, proves
      the runner executes and fails visibly.
- [x] 1.6 GREEN: fix the placeholder; confirm the suite is green and `mypy src tests` passes on empty `src/`.
- [x] 1.7 RED: `tests/unit/domain/test_ids.py` — `JobId`/`MediaId` reject a string failing
      `^[0-9A-HJKMNP-TV-Z]{26}$`, accept a valid ULID.
- [x] 1.8 GREEN: `domain/ids.py` — `NewType` ids + regex validator.
- [x] 1.9 RED: `tests/unit/domain/test_{media,jobs,chunking,transcript,generation}.py` — construct every
      dataclass from the design's Domain Model table, assert `FrozenInstanceError` on mutation.
- [x] 1.10 GREEN: `domain/{media,jobs,chunking,transcript,generation,errors}.py` — all entities, `SpeakerMode`,
      `EngineChoice`, job/chunk state enums, domain error types.
- [x] 1.11 RED: `tests/unit/ports/test_capabilities.py` — `TranscriptionCapabilities` field shape,
      `DiarizationSupport` has exactly `UNSUPPORTED`/`REQUIRES_SETUP`/`AVAILABLE`.
- [x] 1.12 GREEN: `ports/{media_source,audio_extractor,transcription,text_generation,transcript_storage,
      capabilities}.py` — five `Protocol`s, no `@runtime_checkable`.
- [x] 1.13 RED: `tests/unit/usecases/test_ingest_media_walking_skeleton.py` — fake `MediaSourcePort`,
      `AudioExtractorPort` (one chunk), `TranscriptionPort` (fixed segments, `diarization=UNSUPPORTED`),
      `TranscriptStoragePort` → real `Transcript` + `.txt` file end to end.
- [x] 1.14 GREEN: `tests/fakes/*` + `usecases/ingest_media.py` orchestrating the four sync ports.
- [x] 1.15 RED: `tests/test_architecture.py` seeded with a throwaway forbidden import in a fixture module,
      proves the assertion fires.
- [x] 1.16 GREEN: land the `ast` walker asserting `domain`/`usecases`/`ports` never import
      `adapters`/`runtime`; remove the throwaway fixture.
- [x] 1.17 REFACTOR: extract shared fake-construction helpers into `tests/fakes/__init__.py`; suite green.

## Slice 2: Chunk Planning + Overlap Stitching (~300 lines)

Closes: `speech-transcription` Chunk Planning, Overlap Stitching, Cloud Adapter Request-Size Handling
(planning half only — real cap value lands slice 8).

- [ ] 2.1 RED: `tests/unit/usecases/test_plan_chunks.py` — stride/overlap arithmetic against
      `target_chunk_seconds=600, overlap_s=5.0` fixture durations.
- [ ] 2.2 GREEN: `usecases/plan_chunks.py` — `stride_s`/chunk-bounds formula from design.
- [ ] 2.3 RED: byte-cap test — fake `TranscriptionCapabilities(max_chunk_bytes=25_000_000)` drives
      `cap_s` derivation and stride selection.
- [ ] 2.4 GREEN: extend planner with `bytes_per_second`/`cap_s` bridging logic (0.9 headroom factor).
- [ ] 2.5 RED: tail-merge test — trailing chunk `< min_chunk_seconds` (30s) merges into predecessor.
- [ ] 2.6 GREEN: implement tail merge.
- [ ] 2.7 RED: `tests/unit/usecases/test_stitch_transcript.py` — matched suffix/prefix overlap (≥4 tokens,
      accents preserved) cuts once, no duplication or loss.
- [ ] 2.8 GREEN: `usecases/stitch_transcript.py` — tokenize/match/cut per design algorithm.
- [ ] 2.9 RED: no-match fallback — overlap cuts at the snapped midpoint, never loses more than `overlap_s`.
- [ ] 2.10 GREEN: implement fallback branch.
- [ ] 2.11 RED: straddling-segment test — a segment crossing the cut truncates, drops if empty, never
      duplicated.
- [ ] 2.12 GREEN: implement truncation/drop.
- [ ] 2.13 REFACTOR: consolidate the tokenizer helper shared by matcher and fallback; suite green.

## Slice 3: ffmpeg Extraction + Slicing (~250 lines)

Closes: `audio-extraction` Video to Normalized Audio, Chunk Slicing, ffmpeg Runtime Availability Check;
`project-bootstrap` ffmpeg Declared as a System Dependency; threat-matrix row **ffmpeg subprocess argv**.

- [ ] 3.1 RED: `tests/unit/adapters/ffmpeg/test_argv_composition.py` — hostile filenames (`;`, `--`,
      leading `-`, spaces) produce list-form argv, never a shell string.
- [ ] 3.2 GREEN: `adapters/ffmpeg/extractor.py` — `probe`/`extract`/`slice` via `subprocess.run([...])`,
      never `shell=True`.
- [ ] 3.3 RED: path-outside-job-dir test — a resolved path escaping the job directory is rejected before spawn.
- [ ] 3.4 GREEN: add `Path.resolve()` containment check before every ffmpeg invocation.
- [ ] 3.5 RED: `integration`-marked test against a tiny checked-in fixture — extraction produces a
      16kHz mono FLAC `AudioTrack`; skips via `ffmpeg_available` fixture when ffmpeg is absent.
- [ ] 3.6 GREEN: wire the real ffmpeg command (`-nostdin -protocol_whitelist file`, explicit timeout).
- [ ] 3.7 RED: `integration`-marked slicing test — an N-chunk plan produces N `AudioChunk`s matching
      boundaries/overlap.
- [ ] 3.8 GREEN: implement `slice()` against the plan.
- [ ] 3.9 RED: ffmpeg-missing-from-PATH test — actionable error naming ffmpeg, not a raw subprocess exception.
- [ ] 3.10 GREEN: PATH check at first use; raise `FfmpegUnavailable` with remediation text.
- [ ] 3.11 Update `README.md` with ffmpeg install as a step distinct from `pip install -r requirements.txt`.
- [ ] 3.12 REFACTOR: share the subprocess-invocation helper between `probe`/`extract`/`slice`; suite green.

## Slice 4: Headless Job Model (~400 lines)

Closes: `transcription-jobs` all six requirements (compat-gate itself is slice 6); `transcript-artifacts`
Intermediate Chunk Result Persistence, Retention Is Unbounded (seam only).

- [ ] 4.1 RED: `tests/unit/usecases/test_transcribe_job.py` — job runs against fake ports through all
      chunks, `JobRecord` transitions `PENDING→…→COMPLETED`.
- [ ] 4.2 GREEN: `usecases/transcribe_job.py` orchestrating plan → slice → transcribe → persist per chunk.
- [ ] 4.3 RED: `tests/unit/usecases/test_progress.py` — progress derived from `results/` listing vs
      `ChunkPlan`, never a mutable counter; ETA `None` until first chunk done.
- [ ] 4.4 GREEN: `usecases/progress.py` — pure derivation function.
- [ ] 4.5 RED: chunk-84-of-87 failure test — chunks 1-83 remain persisted/intact, job record not terminated.
- [ ] 4.6 GREEN: per-chunk error isolation in `transcribe_job.py`; `ChunkResult(state=FAILED)` recorded.
- [ ] 4.7 RED: `tests/unit/usecases/test_resume_job.py` — resume after a simulated crash continues at the
      first `!= DONE` chunk; completed chunks untouched.
- [ ] 4.8 GREEN: `usecases/resume_job.py` — work-set = chunks where state != DONE.
- [ ] 4.9 RED: transient-cloud-error retry test — only the failed chunk retries.
- [ ] 4.10 GREEN: bounded per-chunk retry in `transcribe_job.py`.
- [ ] 4.11 RED: per-chunk timeout test — a timed-out chunk marks `FAILED(TIMEOUT)`, job continues; a
      3-hour job within per-chunk timeouts is never terminated on elapsed time alone.
- [ ] 4.12 GREEN: `TranscriptionRequest.timeout_s` honored in-call (watchdog stub; real watchdog is slice 7).
- [ ] 4.13 RED: job-record propagation test — `engine_choice=cloud, speaker_mode=multi-speaker` resolves
      to the matching fake adapter and a diarized request.
- [ ] 4.14 GREEN: `runtime/engine_resolver.py` stub (fakes only this slice) + propagation wiring.
- [ ] 4.15 RED: atomic chunk-write test — a simulated crash between `.tmp` write and `os.replace` leaves
      the loader unaffected by a stale `.tmp`.
- [ ] 4.16 GREEN: `save_chunk_result` — `os.replace`, fsync-before-replace, stale `.tmp` ignored by loader.
- [ ] 4.17 RED: single-writer test — only the worker writes `job.json`; `control.json` cancellation flag
      is polled at chunk boundaries.
- [ ] 4.18 GREEN: filesystem `TranscriptStoragePort` job/chunk persistence methods used by this slice.
- [ ] 4.19 RED+GREEN: headless entrypoint `python -m transcribe.runtime.worker --job-id <id>` proven by
      an E2E-style test — real filesystem, fake engines.
- [ ] 4.20 REFACTOR: extract the chunk-loop state machine shared by `transcribe_job`/`resume_job`; suite green.
- [ ] 4.21 GREEN: add an unused `usecases/purge_job_artifacts.py` seam (`PurgeJobArtifacts(job_id, keep)`),
      no caller wired. **Answers Q6 later** — retention policy wires a trigger to this seam, no restructure.

## Slice 5: Web Upload + Status (~330 lines)

Closes: `media-ingest` Non-Blocking Upload Acceptance, Upload Size Limit; threat-matrix rows
**Uploaded-file classification**, **HTTP routing/path params**, **Resource exhaustion at ingest**.

- [ ] 5.1 RED: `tests/unit/adapters/web/test_admit_job_route.py` — `POST /api/jobs` with valid JSON
      returns `201 {job_id}`; missing engine returns `422`.
- [ ] 5.2 GREEN: FastAPI app skeleton, `POST /api/jobs` route + Pydantic schema, `AdmitJob` wiring
      (single-speaker/no-diarization path only — MI5 rejection lands slice 6).
- [ ] 5.3 RED: `PUT /api/jobs/{id}/media` streams raw bytes to disk with constant memory (assert no
      `UploadFile`/multipart path exists).
- [ ] 5.4 GREEN: `adapters/web/routers/jobs.py` — `async for part in request.stream()` writer;
      `adapters/storage/media_source.py`.
- [ ] 5.5 RED: oversized-upload test — `Content-Length` precheck rejects before any bytes read; a lying
      header caught by a running byte counter aborts and deletes the partial file.
- [ ] 5.6 GREEN: implement both checks against `TRANSCRIBE_MAX_UPLOAD_BYTES`.
- [ ] 5.7 RED: hostile-filename test — `../../etc/passwd`-style filename never becomes a path component;
      stored path stays inside the job dir.
- [ ] 5.8 GREEN: filename treated as metadata only; storage path is `jobs/{ulid}/source{ext}` from a
      container allowlist.
- [ ] 5.9 RED: non-media-content-with-media-extension test — `ffprobe`-based validation rejects it, not
      the extension.
- [ ] 5.10 GREEN: `probe()` call in the ingest path raises `UnsupportedContainer`.
- [ ] 5.11 RED: `job_id` path-traversal test per route (`..`, `/`, URL-encoded separators) rejected
      before filesystem access.
- [ ] 5.12 GREEN: route-level regex validation reusing `domain/ids.py`.
- [ ] 5.13 RED: `GET /api/jobs/{id}` status test — chunk-derived progress + ETA surfaced over HTTP.
- [ ] 5.14 GREEN: status route reading `usecases/progress.py` output.
- [ ] 5.15 GREEN: wire `runtime/app.py` lifespan — spawn `Supervisor`, startup reconciliation
      (`TRANSCRIBING` + no live PID → `INTERRUPTED`), verify ffmpeg on startup.
- [ ] 5.16 RED: E2E test — real HTTP client + real filesystem + fake engines: upload → poll → `.txt`.
- [ ] 5.17 GREEN: close any wiring gap the E2E test exposes.
- [ ] 5.18 REFACTOR: extract shared Pydantic schemas into `adapters/web/schemas.py`; suite green.

## Slice 6: Per-Job Speaker Mode + Engine Selection + Diarization Rejection (~250 lines)

Closes: `media-ingest` Per-Job Speaker Mode Input, Per-Job ASR Engine Selection, Reject Incompatible
Engine/Speaker-Mode Combination at Admission (all 3 scenarios); `speech-transcription` Reject Speaker-Mode
Jobs the Adapter Cannot Satisfy (defense-in-depth half). **Moved forward from slice 9 per design refinement 1**
— every adapter already ships `DiarizationSupport.UNSUPPORTED` since slice 1, so this rejection is real
and testable now, before either real ASR adapter exists.

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
      inherit it in slices 7/8).
- [ ] 6.17 REFACTOR: extract the compatibility check into one `usecases/admit_job.py` helper reused by
      the schema-level and port-level checks; suite green.

## Slice 7: Local ASR Adapter (~250 lines)

Closes: `speech-transcription` TranscriptionPort Contract (local), Contract Parity and Declared Divergence
(local half). Real-engine work is `localmodel`-marked, excluded from the default suite.

- [ ] 7.1 RED: `localmodel`-marked contract test — real `faster-whisper` adapter satisfies the shared
      single-speaker contract body.
- [ ] 7.2 GREEN: `adapters/asr/local/faster_whisper_adapter.py` implementing `TranscriptionPort`;
      `capabilities()` still returns `DiarizationSupport.UNSUPPORTED` (diarization lands slice 9), real
      `max_chunk_bytes=None`, real `max_chunk_duration_s`.
- [ ] 7.3 RED: shared contract test parametrized to include the local adapter alongside the existing
      fake, `localmodel`-marked, excluded from the default run.
- [ ] 7.4 GREEN: register the adapter in `runtime/engine_resolver.py` for `EngineChoice.LOCAL`.
- [ ] 7.5 RED: supervisory watchdog test — no progress past `chunk_timeout_s` kills the worker process,
      chunk recorded `FAILED(TIMEOUT)`.
- [ ] 7.6 GREEN: `runtime/supervisor.py` watchdog watching `results/` mtime.
- [ ] 7.7 REFACTOR: extract adapter-construction/secret-read logic shared with the cloud adapter (slice 8)
      into a resolver helper; suite green.

## Slice 8: Cloud ASR Adapter + Real Byte Cap + Split-Retry (~250 lines)

Closes: `speech-transcription` TranscriptionPort Contract (cloud), Contract Parity and Declared Divergence
(cloud half), Cloud Adapter Request-Size Handling (real cap + `ChunkTooLarge` recovery). **Moved forward per
design refinement 2** — slice 2 already implemented the byte-cap-aware planning formula against a fake
`max_chunk_bytes=25_000_000`; this slice supplies the real value and the split-and-retry recovery only.
Real-engine work is `paid`-marked, excluded from the default suite.

- [ ] 8.1 RED: `paid`-marked contract test — real cloud adapter satisfies the shared single-speaker
      contract body.
- [ ] 8.2 GREEN: `adapters/asr/cloud/*_adapter.py` implementing `TranscriptionPort` with an HTTP client +
      in-call timeout; `capabilities()` returns real `max_chunk_bytes=25_000_000` (still
      `DiarizationSupport.UNSUPPORTED`), reads `CLOUD_ASR_API_KEY` at construction.
- [ ] 8.3 GREEN: register in `engine_resolver.py` for `EngineChoice.CLOUD`.
- [ ] 8.4 RED: within-limit test — a plan sized against the real 25MB cap never exceeds it on submission.
- [ ] 8.5 GREEN: `paid`-marked assertion confirming the slice-2 planner logic already holds against the
      real capability value.
- [ ] 8.6 RED: `ChunkTooLarge` split-and-retry test — an oversized actual chunk triggers a half-split
      re-slice instead of a failed job.
- [ ] 8.7 GREEN: `plan_chunks.py`/`transcribe_job.py` — catch `ChunkTooLarge`, split, re-slice, retry.
- [ ] 8.8 REFACTOR: unify in-call-timeout construction between local/cloud resolver branches; suite green.

## Slice 9: Opt-In Diarization (~250 lines)

Closes: `speech-transcription` Reject Speaker-Mode Jobs the Adapter Cannot Satisfy (positive path),
Contract Parity and Declared Divergence (diarization scenario). Flips capable adapters from
`UNSUPPORTED`/`REQUIRES_SETUP` to `AVAILABLE`; the rejection path itself was already proven in slice 6.

- [ ] 9.1 RED: `localmodel`-marked test — local adapter declares `AVAILABLE` when `pyannote.audio`/WhisperX
      is installed and the licence accepted, `REQUIRES_SETUP` otherwise.
- [ ] 9.2 GREEN: extend `faster_whisper_adapter.capabilities()` to probe install state; add diarization
      sub-adapter.
- [ ] 9.3 RED: diarizing-adapter-receives-multi-speaker-job test — returned segments include a speaker
      label per segment, namespaced `c{chunk_index:02d}/S{speaker:02d}`.
- [ ] 9.4 GREEN: implement the diarization call + namespaced label assignment.
- [ ] 9.5 RED: cloud diarization test (`paid`-marked) — asserts the declared divergence per provider
      (e.g. flips to `AVAILABLE`, or a Whisper-API-based adapter stays `UNSUPPORTED` and still refuses).
- [ ] 9.6 GREEN: implement or explicitly document the divergence for the chosen cloud provider.
- [ ] 9.7 RED: `SpeakerResolver` seam test — stitcher accepts a no-op default resolver passing namespaced
      labels through unchanged; a stub resolver substitutes without touching the stitching algorithm.
- [ ] 9.8 GREEN: `usecases/stitch_transcript.py` — inject `SpeakerResolver` protocol, default no-op impl.
      **Answers the new cross-chunk speaker identity question later** — this seam ships now; a real
      voice-embedding re-identification resolver is future work pending a product decision.
- [ ] 9.9 GREEN: extend slice-6's admission tests to also cover now-`AVAILABLE` engines admitting normally.
- [ ] 9.10 REFACTOR: consolidate the two adapters' capability-probing pattern; suite green.

## Slice 10a: Map-Reduce Summarization (~230 lines)

Closes: `script-generation` Map-Reduce Summarization.

- [ ] 10.1 RED: fake `TextGenerationPort`-based test — `complete()` call shape only, no summary logic yet.
- [ ] 10.2 GREEN: confirm `ports/text_generation.py` (slice 1) is sufficient; add `adapters/llm/*` fake
      wiring stub if missing.
- [ ] 10.3 RED: `tests/unit/usecases/test_generate_artifacts_map.py` — a transcript exceeding
      `map_window_tokens` windows by estimated char/4 budget, 200-token overlap, rendered with segment ids.
- [ ] 10.4 GREEN: `usecases/generate_artifacts.py` MAP phase.
- [ ] 10.5 RED: segment-id-rejection test — a model response referencing an id absent from its window is
      rejected.
- [ ] 10.6 GREEN: id-validation against the real `Transcript`.
- [ ] 10.7 RED: REDUCE test — partial summaries fold sequentially into one final summary without a single
      call exceeding practical context.
- [ ] 10.8 GREEN: REDUCE phase.
- [ ] 10.9 RED: `ContextLengthExceeded` retry test — window halves and retries.
- [ ] 10.10 GREEN: implement halving retry.
- [ ] 10.11 REFACTOR: extract token-estimation helper; suite green.

## Slice 10b: Clip Candidates + N Script Variants (~220 lines)

Closes: `script-generation` Clip Candidate Output, N Script Variants Per Clip Candidate, Scope Boundary —
No Rendering.

- [ ] 10.12 RED: clip-candidate test — candidate carries `start_s`/`end_s` mapping into the source
      transcript plus a short script.
- [ ] 10.13 GREEN: rank-by-score candidate selection, top `max_clip_candidates`.
- [ ] 10.14 RED: multiple-variants test — a candidate carries `variants: tuple[ScriptVariant, ...]`
      without a schema change when count > 1.
- [ ] 10.15 GREEN: one `complete()` call per `(candidate, target)` pair, `target` sourced from
      `settings.script_targets`.
- [ ] 10.16 GREEN: ship `settings.script_targets` defaulting to `["generic"]`. **Answers Q3 later** —
      the concrete target list is a config change only, no task here reopens.
- [ ] 10.17 RED: scope-boundary test — generation output is summary + candidates + variants only, no
      video file produced.
- [ ] 10.18 GREEN: assert `GenerationResult` shape excludes any media artifact.
- [ ] 10.19 REFACTOR: extract the prompt-template construction shared by MAP/REDUCE/variant calls; full
      default suite green end to end.
