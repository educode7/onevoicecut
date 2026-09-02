# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A shared-server app — several operators, one machine — that turns multi-hour Spanish source video into a structured transcript,
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

# Hear what the local engine makes of real audio — a dev tool, outside the spec and outside mypy
.venv\Scripts\python.exe scripts\try_local_asr.py RECORDING.mp4 --model small --start 42:10 --seconds 90
```

`scripts/try_local_asr.py` exists because every ASR fixture in the suite is synthesised with ffmpeg, and
no synthetic signal reproduces a human singing over a sermon — the case `SegmentKind` was built for. It
goes through `local_transcriber`, the same lazily-imported factory the resolver uses, and prints each
segment's kind and timestamps, the per-kind totals, the share of the window covered, and the
`transcript.txt` that would be delivered.

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
- `requirements-local-asr.txt` — `faster-whisper==1.2.1`, installed. Pulls CTranslate2 and onnxruntime,
  ~90 MB of wheels before a single weight is fetched, which is why every module that touches it is
  imported lazily or behind `pytest.importorskip`
- `requirements-diarization.txt` — pyannote.audio / WhisperX (slice 9, still empty)
- `requirements.lock.txt` — `pip freeze` of a full install, for reproduction only

ffmpeg is a **system binary**, never a pip dependency.

## Architecture

Hexagonal, with the boundary enforced by a test rather than by convention.

```
src/onevoicecut/
  domain/     # zero third-party imports; frozen slotted dataclasses only
  ports/      # typing.Protocol definitions; imports domain only
  usecases/   # imports domain + ports only — all orchestration lives here
  adapters/   # web/ ffmpeg/ asr/local/ storage/   (asr/cloud/ and llm/ not built yet)
  runtime/    # composition root — the ONLY place adapters are constructed
