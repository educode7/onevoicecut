# Exploration: video-transcription-pipeline

> Phase: `sdd-explore` · Artifact store: hybrid (mirror of Engram `sdd/video-transcription-pipeline/explore`, id 640)
> Status: complete — ready for `sdd-propose` pending two open questions (see end).

## Current State

Greenfield project. Only `.atl/` and `openspec/` scaffolding exist; no source code, no dependency
manifest, no tests, no README, and it is not a git repository. `openspec/config.yaml` records: tech
stack Python (unscaffolded), architecture unresolved, testing unresolved (`test_command: ""`), strict
TDD enabled globally.

Binding user decision: ASR must be a port with two interchangeable adapters (local engine + cloud API
engine). Committing to a single engine was explicitly rejected.

User's stated flow (translated from Spanish): upload video/audio -> app transcribes -> transcript goes
to an LLM -> LLM produces a summary and a script for generating a video/ad for social networks.

## Affected Areas

Greenfield — these are artifacts to be created, not existing files to be changed:

- `openspec/changes/video-transcription-pipeline/` — this change folder (proposal/spec/design/tasks).
- Future Python package layout (hexagonal: domain / use cases / ports / adapters) — not yet scaffolded.
- Dependency manifest — `requirements.txt` by default per the `managing-python-dependencies` skill,
  since no `uv.lock`, `pyproject.toml`, `Pipfile`, or `environment.yml` exists.
- `openspec/config.yaml` — `test_command` and dependency-manager rules remain unresolved and must be
  filled in by proposal/design.

## 1. Scope Boundaries and Non-Goals

Read literally, the user's flow ends at "a script for generating a video". It does not describe
rendering that video: the described steps are upload -> transcribe -> LLM summary + script, with no
step for footage assembly, avatar generation, or rendering. Actual video generation is a materially
separate toolchain (Remotion/Hyperframes for programmatic video; Veo/Sora/Runway/Kling for AI
generation; HeyGen/Synthesia for avatars), each with dependencies, cost models, and failure modes
disjoint from an ASR/LLM pipeline.

**Recommended scope boundary: stop at a structured script/summary artifact.** That artifact is the
seam a future video-generation change would consume. This change should pull in no video-rendering
dependency.

Non-goals to state explicitly in the proposal:

- Rendering or assembling video
- AI avatar generation
- Publishing or scheduling to social networks
- Real-time / streaming transcription
- Multi-user authentication (unless the upload-mechanism decision below requires it)

## 2. Pipeline Decomposition (Ports and Adapters)

Three responsibilities map cleanly to a hexagonal shape:

**Domain core** — entities/value objects: `SourceMedia`, `AudioTrack`, `Transcript` (containing
`TranscriptSegment`s), `ScriptOutput`. Use cases: `IngestMedia`, `TranscribeAudio`, `GenerateScript`.

**Ports**

| Port | Responsibility |
| --- | --- |
| `MediaSourcePort` | How the source file arrives |
| `AudioExtractorPort` | Video container -> audio track |
| `TranscriptionPort` | ASR — two adapters required by binding decision |
| `TextGenerationPort` | LLM summary/script — same swappability reasoning |
| `TranscriptStoragePort` | Persist the transcript (e.g. as `.txt`) |

**Adapters** — ffmpeg-based extractor; local-ASR adapter (e.g. faster-whisper) and cloud-ASR adapter
both implementing `TranscriptionPort`; one or more LLM-provider adapters implementing
`TextGenerationPort`; a CLI or HTTP adapter implementing `MediaSourcePort`.

This decomposition is the direct architectural answer to the user's swappability constraint, not an
incidental pattern choice: it is what makes "swap ASR engine" or "swap LLM provider" an adapter-only
change.

## 3. "Upload" Is Ambiguous — Options

| Option | Description | Pros | Cons | Effort |
| --- | --- | --- | --- | --- |
| A. CLI / local file argument | `transcribe path/to/video.mp4` | No web framework, no auth, simplest failure modes, fastest to build and test | Does not literally match "upload"; no browser UI | Low |
| B. Local web UI with HTTP upload | Small local web app (e.g. FastAPI) with an upload form | Matches "upload" literally; usable by non-technical operators; natural home for later job-status UI | Adds web framework; request size/timeout limits for multi-hour video; needs async job handling so uploads do not block on hour-long transcription | Medium |
| C. Full multi-user web service | Auth, hosted storage, job queue | Supports teams/remote use | No stated multi-user requirement; large scope increase | High |

Nothing in the request dictates one of these. The ambiguity must be resolved with the user, not
assumed silently.

## 4. ASR Landscape (both sides of the port)

**Local engines**

- `faster-whisper` — CTranslate2 reimplementation of Whisper; up to ~4x faster than the reference
  OpenAI implementation at comparable accuracy, lower memory, CPU or GPU.
- `whisper.cpp` — C++ port, useful for CPU-only / no-PyTorch environments.
- `openai-whisper` — reference PyTorch implementation, slower.

Spanish quality: Whisper large-v3 reports ~5% WER on Spanish, comparable to English. No published
file-size or duration cap for local use — bounded instead by machine memory and runtime.

**Cloud engines**

