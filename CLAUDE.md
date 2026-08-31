# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-operator local app that turns multi-hour Spanish source video into a structured transcript,
then into a summary plus timestamped clip candidates with short scripts. Video rendering/publishing is
an explicit non-goal — the script artifact is the stopping point.

Two facts about the input drive nearly every design decision. Neither is an edge case:

- **Multi-hour input is the normal case.** Hence chunked processing, chunk-level progress and failure,
  resume after crash, per-chunk timeouts.
- **Music and singing are normal input.** The speaker is sometimes accompanied by a singer, sometimes
  over background music. Hence `SegmentKind` on every segment, speech-only message export, speech-only
  LLM input, and hallucination containment at the ASR adapters.

Interview mode (multiple speakers) is opt-in per job and is genuinely occasional — unlike the two above.

## Commands

Windows paths (`.venv\Scripts\`), no POSIX `bin/`.

```powershell
# Default test suite — excludes paid APIs and real model weights
.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"

# Type check (strict mypy, src + tests)
.venv\Scripts\python.exe -m mypy src tests

# Single test file / single test
.venv\Scripts\python.exe -m pytest tests/unit/domain/test_chunking.py
.venv\Scripts\python.exe -m pytest tests/unit/domain/test_chunking.py::test_name

# Install
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

The two commands above are recorded as `test_command` / `build_command` in `openspec/config.yaml`.
Both must be green before any slice is considered done.

### pytest markers

`pytest.ini` uses `--strict-markers`, so a typo'd marker is an error rather than a silently-included
paid test.

| Marker | Meaning | In default run |
| --- | --- | --- |
| *(none)* | Domain/use-case tests against fakes | Yes |
| `integration` | Real filesystem or ffmpeg subprocess — free and fast; skips when ffmpeg is absent | Yes |
| `localmodel` | Loads real ASR/diarization weights | **No** |
| `paid` | Invokes a billed cloud API | **No** |

The default run must never invoke a paid API or load real model weights. This is a success criterion,
not a preference.

### Dependencies

venv + pip, hand-pinned, deliberately split so a unit-test run never downloads PyTorch:

- `requirements.txt` — core (fastapi, uvicorn, pydantic, pydantic-settings, httpx)
- `requirements-dev.txt` — pytest, pytest-asyncio, mypy
- `requirements-local-asr.txt` — faster-whisper (slice 7, still empty)
- `requirements-diarization.txt` — pyannote.audio / WhisperX (slice 9, still empty)
- `requirements.lock.txt` — `pip freeze` of a full install, for reproduction only

ffmpeg is a **system binary**, never a pip dependency.

## Architecture

Hexagonal, with the boundary enforced by a test rather than by convention.

```
src/transcribe/
  domain/     # zero third-party imports; frozen slotted dataclasses only
  ports/      # typing.Protocol definitions; imports domain only
  usecases/   # imports domain + ports only — all orchestration lives here
  adapters/   # web/ ffmpeg/ asr/local/ asr/cloud/ llm/ storage/   (not built yet)
  runtime/    # composition root — the ONLY place adapters are constructed (not built yet)
```

`tests/test_architecture.py` walks `domain`, `usecases`, and `ports` with `ast` and fails if any of them
imports `transcribe.adapters` or `transcribe.runtime`. It parses source text rather than importing, so it
works before those packages exist. Do not weaken it.

### The five ports

| Port | Contract |
| --- | --- |
| `MediaSourcePort` | **The one async port.** Used only by the web adapter, never by the worker. |
| `AudioExtractorPort` | `probe`/`extract`/`slice`. ffmpeg lives behind this and nowhere else. |
| `TranscriptionPort` | `AudioChunk` → segments. **Returned times are chunk-local**, not absolute. Declares `capabilities()`. |
| `TextGenerationPort` | Generic `complete()`. Knows nothing about summaries, clips, or chunking. |
| `TranscriptStoragePort` | Job record, chunk plan, per-chunk results, transcript, artifacts. `save_chunk_result` MUST be atomic — resume is built on it. |

Ports are `typing.Protocol`, not ABCs: adapters satisfy them structurally, with no import from the core.

### Load-bearing decisions

These were argued in `openspec/changes/video-transcription-pipeline/design.md`. Reversing one is a design
change, not a refactor.

- **Immutability**: every domain entity is `@dataclass(frozen=True, slots=True)`.
- **Timestamps are never discarded** at the ASR boundary. `Transcript` is the source of truth; the `.txt`
  file is one export of it.
- **Progress is derived, never a counter** — computed on read by listing `results/` against the persisted
  `ChunkPlan`, so progress after a crash is correct with no recovery code. ETA is `None` until the first
  chunk completes rather than fabricated.
- **Single-writer rule**: while a worker lives it is the sole writer of `job.json`. The web process
  requests cancellation via a separate `control.json` polled at chunk boundaries.
- **One supervised worker process per job** — not a thread, not a queue.
- **Capability declaration over silent degradation**: an adapter that cannot diarize MUST reject a
  speaker-mode job (`DiarizationUnsupported`). Returning unlabeled segments for a multi-speaker job is
  the dangerous failure, because the transcript looks fine.
- **`SegmentKind` (`SPEECH | MUSIC | UNCERTAIN`) is marked, never filtered at the boundary.** Every
  segment keeps its timestamps regardless of class, so a musical range stays addressable as clip
  material; the `.txt` message export and the LLM MAP windows select `SPEECH` only. **An adapter that
  cannot classify returns `UNCERTAIN`, never `SPEECH`** — same no-silent-degradation invariant as
  diarization, on a second and independent axis. Do not infer one axis from the other.
- **Engine choice has no global default** — it is per job, resolved by `runtime/engine_resolver.py`.
  Use cases stay engine-agnostic.
- **Secrets** are read at adapter construction in the resolver, so a missing key fails fast before a
  three-hour run starts. They never enter `JobRecord`, logs, or worker argv.

### Security invariants (already specified, tested per slice)

- ffmpeg is invoked with list-form `subprocess.run([...])`, never `shell=True`, never string
  interpolation; `-nostdin`, `-protocol_whitelist file`, explicit timeout.
- Client filenames are **metadata only**, never a path component. Storage path is `jobs/{ulid}/source{ext}`.
- All paths are `Path.resolve()`-checked to be inside the job directory before any spawn.
- `job_id` is validated against the ULID regex in `domain/ids.py` before touching the filesystem.
- Content type is validated by `ffprobe`, never by extension.

## Workflow

This repo runs **Spec-Driven Development** (`openspec/`) with **strict TDD** (`strict_tdd: true`).

- `openspec/changes/video-transcription-pipeline/` holds `proposal.md`, `design.md`, seven
  `specs/*/spec.md`, and `tasks.md`. **Read `tasks.md` before implementing** — it is the ordered,
  RED-before-GREEN checklist, and it names the spec scenario each task closes.
- Every task pair is RED first: write the failing test, then the implementation. Slice 1's checklist is
  marked `[x]`; slices 2a onward are open.
- Review budget is **400 lines** per slice (`review.budget_lines`). Slice 1 overran to 1,273 lines under
  an accepted one-time exception; the rest were re-estimated from that measured cost. The measured
  ratio is tests 56% / `src` 36% / config 8% — budget accordingly, tests dominate.
- `delivery_strategy: auto-chain`, `chain_strategy: stacked-to-main`. Work lands as 23 stacked units,
  PR 1 → PR 23.
- **Measure the diff before committing a slice, not after.** Nine measured slices have overrun their
  estimate by **3.2x to 5.1x, mean ≈ 4.0x, with none under 3.2x** — a fixed multiplier, not noise. The
  excess is test code every time (test share 61–81%, never the 56% the plan assumed). Split units the
  moment the measurement exceeds 400 lines; slice 1 did not and needed an exception, every slice since
  has split instead. Treat every remaining estimate in `tasks.md` as `estimate × 4`.

### Current state

Slices 1 through 5c are complete and green: **511 tests, 0 skipped, mypy clean over 104 files**. On disk
today are `domain/`, `ports/`, the use cases (`ingest_media`, `admit_job`, `plan_chunks`,
`stitch_transcript`, `transcribe_job`, `resume_job`, plus the uncalled `purge_job_artifacts` seam),
`adapters/ffmpeg/`, `adapters/storage/`, `adapters/web/`, `runtime/` (`app`, `settings`,
`engine_resolver`, `worker`), and `tests/fakes/`. Still missing: any ASR or LLM adapter, script
generation, and the browser UI — the HTTP surface exists but nothing renders it.

The pipeline runs end to end today with a fake ASR engine — real HTTP, real filesystem, real ffmpeg:

```powershell
$env:TRANSCRIBE_DATA_DIR = ".\data"; $env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m uvicorn transcribe.runtime.app:get_app --factory
```

`POST /api/jobs` → `PUT /api/jobs/{id}/media` → the web process spawns a worker → `GET /api/jobs/{id}`
reports chunk progress → `transcript.txt` lands in the job directory. **No real ASR engine is wired**, so
a spawned worker exits 3 ("nothing usable to run"); `tests/integration/test_ingest_to_transcript.py`
drives the same path with a fake engine and gets a transcript.

Three HTTP routes exist: `POST /api/jobs` (admit), `PUT /api/jobs/{id}/media` (raw-body streaming
upload) and `GET /api/jobs/{id}` (chunk-level progress; read-only, and a test enforces that it writes
nothing). Four things about the upload path are load-bearing and easy to undo by accident:

- The filename travels **percent-encoded** in an `X-Filename` header. HTTP header values are ASCII and
  Spanish filenames are the normal case here, not an edge case.
- No `UploadFile`/`File`/`Form` is imported anywhere in `adapters/web` — a structural test enforces it,
  because an absence cannot be proven by a request.
- The upload commits by **rename** from a sibling `.part`. Writing to the destination directly truncates
  it before the first byte arrives, so a failed retry would destroy the upload that had succeeded.
- The stored source is **extensionless** (`jobs/{ulid}/source`). Content type comes from `ffprobe`, and
  the media record's `container` reads `"unverified"` only until that probe runs.

ffmpeg 9.0.1 is installed (winget, `Gyan.FFmpeg`), so the `integration`-marked tests run rather than
skip — the flag set in `adapters/ffmpeg/argv.py` is verified against the real binaries, not just argued.

Next up is **Slice 6**: per-job speaker mode and engine selection end to end, plus the diarization
rejection path — an adapter that cannot diarize must reject a speaker-mode job rather than return
unlabelled segments.

The proposal is at **rev 4**: rendering vertical clips is now in scope, which adds slices 11-13 after
10b and modifies `transcript-artifacts` (word-level timing) and `MediaProbe` (frame dimensions). Those
two domain gaps are recorded but not yet built.

One decision is deliberately left open for slice 10a: whether MAP windowing excludes `UNCERTAIN`
segments or marks them the way the `.txt` export does. Excluding risks an empty summary on a
non-classifying engine; marking risks the model ignoring the marker. See the `speech_segments`
docstring in `domain/transcript.py`.

## Conventions

- Python 3.12, mypy `strict = True` with `disallow_untyped_defs`. Every function is annotated, including
  tests.
- Module docstrings state *why* the module exists or what invariant it protects, not what it contains.
  Match that density; do not add narration comments.
- Top-level names name the problem (`chunking`, `jobs`, `transcript`), not the framework.
- Errors are domain types in `domain/errors.py`, all deriving from `DomainError`, raised across port
  boundaries. Adapters translate library exceptions into these — never leak a provider exception upward.
- Source audio is Spanish only. No multi-language, no code-switching.
- Never commit media, model weights, or `.env` — `.gitignore` already covers them.
