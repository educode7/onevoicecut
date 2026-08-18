# Proposal: Video Transcription Pipeline

> Phase: `sdd-propose` · Artifact store: hybrid (mirror of Engram `sdd/video-transcription-pipeline/proposal`)
> Inputs: `exploration.md`, Engram 637/639/640/641, plus the answered proposal question round (rev 2).
> **[BINDING]** = user decision, not re-openable. **[ANSWERED]** = resolved in the question round.

## Intent

The user produces high-impact short-form social video. The raw material for that is what was said in
existing footage, and today extracting it is manual: watch the video, take notes, retype quotes, guess
where the good moments are. No application code exists yet — the repository is initialized and nothing
is committed.

The outcome wanted is not "a transcript". It is: drop a video in, get back a summary plus a shortlist
of **clip-worthy moments with timestamps that point back into the source footage**, each with a short
script, so the next step (cutting and producing the clip) starts from evidence instead of memory.
The transcript is the intermediate representation that makes that possible.

**The defining operational fact: source videos are always multi-hour** **[ANSWERED]**. Multi-hour is
the normal case, not the tail. This is not a scaling concern to handle later — it is the constraint
that shapes the job model, the progress reporting, the failure semantics, and the storage design from
slice 1 onward.

## Scope

### In Scope

- **Local web UI with HTTP upload** as the ingest path **[BINDING]**, plus **asynchronous job handling**:
  job creation, chunk-level status/progress polling, terminal success/failure. A multi-hour
  transcription MUST NOT block an HTTP request. Upload size limits and request timeouts are in scope.
- **Hexagonal decomposition** with five ports (see Approach). ASR is a port with **two interchangeable
  adapters — one local engine, one cloud API engine** **[BINDING]**.
- **Per-job ASR engine selection** **[ANSWERED]**: the operator chooses local or cloud **per job**,
  because the choice is content-dependent — sensitive material goes local, the rest may go cloud.
  There is no single global default engine.
- **Per-job speaker mode** **[ANSWERED]**: default is **single voice, talking-head**. The operator may
  declare "two or more speakers" on a job, which enables diarization. See the honest capability
  asymmetry in *Diarization Reality* below.
- **Spanish-only source audio** **[ANSWERED]** — promoted from assumption to stated requirement. No
  multi-language handling, no code-switching support. This narrows model and provider selection.
- **Structured transcript** — segments with start/end timestamps — as the internal domain object;
  plain `.txt` is one **export** of it **[BINDING]**. Timestamps MUST NOT be discarded at the ASR boundary.
- **Long-audio handling as a first-class constraint** (see dedicated section), in the use-case layer,
  not inside adapters: chunk planning, overlap stitching, chunk-level progress, chunk-level failure and
  resume, intermediate chunk persistence, job timeouts, and map-reduce summarization.
- **Generation output contract** **[BINDING]**: summary + list of candidate clip moments (each carrying
  source timestamps and a short script), designed so **N script variants** (per network/format) is a
  natural shape, not a later bolt-on.
- **Bootstrapping**: dependency manager = **venv + pip + `requirements.txt`** (skill default — no
  `uv.lock`/`pyproject.toml`/`Pipfile`/`environment.yml` present); test runner = **pytest** recorded as
  `test_command` in `openspec/config.yaml`; **ffmpeg as a system binary** with its own install/verify
  step — it is NOT a pip dependency.

### Out of Scope (explicit non-goals)

- Rendering, assembling, or publishing video. **The script/summary artifact is the stopping point** and
  the seam for a future change **[BINDING]**.
- AI avatar generation (HeyGen/Synthesia), programmatic rendering (Remotion/Hyperframes), AI footage
  generation (Veo/Sora/Runway).
- Publishing or scheduling to social networks.
- Real-time / streaming transcription.
- Multi-user authentication, hosted storage, remote access. This is a single-operator local app.
- Multi-language and code-switching transcription — Spanish only **[ANSWERED]**.
- **Automatic** speaker detection. Diarization is opt-in per job, never inferred from the audio.

