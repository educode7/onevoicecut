# Proposal: Video Transcription Pipeline

> Phase: `sdd-propose` · Artifact store: hybrid (mirror of Engram `sdd/video-transcription-pipeline/proposal`)
> Inputs: `exploration.md`, Engram 637/639/640/641. Binding user decisions are marked **[BINDING]**.

## Intent

The user produces high-impact short-form social video. The raw material for that is what was said in
existing footage, and today extracting it is manual: watch the video, take notes, retype quotes, guess
where the good moments are. Nothing exists on disk — this is a greenfield project.

The outcome wanted is not "a transcript". It is: drop a video in, get back a summary plus a shortlist
of **clip-worthy moments with timestamps that point back into the source footage**, each with a short
script, so the next step (cutting and producing the clip) starts from evidence instead of memory.
The transcript is the intermediate representation that makes that possible.

## Scope

### In Scope

- **Local web UI with HTTP upload** as the ingest path **[BINDING]**, plus **asynchronous job handling**:
  job creation, status/progress polling, terminal success/failure. A multi-hour transcription MUST NOT
  block an HTTP request. Upload size limits and request timeouts are in scope, not deferred.
- **Hexagonal decomposition** with five ports (see Approach). ASR is a port with **two interchangeable
  adapters — one local engine, one cloud API engine** **[BINDING]**.
- **Structured transcript** — segments with start/end timestamps — as the internal domain object;
  plain `.txt` is one **export** of it **[BINDING]**. Timestamps MUST NOT be discarded at the ASR boundary.
- **Long-audio handling in the use-case layer, not inside adapters**: chunking (forced by a 25MB
  per-request cap on at least one cloud provider), overlap handling when stitching chunk boundaries,
  and map-reduce summarization for transcripts exceeding practical LLM context.
- **Generation output contract** **[BINDING]**: summary + list of candidate clip moments (each carrying
  source timestamps and a short script), designed so **N script variants** (per network/format) is a
  natural shape, not a later bolt-on.
- **Bootstrapping** (nothing exists): dependency manager = **venv + pip + `requirements.txt`** (skill
  default — no `uv.lock`/`pyproject.toml`/`Pipfile`/`environment.yml` present); test runner = **pytest**
  recorded as `test_command` in `openspec/config.yaml`; **ffmpeg as a system binary** with its own
  install/verify step — it is NOT a pip dependency.

### Out of Scope (explicit non-goals)

- Rendering, assembling, or publishing video. **The script/summary artifact is the stopping point** and
  the seam for a future change **[BINDING]**.
- AI avatar generation (HeyGen/Synthesia), programmatic rendering (Remotion/Hyperframes), AI footage
  generation (Veo/Sora/Runway).
- Publishing or scheduling to social networks.
- Real-time / streaming transcription.
- Multi-user authentication, hosted storage, remote access. This is a single-operator local app.
- Speaker diarization (see Open Questions — not assumed).

## Capabilities

### New Capabilities

- `project-bootstrap`: dependency manager, pytest + marker policy, `test_command`, ffmpeg system-binary install/verify.
- `media-ingest`: local web UI HTTP upload, accepted containers, size limits, timeout behavior.
- `transcription-jobs`: async job lifecycle — queued/running/succeeded/failed, status and progress, failure surfacing.
- `audio-extraction`: video container to normalized audio track via ffmpeg adapter.
- `speech-transcription`: `TranscriptionPort` contract, two adapters, chunking, overlap stitching, timestamp preservation.
- `transcript-artifacts`: structured `Transcript`/`TranscriptSegment`, storage, `.txt` export.
- `script-generation`: summary + timestamped clip candidates + N script variants, map-reduce over long transcripts.

### Modified Capabilities

None — `openspec/specs/` is empty.

## Approach

Hexagonal architecture. The swappability requirement is the reason for the shape, not decoration.

| Port | Contract intent |
| --- | --- |
| `MediaSourcePort` | Accept an uploaded media stream, persist it, return a stable media reference. Hides HTTP entirely from the core. |
| `AudioExtractorPort` | `SourceMedia` → normalized `AudioTrack` (codec/sample rate the ASR side expects). ffmpeg lives behind this and nowhere else. |
| `TranscriptionPort` | `AudioTrack` (or chunk) → segments with **start/end timestamps** and text. Provider-neutral: no engine-specific fields. Two adapters MUST satisfy it identically. |
| `TextGenerationPort` | Prompt/context → text completion. Provider-neutral. Knows nothing about summaries, clips, or chunking. |
| `TranscriptStoragePort` | Persist and retrieve `Transcript` and generated artifacts by job id. |

Chunking, overlap stitching, and map-reduce summarization live in **use cases** above the ports, so
swapping an ASR engine or LLM provider never forces rewriting them.

**Strict TDD satisfaction**: the default fast suite drives domain and use cases against fakes/stubs
behind every port — the direct payoff of the boundary. Real-engine adapters get a small set of contract
tests, **marked and excluded from the default run** (e.g. `pytest -m "not integration"`), so **no test
invokes a paid API or a real local model by default**. The same contract test body runs against both
ASR adapters to prove interchangeability.

## Affected Areas

| Area | Impact | Description |
| --- | --- | --- |
| `requirements.txt`, `.venv/` | New | venv + pip per skill default |
| `pytest.ini` / `pyproject.toml` | New | pytest config, `integration` marker registration |
| `openspec/config.yaml` | Modified | Fill `test_command`, `build_command` (currently `""`) |
| `src/transcribe/domain/` | New | `SourceMedia`, `AudioTrack`, `Transcript`, `TranscriptSegment`, `ClipCandidate`, `ScriptVariant` |
| `src/transcribe/ports/` | New | Five port protocols |
| `src/transcribe/usecases/` | New | Ingest, chunk/transcribe/stitch, map-reduce generate |
| `src/transcribe/adapters/` | New | web/upload, ffmpeg, local ASR, cloud ASR, LLM, filesystem storage |
| `tests/` | New | Fast unit suite (fakes) + marked adapter contract tests |
| `README.md` | New | ffmpeg install step, run instructions |