```

`runtime/` holds `app.py` (web composition root, drain, reconcile), `supervisor.py` (liveness, the
per-chunk watchdog, reaping), `engine_resolver.py`, `settings.py` and `worker.py`. `worker.py` is a
second composition root in its own right: it is a separate process, and it reads its own environment.

`tests/test_architecture.py` walks `domain`, `usecases`, and `ports` with `ast` and fails if any of them
imports `onevoicecut.adapters` or `onevoicecut.runtime`. It parses source text rather than importing, so it
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
  material. **An adapter that cannot classify returns `UNCERTAIN`, never `SPEECH`** — same
  no-silent-degradation invariant as diarization, on a second and independent axis. Do not infer one axis
  from the other.

  The two message-facing consumers then differ on purpose, and it is easy to conflate them:
  `speech_segments` (for the LLM) takes `SPEECH` only, because a model will not honour an inline marker
  the way a reader does; `render_message_text` (the `.txt`) drops `MUSIC` but **keeps `UNCERTAIN`,
  marked**, because dropping it would render an all-uncertain transcript — exactly what a non-classifying
  adapter produces — as a zero-byte file after a three-hour run. Segments with no text at all are
  skipped: a filtered non-speech range is a range, not a line, and rendering it printed a bare `[?] ` per
  silence.
- **Engine choice has no global default** — it is per job, resolved by `runtime/engine_resolver.py`.
  Use cases stay engine-agnostic.
- **Secrets** are read at adapter construction in the resolver, so a missing key fails fast before a
  three-hour run starts. They never enter `JobRecord`, logs, or worker argv.
- **One spawn decision point.** Upload queues; only `drain_once`, driven by the lifespan supervisor,
  starts a worker. `WebDependencies` carries no launcher at all, which is what makes "never exceed the
  cap" a property of the wiring rather than of two code paths agreeing. Two spawn points meant two
  concurrent uploads could each decide a slot was free.
- **Concurrency is derived, never counted** — every sweep lists the store and re-asks the OS, exactly
  like progress. A counter would be correct until the first crash, and crashes are the designed-for case.
- **Liveness is a live pid *and* a fresh heartbeat**, defined once in `worker_is_alive` and consumed by
  both reconcile and the capacity derivation. A bare pid check cannot see a hung worker and believes a
  recycled pid; either one orphans a job forever. The worker is the sole writer of the heartbeat, at
  claim time and every chunk boundary — liveness has to be a side effect of doing work, not of being
  loaded into memory.
- **State-set membership lives in `domain/jobs.py`** (`WORKER_BOUND_STATES`, `TERMINAL_STATES`), because
  reconcile, the capacity gate and cancel classification all branch on it and three derivations drift.
- **Filtering non-speech out of the decode is only half the job.** The local adapter runs the
  voice-activity pass twice over the same samples: once inside the decode, to starve the hallucination,
  and once alongside it, to put the filtered ranges *back into the result* with their timestamps. A plain
  `vad_filter=True` satisfies "no fabricated SPEECH" while destroying every musical range clip rendering
  has to aim at. `_tile` fills every remaining hole, so a chunk always comes back whole.
- **Two non-speech kinds, decided by which detector said what.** A hole with no voice activity is `MUSIC`;
  a hole *with* voice activity but no decoder text is `UNCERTAIN` — a real disagreement between two
  detectors, and claiming to know which was right is the silent degradation this axis exists to stop. The
  same reasoning classifies decoded text carrying a high `no_speech_prob` as `UNCERTAIN`, not `MUSIC`,
  because `without_music` drops `MUSIC` outright and a misjudged sentence would vanish from the export
  instead of arriving marked.
- **The engine must prove the device, not merely load on it.** CTranslate2 allocates the model on the
  selected device and returns happily, then resolves its compute libraries lazily on the first `encode()`
  — so a machine with a GPU and no usable cuBLAS constructs fine and dies mid-job. `_prove` decodes one
  second of silence in the constructor. **It never falls back to CPU**: that is the same job twenty times
  slower, chosen by nobody, and the identical silent substitution the resolver refuses between engines.
- **The watchdog reads the heartbeat, not `results/` mtime.** The worker writes a heartbeat at the top of
  every chunk iteration, and TRANSCRIBING begins only after extraction and planning — so for a job in that
  state the heartbeat's age *is* how long the current chunk has been running. Two conditions must hold
  together: the heartbeat is stale **and** the job has been TRANSCRIBING longer than the timeout. The
  second is not decoration — the heartbeat is not refreshed during extraction, and extracting three hours
  of video outlasts a thirty-minute chunk timeout.
- **Reconcile, the watchdog and reaping do not overlap**, because they ask different questions. Reconcile
  asks whether a worker *exists* (at boot). The watchdog asks whether a *live* one is still moving. Reaping
  asks what an *exited* one left behind, and classifies by what the record says rather than by the exit
  code: QUEUED means nothing will ever write it → FAILED with a reason; worker-bound means it died
  mid-flight → INTERRUPTED; terminal means the worker wrote its own account → left alone.

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
- **Measure the diff before committing a slice, not after.** The ×4 rule came from nine early slices that
  overran **3.2x to 5.1x, mean ≈ 4.0x**. It no longer describes how this repo works: the six units since
  `multi-operator-access` measured **0.86x, 0.92x, 1.06x, 1.26x, and 1.97x** (the last only because it
  absorbed an unforeseen defect). What changed is not estimating skill — it is that units are now sized
  against the smaller target and split at the first natural seam. **Keep measuring every unit**; the ×4
  multiplier is history, not a planning rule, and treating it as one now over-splits.
- **The budget is 800 lines**, not 400 — `openspec/config.yaml` `review.budget_lines` was raised on
  2026-08-31 and this file said 400 for months afterwards. Units in `multi-operator-access` were still
  sized against 400, deliberately: the smaller target is what forced the four-way splits that finally
  landed units on estimate instead of 4x over.
- **Split at the seam, not at the line count.** The rule that has actually held: two halves that are each
  green alone are two units. Slice 7a-ii split that way at 504 lines, slice 7c at 986 — and both halves of
  each landed well inside the budget.

### Current state

Two changes are in flight. `video-transcription-pipeline` is green through **slice 7c**; only 7b-ii is
open in slice 7. `multi-operator-access` is **complete** (all six slices) and ready to archive. Together:
**973 tests — 954 in the default run, 19 `localmodel`, no `paid` tests yet — mypy clean over 157 files.**

On disk today are `domain/`, `ports/`, the use cases (`ingest_media`, `admit_job`, `plan_chunks`,
`stitch_transcript`, `transcribe_job`, `resume_job`, `ownership`, `cancel_job`, plus the uncalled
`purge_job_artifacts` seam), `adapters/ffmpeg/`, `adapters/storage/`, `adapters/web/` (including
`auth.py`), `adapters/asr/local/faster_whisper_adapter.py`, `runtime/` (`app`, `supervisor`, `settings`,
`engine_resolver`, `worker`), `tests/fakes/`, `tests/contract/` and `scripts/`. Still missing: the cloud
ASR adapter, diarization, any LLM adapter, script generation, clip rendering, and the browser UI — the
HTTP surface exists but nothing renders it.

**The pipeline runs end to end with the real local engine** — real HTTP, real filesystem, real ffmpeg,
real faster-whisper:

```powershell
$env:ONEVOICECUT_DATA_DIR = ".\data"
$env:ONEVOICECUT_OPERATOR_TOKENS = "maria:some-token"
$env:ONEVOICECUT_LOCAL_MODEL_SIZE = "small"   # no default: it decides quality and hours of runtime
$env:ONEVOICECUT_LOCAL_DEVICE = "cpu"         # "auto" is the default; see the cuBLAS note below
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m uvicorn onevoicecut.runtime.app:get_app --factory
```

`POST /api/jobs` → `PUT /api/jobs/{id}/media` → the record goes **QUEUED** → the drain supervisor starts
a worker within one five-second sweep → `GET /api/jobs/{id}` reports chunk progress → `transcript.txt`
lands in the job directory.

**On this machine `ONEVOICECUT_LOCAL_DEVICE=cpu` is required.** `get_cuda_device_count()` returns 1, so
`auto` selects CUDA, but `cublas64_12.dll` is absent and inference cannot run. Since slice 7c the engine
proves the device at construction, so this is a clean `EngineUnavailable` at engine resolution naming the
variable — not a job that dies mid-chunk. Installing the CUDA runtime is the other way out.

Three environment variables are new since slice 7b and none of them existed when the first drafts of this
file were written: `ONEVOICECUT_LOCAL_MODEL_SIZE` (no default — an unset value registers *no* local
engine rather than picking a size), `ONEVOICECUT_LOCAL_DEVICE` (default `auto`), and
`ONEVOICECUT_CHUNK_TIMEOUT_SECONDS` (default 1800; also accepted as `..._CHUNK_TIMEOUT_S`). The worker
reads the first two from its own inherited environment, because argv is visible to every user on a shared
machine.

Five HTTP routes exist, **all of them authenticated** — a bearer token parsed from
`ONEVOICECUT_OPERATOR_TOKENS`, fail-closed at boot: `POST /api/jobs` (admit), `GET /api/jobs` (shared
listing with owner attribution and a server-side `?mine=true` filter), `GET /api/jobs/{id}`
(chunk-level progress; read-only, and a test enforces that it writes nothing),
`PUT /api/jobs/{id}/media` (raw-body streaming upload) and `POST /api/jobs/{id}/cancel`.

Three authorization invariants, each enforced by a test rather than by review:

- **Deny by default.** `WebDependencies` cannot be constructed without an authenticator, and the 401
  check is generated from `app.routes` — a route added later joins it automatically and fails the
  default run the day it is written without auth wiring.
- **Reading is shared, mutating is owner-only.** The 403 check is likewise generated from the route
  table, over every mutating route that names a job. `owner=None` (a legacy record) matches nobody:
  visible to all, mutable by none, with no special case in the authorization code.
- **Precedence is 401 → 404 → 403.** An unauthenticated caller never learns whether an id exists; a
  malformed id and an unknown one are indistinguishable.

Four things about the upload path are load-bearing and easy to undo by accident:

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

Two supervised tasks run for the app's lifetime, on deliberately different clocks. The **drain** sweeps
every five seconds, reaping exited workers before it serves the queue. The **watchdog** sweeps every
sixty against a thirty-minute per-chunk timeout; folding it into the drain would tie that judgement to
the drain's cadence, and a drain sweep that raised would take the timeout down with it.

Next up is **slice 7b-ii** (task 7.7) — but its own text says it is written against the cloud adapter that
lands in slice 8a, so it will most likely find nothing to extract yet, the way task 4.20 and 5.18 did.
The follow-up 7b-i noted for it (moving liveness out of `app.py`) is already done: slice 7c's wiring
forced it, because `app.py` needed the sweep and the sweep needed the probe. After that, **slice 8a-i**:
the cloud ASR adapter, which needs a provider choice and `CLOUD_ASR_API_KEY`.

Two gaps are known and deliberately open:

- **The worker's own message never reaches the operator.** Reaping records *that* a worker exited and with
  which status, and points at the server log; the engine's actual complaint goes to the web process's
  stderr. Capturing the child's stderr means pipe management and a deadlock risk if that pipe fills
  during a three-hour job.
- **Real singing is unproven.** Every ASR fixture is synthesised with ffmpeg, and no synthetic signal
  reaches `no_speech_prob ≤ 0.6`. A human voice singing plausibly does, which would classify sung lyrics
  as `SPEECH` and put them in the message — the project's stated normal case. `scripts/try_local_asr.py`
  exists to test it against real material, since media must never be committed.

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