> Speaker diarization is **no longer a non-goal.** It moved into scope as a conditional, opt-in path.

## Long-Audio: The Driving Constraint

Because multi-hour input is normal rather than exceptional, these stop being slice-4 details and
become properties the job model must be **designed with, not retrofitted for**:

| Concern | Requirement it forces |
| --- | --- |
| **Job timeouts** | No end-to-end request timeout can bound the work. Timeouts belong per chunk, not per job. A job runs for hours by design. |
| **Progress granularity** | "running" is useless for a 3-hour job. Progress MUST be chunk-level: chunks completed / total, plus elapsed and a derived estimate. |
| **Chunk-level failure** | A failure at chunk 84 of 87 MUST NOT discard the first 83. Failure is recorded per chunk. |
| **Resume** | A job MUST be resumable from the first incomplete chunk after a crash, restart, or transient cloud error. |
| **Intermediate persistence** | Chunk results MUST be persisted as they complete, via `TranscriptStoragePort`. This is what makes progress and resume real rather than in-memory. |
| **Disk consumption** | Multi-hour source video plus extracted audio plus chunk files is a real operational cost. See Open Question 6. |

**Consequence for slice ordering**: chunking and the chunk-aware job model move *earlier*, ahead of any
real ASR adapter. The chunk planner and stitcher are pure use-case logic testable against a fake ASR
adapter, so they can and should be built and proven before either real engine exists. This is a
correction to the previous revision, which deferred chunking to slice 4 and would have forced a
retrofit of the job model.

## Diarization Reality (stated honestly — the two adapters are NOT at parity)

Opt-in diarization is cheap on one side of the port and expensive on the other. Pretending otherwise
would hide the real cost:

| Side | What enabling speaker mode actually requires |
| --- | --- |
| **Cloud** | Mostly a request flag, billed as a **paid add-on**: Deepgram ~$0.002/min on top of transcription; AssemblyAI ~$0.02/hr on top. Google STT / Chirp 3 includes diarization. **OpenAI's Whisper API does not diarize at all** — an adapter built on it cannot satisfy speaker mode. |
| **Local** | Whisper and `faster-whisper` **do not diarize**. They produce no speaker labels at any setting. Diarization locally needs an **additional component** — typically `pyannote.audio` (a separate model, extra dependencies, and a gated Hugging Face license the user must accept) or `WhisperX`, which wraps Whisper with alignment plus `pyannote`. That means extra weights to download, extra install friction, and materially more setup than the cloud flag. |

**Architectural consequence**: `TranscriptionPort` MUST support **capability declaration**. An adapter
that cannot diarize MUST reject a job requesting speaker mode with a clear error, rather than silently
returning single-speaker output that the operator would mistake for a diarized result. Silent
degradation here is the dangerous failure, because the transcript looks fine.

This is also the first genuine crack in "the two adapters satisfy the port identically". The contract
tests must therefore assert *identical behavior on the single-speaker default path*, and *declared,
tested divergence* on the diarization path.

## Capabilities

### New Capabilities

- `project-bootstrap`: dependency manager, pytest + marker policy, `test_command`, ffmpeg system-binary install/verify.
- `media-ingest`: local web UI HTTP upload, accepted containers, size limits, timeout behavior, **plus the two per-job inputs — speaker mode (default single) and ASR engine selection (local vs cloud)** — including their validation at the boundary.
- `transcription-jobs`: async job lifecycle; **chunk-level progress**, chunk-level failure, **resume**, per-chunk timeouts; **persistence of speaker mode and engine choice on the job record** and their propagation into the use case.
- `audio-extraction`: video container to normalized audio track via ffmpeg adapter, **plus chunk slicing**.
- `speech-transcription`: `TranscriptionPort` contract, two adapters, **capability declaration**, chunk planning, overlap stitching, timestamp preservation, opt-in diarization.
- `transcript-artifacts`: structured `Transcript`/`TranscriptSegment` (with optional speaker), **intermediate chunk results**, storage, `.txt` export.
- `script-generation`: summary + timestamped clip candidates + N script variants, map-reduce over long transcripts.

