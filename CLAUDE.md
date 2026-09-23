# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A shared-server app — several operators, one machine — that turns multi-hour Spanish source video into a
structured transcript, then into a summary plus timestamped clip candidates with short scripts, then into
rendered vertical clips ready to upload by hand. Two non-goals frame everything downstream of the
transcript. **Nothing is ever published**: `PublishPort` is declared and deliberately unimplemented.
**No frame or word in an output clip may be one the source sermon did not contain** — no avatars, no
synthesized footage, no dubbing, no B-roll, no stock beds; reframing real footage is editing and
inventing a speaker is fabrication, and rev 4 separated the two on purpose. The stopping point used to
be the script artifact; that was a **[BINDING]** non-goal and only the operator who bound it could
reverse it, which is what rev 4 did. The seam moved to after the rendered clip rather than disappearing.

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
  adapters/   # web/ ffmpeg/ storage/ asr/local/ asr/cloud/   (llm/ and vision/ not built yet)
  runtime/    # composition root — the ONLY place adapters are constructed
```

`runtime/` holds `app.py` (web composition root, the three supervisor loops, reconcile),
`supervisor.py` (liveness, the per-chunk watchdog, reaping), `engine_resolver.py`, `settings.py`,
`worker.py` and `render_worker.py`. The last two are each a composition root in their own right: a
separate process, reading its own environment.

`tests/test_architecture.py` walks `domain`, `usecases`, and `ports` with `ast` and fails if any of them
imports `onevoicecut.adapters` or `onevoicecut.runtime`. It parses source text rather than importing, so it
works before those packages exist. Do not weaken it.

### The seven ports

| Port | Contract |
| --- | --- |
| `MediaSourcePort` | **The one async port.** Used only by the web adapter, never by the worker. |
| `AudioExtractorPort` | `probe`/`extract`/`slice`. ffmpeg lives behind this and nowhere else. |
| `TranscriptionPort` | `AudioChunk` → segments. **Returned times are chunk-local**, not absolute. Declares `capabilities()`. |
| `TextGenerationPort` | Generic `complete()`. Knows nothing about summaries, clips, or chunking. |
| `TranscriptStoragePort` | Job record, chunk plan, per-chunk results, transcript, artifacts. `save_chunk_result` MUST be atomic — resume is built on it. |
| `VideoRenderPort` | `RenderRequest` → one file. **One ffmpeg process; no raw frames cross a process boundary.** Only `request.span` is cut, so a clip's cost never depends on the length of the sermon it came from. |
| `SubjectTrackerPort` | `detect()` over a span at a sample rate. Declares `capabilities()` — and **has no adapter**. Production constructs `_UnconfiguredSubjectTracker`, which declares `UNSUPPORTED` so every clip reaches the proven `TrackingUnavailable` path instead of a process that cannot start. |

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
- **Render liveness is a one-shot claim, not a pid-and-heartbeat pair.** `render/{clip_id}/claim`
  carries one timestamp, written when the render worker picks the clip up, and nothing refreshes it.
  A job worker needs a heartbeat because a multi-hour transcription has a *middle* — a stretch where
  the pid is alive, the record reads TRANSCRIBING and nothing is moving. A render is a single bounded
  ffmpeg invocation under `FfmpegVideoRenderer`'s own hard per-call timeout, so it has no middle in
  which to stall unobserved, and a periodic refresh would prove nothing the one-shot claim does not.
  The staleness bound is `render_timeout_for`, **imported rather than reimplemented**, so the drain's
  notion of "too long" cannot drift from the timeout that actually kills the process. The cap counts
  **clips, not export rows** — one process claims a whole clip's profiles at once, so three profiles
  are one slot. And the keys surviving the eligibility prune are in-flight launches, invisible to a
  count derived from storage, so they are *added* to it: without that, a cap of one starts a second
  ffmpeg beside the first during every launch-to-claim window.

### Security invariants (already specified, tested per slice)

- ffmpeg is invoked with list-form `subprocess.run([...])`, never `shell=True`, never string
  interpolation; `-nostdin`, `-protocol_whitelist file`, explicit timeout.
- Client filenames are **metadata only**, never a path component. Storage path is `jobs/{ulid}/source`
  — extensionless, so not even a suffix is the client's to choose.
- All paths are `Path.resolve()`-checked to be inside the job directory before any spawn.
- `job_id` is validated against the ULID regex in `domain/ids.py` before touching the filesystem.
- Content type is validated by `ffprobe`, never by extension.

## Workflow

This repo runs **Spec-Driven Development** (`openspec/`) with **strict TDD** (`strict_tdd: true`).

- `openspec/changes/video-transcription-pipeline/` holds `proposal.md`, `design.md`, ten
  `specs/*/spec.md`, and `tasks.md`. **Read `tasks.md` before implementing** — it is the ordered,
  RED-before-GREEN checklist, and it names the spec scenario each task closes. Archived changes land
  under `openspec/changes/archive/<date>-<name>/`, and their delta specs are promoted to canonical
  `openspec/specs/<capability>/spec.md` — eight capabilities are canonical today.
- Every task pair is RED first: write the failing test, then the implementation. **382 of the 396
  checkboxes in `tasks.md` are checked**; the 14 open ones are named under Current state below.
- The original review budget was **400 lines** per slice. Slice 1 overran to 1,273 lines under
  an accepted one-time exception; the rest were re-estimated from that measured cost. The measured
  ratio is tests 56% / `src` 36% / config 8% — budget accordingly, tests dominate.
- `delivery_strategy: auto-chain`, `chain_strategy: stacked-to-main`. The plan no longer lands as the
  23 units it was first drawn as: slices have been re-split at their seams until `tasks.md` carries
  **61 `## Slice` headings**, so a reviewable unit is a sub-slice (7a-ii, 13b-iv-b), not a slice.
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

One change is in flight. `video-transcription-pipeline` is green through **slice 13b-v**, and the last
unit merged was 13b-iv-b (the render drain). `multi-operator-access` is **archived** at
`openspec/changes/archive/2026-09-17-multi-operator-access/`, its seven delta specs promoted to
canonical `openspec/specs/`. Measured on this tree: **1969 tests — 1939 in the default run, 20
`localmodel`, 10 `paid`, zero skips — mypy clean over 233 source files.**

On disk today are `domain/` (nine modules: `chunking`, `errors`, `framing`, `generation`, `ids`,
`jobs`, `media`, `rendering`, `transcript`), `ports/` (the seven plus `capabilities`), fifteen use
cases (`admit_job`, `build_subtitle_cues`, `cancel_job`, `generate_artifacts`, `ingest_media`,
`ownership`, `plan_chunks`, `plan_trajectory`, `purge_job_artifacts`, `render_clip`,
`render_profiles`, `request_clip_export`, `resume_job`, `stitch_transcript`, `transcribe_job`),
`adapters/ffmpeg/` (`argv`, `extractor`, `process`, `sendcmd`, `subtitles`, `video_render`),
`adapters/storage/`, `adapters/web/` (`app`, `auth`, `schemas`, `routers/jobs`), both ASR adapters
(`asr/local/faster_whisper_adapter` + `declarations`, `asr/cloud/openai_whisper_adapter`), `runtime/`
(`app`, `engine_resolver`, `render_worker`, `settings`, `supervisor`, `worker`), `tests/{fakes,unit,
integration,contract}/` and `scripts/`.

Four things are still missing, and the first is the one that bites:

- **No LLM adapter, so generation never runs in production.** `generate_artifacts` is built and
  unit-tested above `TextGenerationPort`, but nothing constructs a `TextGenerationPort`, nothing calls
  `run_map`/`write_script_variants`, and `save_artifacts` has no production caller. Consequence:
  `load_artifacts` returns `None` for every real job, so `POST /api/jobs/{id}/clips` answers 409
  `ArtifactsNotAvailable` and the whole render half downstream of it is unreachable from HTTP today.
- **No vision-backed `SubjectTrackerPort`** (slice 13c). Production constructs
  `_UnconfiguredSubjectTracker`, which declares `UNSUPPORTED`, so every clip that does get rendered
  fails cleanly with `TrackingUnavailable` rather than producing a wrong crop.
- **No diarization** (tasks 9.3/9.4), and `requirements-diarization.txt` is still a comment.
- **No browser UI.** The HTTP surface is complete and authenticated; nothing renders it.

And a fifth gap that is measurement, not code: **the one shipped render profile is deliberately
unmeasured.** `RENDER_PROFILES` holds a single `vertical` (1080×1920) with `safe_area=None`, because
nobody has measured where each destination's interface sits over the frame — and a profile declaring no
safe area is *refused*, never defaulted, so `resolve_render_profiles` answers `RenderProfileInvalid` for
every clip request even once generation and tracking exist. All four `SCRIPT_TARGETS` name that one
profile, on purpose: one file shared by four networks is exactly what render dedup exists for.
`check_target_profiles` cross-checks the two registries at boot for *membership* only and must not be
tightened to renderability — doing so would refuse to start a server that transcribes and renders
nothing.

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

Configuration is read once, in `runtime/settings.py` (`env_prefix="ONEVOICECUT_"`), and nothing below
`runtime/` reads the environment at all:

| Variable | Default | Why that default |
| --- | --- | --- |
| `ONEVOICECUT_DATA_DIR` | *(required)* | No default on purpose: one would put multi-hour sermons somewhere the operator did not choose, and the first they would know of it is a full disk. |
| `ONEVOICECUT_OPERATOR_TOKENS` | `""` | `name:token;name:token`. Empty rather than absent so the token-map parser — not a bare pydantic error — is what refuses an unconfigured boot. |
| `ONEVOICECUT_MAX_UPLOAD_BYTES` | 16 GiB | Bounds one upload on a machine whose normal input is multi-hour video; it describes a ceiling, not a typical file. |
| `ONEVOICECUT_MAX_CONCURRENT_JOBS` | 1, `ge=1` | Local ASR saturates this machine by itself. `ge=1` because 0 is not "unlimited", it is a queue with no exit. |
| `ONEVOICECUT_MAX_CONCURRENT_RENDERS` | 1, `ge=1` | **Independent of the job cap, deliberately** — a render is minutes of ffmpeg work and a job is hours of ASR, so the two caps have no reason to move together. |
| `ONEVOICECUT_CHUNK_TIMEOUT_SECONDS` | 1800, `gt=0` | Also accepted as `..._CHUNK_TIMEOUT_S` (the name pydantic would derive) because design.md documents the long one, and an operator setting the documented variable and watching it do nothing is the worst of both. This value reaches the *watchdog*. |
| `ONEVOICECUT_SCRIPT_TARGETS` | `tiktok,instagram,youtube,facebook` | Comma-separated, the same shape `OPERATOR_TOKENS` uses: an operator who has to write JSON into an environment variable gets it wrong once. The default is also the billed cost — four `complete()` calls per candidate, not one. |

Four more are read outside `Settings`, and two of them carry no `ONEVOICECUT_` prefix. The worker reads
`ONEVOICECUT_LOCAL_MODEL_SIZE` (no default — an unset value registers *no* local engine rather than
picking a size) and `ONEVOICECUT_LOCAL_DEVICE` (default `auto`) from its own inherited environment,
because argv is visible to every user on a shared machine; `CLOUD_ASR_API_KEY` and
`HUGGING_FACE_TOKEN` are named without the prefix, the latter because `huggingface_hub` recognises
several spellings and that is the one this project documents. All four are values *handed in* at
adapter construction — the modules that need them know only the variable's name, so a refusal can carry
its own remedy. `CHUNK_TIMEOUT_ENV_NAMES` lives in `settings.py` rather than at either call site
because two separate programs enforce the per-chunk timeout, and a spelling that drifted between them
would be a setting silently applying to one and not the other.

Eight HTTP operations across seven paths, **all of them authenticated** — a bearer token parsed from
`ONEVOICECUT_OPERATOR_TOKENS`, fail-closed at boot. The five job-level ones: `POST /api/jobs` (admit,
201), `GET /api/jobs` (shared listing with owner attribution and a server-side `?mine=true` filter),
`GET /api/jobs/{id}` (chunk-level progress; read-only, and a test enforces that it writes nothing),
`PUT /api/jobs/{id}/media` (raw-body streaming upload, 204) and `POST /api/jobs/{id}/cancel`. Then
three for clips: `POST /api/jobs/{id}/clips` (202 — writes one `PENDING` export per distinct profile
and renders nothing), `GET /api/jobs/{id}/clips/{clip_id}` (every profile's export, never just one: a
single-object response would have to pick a profile to report and be wrong about the rest) and
`GET /api/jobs/{id}/clips/{clip_id}/{profile}` (exactly one — a clip id alone never identifies a
single rendered file, so an unknown profile on a known clip is a distinct 404 from an unknown clip).

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

**And a stale `$env:Path` quietly takes that away.** The binaries live at
`%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_...\ffmpeg-9.0.1-full_build\bin`, and that
directory *is* on the persisted user PATH — but a long-running agent or editor process holds the
environment it was started with, so `Get-Command ffmpeg` finds nothing while the install is perfectly
healthy. The failure is silent and green: **exactly 36 tests skip** and the suite still passes, so a
result reporting 36 skips proves nothing about the ffmpeg surface. Diagnose with
`[Environment]::GetEnvironmentVariable('Path','User')`, never `$env:Path` alone, and never conclude
ffmpeg is uninstalled from a shallow recursive scan. To force a real run inside a stale session,
prepend the bin directory to `$env:Path` in the same command that launches pytest. Slice 13b-iv-b's own
note in `tasks.md` — "1894 passed, 41 skipped" — was written from exactly such a run and is not
evidence about the ffmpeg surface; the same suite measured today against the same tree reports **1939
passed, 30 deselected, zero skips**.

Three supervised tasks run for the app's lifetime, on deliberately different clocks. The **job drain**
sweeps every five seconds, reaping exited workers before it serves the queue. The **watchdog** sweeps
every sixty against a thirty-minute per-chunk timeout; folding it into the drain would tie that
judgement to the drain's cadence, and a drain sweep that raised would take the timeout down with it.
The **render drain** sweeps every five seconds as well, and still gets its own loop rather than a
second body inside the job drain's `try`. It answers against a different cap
(`MAX_CONCURRENT_RENDERS`, independent by design), a different liveness rule (claim age, not
pid-and-heartbeat), and it has no reconcile step at all — an abandoned claim self-heals off storage
the moment a sweep looks at it, where a job record needs `reap_exited_workers` to turn a dead pid into
something an operator can read. Sharing one loop would also mean sharing one `except`: a render sweep
that raised would strand every queued job on the machine, which is exactly the coupling the watchdog's
own paragraph refuses. Each loop logs its own bad sweep, sleeps, and goes round again.

Fourteen tasks are open, in two groups, and **neither is blocked on anything this repo can write**.

- **9.3 / 9.4 (slice 9a-ii): the diarizing call and speaker labels.** `pyannote.audio`'s weights are
  gated on HuggingFace — they do not download until a *human* accepts the terms on their own account,
  and no token can be configured before that. Writing several hundred lines against a call shape,
  return type and set of failure modes nobody has executed once was considered and rejected: it would
  look finished and be unverified in every detail that matters. `requirements-diarization.txt` is
  therefore still a comment rather than a pin, and gets one only from a real install, the way
  `requirements-local-asr.txt` got `faster-whisper==1.2.1`. What ships today is the honest refusal:
  9a-i flipped the declaration to `REQUIRES_SETUP`, so a speaker-mode job this machine cannot serve is
  refused at admission. **To unblock**: accept the `pyannote/speaker-diarization-3.1` terms, install
  the package, set `HUGGING_FACE_TOKEN`, pin from that install.
- **13c.1 – 13c.12 (slices 13c-i / 13c-ii): the vision-backed `SubjectTrackerPort`.** The RED tests
  (13c.1, .3, .5, .8) are `localmodel`-marked because the adapter they drive *is* model weights —
  sequential in-process decode over the clip span, downscaled, every Nth frame; a pre-decode span guard
  reading `max_clip_seconds`; a `capabilities()` probe that reports `REQUIRES_SETUP` while
  `requirements-vision.txt` is absent; a tracker-resolver mirroring `runtime/engine_resolver.py`; and a
  contract test putting the real adapter through the same body the fake passes. `_UnconfiguredSubjectTracker`
  is the placeholder 13c-i replaces, and its own docstring records that nothing else about
  `render_worker.py` changes on that day.

Everything else through slice 13b-v is checked off, and the two `## Slice` headings left in `tasks.md`
are exactly these.

Two gaps are known and deliberately open:

- **The worker's own message never reaches the operator.** Reaping records *that* a worker exited and with
  which status, and points at the server log; the engine's actual complaint goes to the web process's
  stderr. Capturing the child's stderr means pipe management and a deadlock risk if that pipe fills
  during a three-hour job.
- **Real singing is unproven.** Every ASR fixture is synthesised with ffmpeg, and no synthetic signal
  reaches `no_speech_prob ≤ 0.6`. A human voice singing plausibly does, which would classify sung lyrics
  as `SPEECH` and put them in the message — the project's stated normal case. `scripts/try_local_asr.py`
  exists to test it against real material, since media must never be committed.

The render drain's review left five follow-ups. They are **WARNINGs, explicitly informational** — none
of them reopens the unit:

- **One malformed export record stops every pending render.** `list_clip_exports` decodes every JSON its
  glob finds inside one comprehension, so a single corrupt or half-written export raises out of the
  whole sweep; `render_drain_supervisor` then only logs and sleeps. The queue stays stopped, with a line
  on stderr, until an operator removes the file.
- **The refused-range batch's terminal state is unproven.** `render_pending_exports` claims — writing
  `RENDERING` and the claim timestamp — *before* `_range_disagreement` runs. A test proves the claim is
  written for a refused range; none proves the refused batch ends `FAILED` rather than as freshly
  `RENDERING` work the drain will later judge abandoned. Read the code and it does record `FAILED`;
  nothing pins it.
- **No test asserts `render_drain_supervisor` forwards `reap()` into the sweep's `exited` parameter.**
  That wiring is verified by mypy and by reading. It is the weakest of the three and the easiest to break
  silently: drop it and a worker that dies before claiming strands its clip until the web process
  restarts.
- **`RenderWorkerProcesses.finished`'s docstring now reads stronger than the truth.** It still says the
  result is gathered "purely to reap it from the OS" and is "deliberately not read for any state
  transition". Both were true when written; the sweep now reads it to release `spawned` keys.
- **The job drain does not have the guarantees its render-side twin now has, and the divergence is
  deliberate.** `drain_once` still derives `active` from records alone, so a launch whose worker has not
  claimed yet consumes no cap slot; and it prunes `spawned` only by what the records still call QUEUED,
  so a worker that dies before claiming strands its job until the web process restarts. Its docstring
  accepts the second one explicitly — "after a restart the records are the truth, and a job whose worker
  died before claiming is correctly started again" — and is silent on the first. `render_drain_once`
  closes both. Do not "fix" the asymmetry by copying one sweep onto the other without re-arguing it.

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