| Provider | Price signal | Notes |
| --- | --- | --- |
| OpenAI Whisper API | ~$0.006/min | **25MB per-request cap** — anything beyond a short clip requires client-side chunking |
| Deepgram Nova-3 | ~$0.0043/min batch | 45+ languages; diarization add-on ~$0.002/min |
| AssemblyAI Universal-2/3.5 | from ~$0.15/hr batch | Diarization add-on ~$0.02/hr |
| Google STT / Chirp 3 | — | 100+ languages, built-in diarization |

A 2026 benchmark reported AssemblyAI Universal-3.5 Pro at ~7.0% WER on long-form audio versus
GPT-4o Transcribe degrading to ~43.8% WER on long financial-call audio. Long-form robustness varies
sharply by provider and needs real evaluation, not short-clip benchmarks.

**Non-Python system dependency**: regardless of which `TranscriptionPort` adapter is chosen,
extracting an audio track from a video container almost certainly requires **ffmpeg as an external
binary**. pip wrappers (`imageio-ffmpeg`, `static-ffmpeg`) vendor the binary but it is still not pure
Python. This needs its own install step, separate from the Python dependency-manager workflow.

## 5. Long-Audio Realities

- Chunking is forced by cloud API limits (OpenAI's 25MB/request cap makes multi-hour chunking
  mandatory) and is advisable for local engines too, to bound memory/runtime and limit the blast
  radius of a mid-file failure.
- Chunk stitching needs overlap handling at word/sentence boundaries to avoid losing words at cut
  points.
- Timestamps/segments matter twice: for accurate chunk stitching, and for letting script generation
  reference *where* in the source footage a strong line occurs — directly relevant to the
  "high-impact video" goal.
- LLM context limits: a multi-hour transcript can run tens of thousands of words, likely exceeding a
  practical single-call context and cost budget. Map-reduce summarization (summarize chunks, then
  summarize the summaries) is the standard mitigation, and belongs in the use-case layer above
  `TextGenerationPort`, independent of the concrete LLM.
- Runtime: local transcription of long video takes real wall-clock time. If Option B or C is chosen,
  this forces asynchronous job handling — a synchronous request cannot block for an hour.

## 6. Transcript Artifact Format

The user asked for a `.txt` file. Recommendation: keep the internal `Transcript` domain object
structured (segments with start/end timestamps, text, optional speaker) and treat plain `.txt` as one
export of that richer object.

Cost of not doing this: once timestamps are dropped at the ASR boundary they cannot be recovered
downstream, and script generation for short-form clips loses the ability to point back at exact
moments in the source video. This is an open decision for the proposal/spec phase.

## 7. Testing Strategy Under Strict TDD (no runner yet)

Nothing testable exists yet. Two things must be established before any TDD cycle can run:

1. A Python dependency manager — default venv + pip, since no `uv.lock`, `pyproject.toml`, `Pipfile`,
   or `environment.yml` exists (per the `managing-python-dependencies` skill).
2. A test runner — pytest is the conventional default — recorded as `test_command` in
   `openspec/config.yaml`, currently empty.

Testability seam for ASR/LLM: because both sit behind ports, the domain and use-case layers are tested
against **fake/stub adapters** returning fixed data. This is the direct payoff of the hexagonal
boundary. Real-engine adapters get a small number of adapter-level contract tests, marked so they are
excluded from the default fast run (e.g. `pytest -m integration`), so the default suite never invokes
a real local model or a paid cloud API. The ffmpeg-based extractor is likewise a thin adapter:
unit-testable via a fake, integration-testable with a tiny checked-in fixture file.

## 8. LLM Provider Seam

Same reasoning as ASR: a `TextGenerationPort` (or two narrower ports for summary vs. script) with one
adapter per provider. Do not hardcode a provider at this stage. As a plausible starting point only,
not a decision: since the user works inside Claude Code, an Anthropic Claude adapter is a low-friction
first adapter — but the port contract must stay provider-neutral so it does not assume Claude-specific
behavior a second adapter could not satisfy. The map-reduce summarization strategy from section 5
belongs in the use-case layer above this port, so provider swaps do not force rewriting chunking.

## Recommendation

Lean toward **Option A (CLI-first)** as the starting scope: the hexagonal decomposition in section 2
means a `MediaSourcePort` HTTP adapter can be added later as a local, additive change rather than a
rewrite, and nothing in the request establishes a remote or multi-user need.

This is a recommendation for the proposal phase to confirm with the user (interactive execution mode),
not a decision made here.

## Risks

- ffmpeg is very likely a required non-Python system binary; it does not fit the pip-only dependency
  model and needs its own install and documentation step.
- No git repository exists — no version-control safety net for the first implementing change.
- Dependency manager and `test_command` are both unresolved in `openspec/config.yaml` and must be
  settled before Strict TDD can start.
- Cloud ASR/LLM providers introduce real per-minute/per-call cost and external-network failure modes
  that local-only testing must not silently depend on.
- Long-form audio behavior varies sharply by ASR provider (~7% vs ~43.8% WER between providers on
  long financial-call audio in one benchmark) — the cloud adapter choice should be evaluated on
  actual long-form content.
- Scope creep toward literal video generation is a real risk given the stated end goal; the proposal
  must state the script-output stopping point explicitly as a non-goal.

## Ready for Proposal

Yes — with two open questions to resolve with the user before or during `sdd-propose`:

1. **Upload mechanism**: CLI vs. local web UI vs. full web service (section 3).
2. **Transcript modeling**: whether the transcript stays structured (segments + timestamps) internally
   even though the delivered artifact is plain `.txt` (section 6).