### Modified Capabilities

None — `openspec/specs/` is empty.

## Approach

Hexagonal architecture. The swappability requirement is the reason for the shape, not decoration.

| Port | Contract intent |
| --- | --- |
| `MediaSourcePort` | Accept an uploaded media stream, persist it, return a stable media reference. Hides HTTP entirely from the core. |
| `AudioExtractorPort` | `SourceMedia` → normalized `AudioTrack`, and `AudioTrack` + chunk plan → `AudioChunk`s. ffmpeg lives behind this and nowhere else. |
| `TranscriptionPort` | `AudioChunk` + requested speaker mode → segments with **start/end timestamps**, text, optional speaker. Provider-neutral. **Declares its capabilities** (diarization: yes/no) so the use case can reject an impossible job up front instead of degrading silently. |
| `TextGenerationPort` | Prompt/context → text completion. Provider-neutral. Knows nothing about summaries, clips, or chunking. |
| `TranscriptStoragePort` | Persist and retrieve the job record, **per-chunk intermediate results**, the assembled `Transcript`, and generated artifacts, by job id. Resume is built on this. |

Chunk planning, overlap stitching, resume orchestration, and map-reduce summarization live in **use
cases** above the ports, so swapping an ASR engine or LLM provider never forces rewriting them.
Per-job engine selection is a composition concern: the job record carries the choice, and a small
resolver hands the use case the matching adapter — the use case itself stays engine-agnostic.

**Strict TDD satisfaction**: the default fast suite drives domain and use cases against fakes/stubs
behind every port — the direct payoff of the boundary. Chunking, stitching, progress, and resume are
fully testable this way, with no real engine. Real-engine adapters get a small set of contract tests,
**marked and excluded from the default run** (e.g. `pytest -m "not integration"`), so **no test invokes
a paid API or a real local model by default**. The shared contract test body runs against both ASR
adapters on the single-speaker path; diarization behavior is asserted per declared capability.

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `requirements.txt`, `.venv/` | New | venv + pip per skill default |
| `pytest.ini` / `pyproject.toml` | New | pytest config, `integration` marker registration |
| `openspec/config.yaml` | Modified | Fill `test_command`, `build_command` (currently `""`) |
| `.gitignore` | Exists | Already excludes uploaded media, local ASR model weights, `.env` |
| `src/transcribe/domain/` | New | `SourceMedia`, `AudioTrack`, `AudioChunk`, `ChunkPlan`, `ChunkResult`, `Transcript`, `TranscriptSegment`, `SpeakerMode`, `EngineChoice`, `JobRecord`, `ClipCandidate`, `ScriptVariant` |
| `src/transcribe/ports/` | New | Five port protocols + capability declaration |
| `src/transcribe/usecases/` | New | Ingest, chunk plan/transcribe/stitch/resume, map-reduce generate |
| `src/transcribe/adapters/` | New | web/upload, ffmpeg, local ASR, cloud ASR, LLM, filesystem storage |
| `tests/` | New | Fast unit suite (fakes) + marked adapter contract tests |
| `README.md` | New | ffmpeg install step, diarization setup caveats, run instructions |

## Size Forecast (400-line review budget)

**400-line budget risk: High. This does NOT fit in one 400-line review, and it grew.**

Revised estimate: **~2,600–3,100 changed lines across 10 slices**, up from ~1,600–1,900 across 5 in
rev 1. The growth is not padding — it is the four answers:

| Added by | Est. added lines |
| --- | --- |
| Chunk-level progress reporting | ~100 |
| Chunk-level failure + resume + intermediate chunk persistence | ~250 |
| Per-chunk timeout handling | ~60 |
| Per-job speaker mode: input, validation, job-record propagation | ~120 |
| Per-job engine selection: input, validation, resolver, propagation | ~120 |
| Opt-in diarization across both adapters + capability declaration + tests | ~250 |
| Per-slice test overhead from splitting 5 slices into 10 | ~150 |