## Size Forecast (400-line review budget)

**400-line budget risk: High. This does NOT fit in one 400-line review.** Honest estimate: greenfield
bootstrap + web layer + async jobs + two ASR adapters + LLM adapter + test harness ≈ **1,600–1,900
changed lines**. Recommend a **chained/stacked PR sequence of five slices**:

| # | Slice | Est. lines | Proves |
| --- | --- | --- | --- |
| 1 | Bootstrap + walking skeleton: deps, pytest, `test_command`, domain entities, all five port protocols, fakes, `TranscribeMedia` use case, `.txt` export | ~350 | Real file in → real `.txt` out through every layer, fake ASR. Thinnest end-to-end proof. |
| 2 | ffmpeg `AudioExtractorPort` adapter + install/verify step + fixture-based contract test | ~200 | Real audio extraction |
| 3 | Local web UI upload + async job handling + status/progress + size/timeout limits | ~350 | The **[BINDING]** ingest decision |
| 4 | Local ASR adapter + cloud ASR adapter + chunking/overlap stitching use case | ~450 | Interchangeability + long audio |
| 5 | `TextGenerationPort` adapter + map-reduce + summary/clip-candidates/N-variant output | ~400 | The actual product outcome |

Slices 4 and 5 remain above budget and may need further splitting at `sdd-tasks` time.
`delivery_strategy: ask-on-risk` → **a delivery decision is required before apply.**

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| ~~No git repository exists~~ — **RESOLVED after this proposal was written.** `git init -b main` was run and a `.gitignore` was added. Nothing is committed yet. | Resolved | Remaining prerequisite for slice 1: an initial commit, so per-slice rollback is `git revert`. |
| Total work far exceeds the 400-line budget | High | Five chained slices above; confirm strategy before apply |
| ffmpeg missing/not on PATH at runtime | Med | Explicit install step + startup verification with an actionable error |
| Async job handling underestimated (crash recovery, orphaned jobs, concurrent uploads) | Med | Confine to slice 3 with explicit failure-state requirements in spec |
| Long-form ASR quality varies sharply by provider (~7% vs ~43.8% WER in one 2026 benchmark) | Med | Two adapters behind one port; evaluate on real long-form content, not short clips |
| Cloud ASR/LLM per-minute cost leaking into CI or default test runs | Med | Marked integration tests excluded from default suite |
| Scope creep toward actual video generation | Med | Non-goal stated explicitly above |
| Chunk-boundary word loss when stitching | Med | Overlap handling is an explicit spec requirement, not adapter discretion |

## Rollback Plan

1. **Prerequisite**: the repository now exists (`git init -b main`, `.gitignore` added). The remaining
   prerequisite before slice 1 is an **initial commit**, so rollback is `git revert <slice>` or
   `git reset --hard`.
2. Per-slice rollback: each slice is additive. Revert its commit; earlier slices keep passing because
   every slice ends with a green default suite.
4. ffmpeg is a system binary installed outside the project; uninstalling it is optional and independent.
5. No external state is mutated: no database, no remote service writes, no published artifacts.

## Dependencies

- **ffmpeg** system binary (not pip) — required before slice 2.
- Python 3 + venv + pip.
- A cloud ASR API key (slice 4) and an LLM API key (slice 5) — required only for marked integration
  tests and real runs, never for the default suite.
- `git init` — recommended prerequisite (see Rollback).

## Open Questions & Stated Assumptions

Enumerated deliberately; none of these are silently assumed by this proposal.

| # | Question | Assumption used pending an answer |
| --- | --- | --- |
| 1 | Source audio language(s)? Does multi-language / code-switching matter? | Spanish-primary, single language per file. Affects ASR model size and provider choice. |
| 2 | Is speaker diarization needed (interviews, podcasts, multiple voices)? | **No** — listed as a non-goal. Both port and cloud adapters can support it later; the `Transcript` model reserves an optional speaker field so adding it is not a rewrite. |
| 3 | Which social networks/formats matter for script variants? | Unknown. Output contract is designed for **N variants** so the answer changes data, not structure. |
| 4 | Typical and worst-case video duration? | Assumed typical 10–60 min, worst case multi-hour. This drives chunking, job timeouts, and whether local ASR is viable on the user's hardware. |
| 5 | Where do transcripts and outputs live between steps? | Assumed local filesystem under a per-job directory behind `TranscriptStoragePort`. No database. |
| 6 | Is retention/cleanup of uploaded video required? | Assumed no automatic deletion; uploads persist locally until removed manually. |
| 7 | Local vs cloud ASR as the **default** adapter for first real runs? | Assumed cloud for slice 4 verification (faster to prove), local as the cost-free default afterward. |

## Success Criteria

- [ ] `openspec/config.yaml` has a real, runnable `test_command`; `strict_tdd` is actually enforceable.
- [ ] A video uploaded through the local web UI produces a `.txt` transcript without blocking the HTTP request.
- [ ] Job status is observable from submission to terminal state, including failure.
- [ ] The same use-case tests pass against both ASR adapters, swapped by configuration only.
- [ ] Transcript segments retain start/end timestamps end to end, and clip candidates reference them.
- [ ] A multi-hour transcript produces a summary via map-reduce without exceeding LLM context.
- [ ] The generation output can carry more than one script variant without a structural change.
- [ ] The default `pytest` run invokes no paid API and no real local model.