Revised chained/stacked PR sequence:

| # | Slice | Est. lines | Proves |
| --- | --- | --- | --- |
| 1 | Bootstrap + walking skeleton: deps, pytest, `test_command`, domain entities, all five port protocols, fakes, `TranscribeMedia` use case, `.txt` export | ~380 | Real file in → real `.txt` out through every layer, fake ASR. Thinnest end-to-end proof. |
| 2 | Chunk planning + overlap stitching use case, against fake ASR | ~300 | Long-audio correctness before any real engine exists |
| 3 | ffmpeg `AudioExtractorPort`: extraction + chunk slicing + install/verify + fixture contract test | ~250 | Real audio handling |
| 4 | Headless job model: chunk-level progress, chunk failure, resume, per-chunk timeouts, intermediate persistence | ~400 | The multi-hour constraint, designed in rather than retrofitted |
| 5 | Local web UI upload + size limits + job status/progress surface | ~330 | The **[BINDING]** ingest decision |
| 6 | Per-job options end to end: speaker mode + engine selection, validation, job record, resolver | ~250 | The two new **[ANSWERED]** inputs |
| 7 | Local ASR adapter + contract test | ~250 | Cost-free path |
| 8 | Cloud ASR adapter + 25MB per-request cap handling + contract test | ~250 | Interchangeability |
| 9 | Opt-in diarization across both adapters + capability declaration + rejection path | ~250 | Speaker mode, with honest asymmetry |
| 10 | `TextGenerationPort` adapter + map-reduce + summary/clip-candidates/N-variant output | ~450 | The actual product outcome |

**Slice 10 remains above budget** and should be split at `sdd-tasks` time (map-reduce summary, then
clip candidates + variants). **Slices 1 and 4 sit at the ceiling** and have no headroom for surprises.
`delivery_strategy: ask-on-risk` → **a delivery decision is required before apply.**

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| ~~No git repository exists~~ — **RESOLVED after this proposal was written.** `git init -b main` was run and a `.gitignore` was added. Nothing is committed yet. | Resolved | Remaining prerequisite for slice 1: an initial commit, so per-slice rollback is `git revert`. |
| Total work far exceeds the 400-line budget, and grew from ~1,800 to ~2,850 lines | High | Ten chained slices above; confirm strategy before apply |
| **Multi-hour as the normal case makes every long-audio defect a routine defect, not an edge case** | High | Chunking and the chunk-aware job model move to slices 2 and 4, ahead of any real engine |
| **Local diarization needs an extra component (`pyannote.audio`/`WhisperX`, extra weights, gated license)** — not parity with the cloud flag | High | Stated plainly above; capability declaration on the port; rejection instead of silent single-speaker output |
| Silent degradation: an adapter returning unlabeled output for a speaker-mode job looks like success | Med | Explicit rejection path is a spec requirement, tested in slice 9 |
| Disk consumption from multi-hour source video + extracted audio + chunk files | Med | Flagged as Open Question 6, now sharper; `.gitignore` already keeps media out of the repo |
| ffmpeg missing/not on PATH at runtime | Med | Explicit install step + startup verification with an actionable error |
| Resume correctness (partial chunk writes, duplicated work after crash) | Med | Chunk results written atomically; resume tested against fakes in slice 4 |
| Long-form ASR quality varies sharply by provider (~7% vs ~43.8% WER in one 2026 benchmark), and every job here is long-form | Med | Two adapters behind one port; evaluate on real multi-hour Spanish content, not short clips |
| Cloud ASR/LLM per-minute cost leaking into CI or default test runs — worse now, since diarization add-ons bill on top | Med | Marked integration tests excluded from default suite |
| Scope creep toward actual video generation | Med | Non-goal stated explicitly above |
| Chunk-boundary word loss when stitching | Med | Overlap handling is an explicit spec requirement, not adapter discretion |

## Rollback Plan

1. **Prerequisite**: the repository now exists (`git init -b main`, `.gitignore` added). The remaining
   prerequisite before slice 1 is an **initial commit**, so rollback is `git revert <slice>` or
   `git reset --hard`.
2. Per-slice rollback: each slice is additive. Revert its commit; earlier slices keep passing because
   every slice ends with a green default suite.
3. ffmpeg is a system binary installed outside the project; uninstalling it is optional and independent.
   The same applies to any local diarization model weights pulled in at slice 9 — they live outside the
   repository and are already `.gitignore`d.
4. No external state is mutated: no database, no remote service writes, no published artifacts.
   Rolling back a slice may leave per-job working directories and intermediate chunk files on disk;
   these are inert data, safe to delete manually.

## Dependencies

- **ffmpeg** system binary (not pip) — required before slice 3.
- Python 3 + venv + pip.
- A cloud ASR API key (slice 8) and an LLM API key (slice 10) — required only for marked integration
  tests and real runs, never for the default suite.
- **Local diarization component** (`pyannote.audio` or `WhisperX`, plus model weights and a gated
  Hugging Face license acceptance) — required only for slice 9's local speaker-mode path.
- An **initial commit** — remaining prerequisite (see Rollback).

## Open Questions & Stated Assumptions

| # | Question | Status |
| --- | --- | --- |
| 1 | Source audio language(s)? Multi-language / code-switching? | **ANSWERED — Spanish only.** No multi-language, no code-switching. Promoted to a stated requirement in scope. |
| 2 | Is speaker diarization needed? | **ANSWERED — conditional and opt-in.** Mostly one voice to camera; multi-speaker material exists but is rare. Default is single-voice/talking-head; a per-job option declares two or more speakers. No longer a non-goal. |
| 3 | Which social networks/formats matter for script variants? | **OPEN.** Output contract is designed for **N variants** so the answer changes data, not structure. Determines whether slice 10 ships one variant or four. |
| 4 | Typical and worst-case video duration? | **ANSWERED — always multi-hour.** Multi-hour is the normal case, not the tail. Supersedes the previous "typical 10–60 min" assumption and drives the whole job model. |
| 5 | Where do transcripts and outputs live between steps? | **OPEN.** Assumed local filesystem, per-job directory behind `TranscriptStoragePort`, no database. Now also has to hold intermediate chunk results. |
| 6 | Is retention/cleanup of uploaded video required? | **OPEN — and sharper now.** With multi-hour video as the normal case, each job stores a large source file plus extracted audio plus chunk files. Without a retention policy, disk consumption grows without bound. Assumed for now: no automatic deletion. This assumption is the most likely to cause a real operational problem. |
| 7 | Local vs cloud ASR as the default adapter? | **ANSWERED — no global default; selectable per job.** Content-dependent: sensitive material goes local, the rest may go cloud. Both adapters are first-class from slice 6 onward. |

## Success Criteria

- [ ] `openspec/config.yaml` has a real, runnable `test_command`; `strict_tdd` is actually enforceable.
- [ ] A multi-hour video uploaded through the local web UI produces a `.txt` transcript without blocking the HTTP request.
- [ ] Job progress is reported at chunk granularity, not as a single "running" state.
- [ ] A job interrupted mid-run resumes from the first incomplete chunk without redoing completed work.
- [ ] The operator selects the ASR engine per job, and the selection is recorded on the job record.
- [ ] The speaker-mode option defaults to single voice, and declaring two or more speakers produces speaker-labelled segments — or an explicit rejection when the selected adapter cannot diarize.
- [ ] The same use-case tests pass against both ASR adapters on the single-speaker path, swapped by configuration only.
- [ ] Transcript segments retain start/end timestamps end to end, and clip candidates reference them.
- [ ] A multi-hour transcript produces a summary via map-reduce without exceeding LLM context.
- [ ] The generation output can carry more than one script variant without a structural change.
- [ ] The default `pytest` run invokes no paid API and no real local model.
