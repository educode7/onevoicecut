# transcribe

Turns multi-hour Spanish source video into a structured transcript, then into a
summary plus timestamped clip candidates with short scripts — the raw material for
cutting short-form video. It stops at the script artifact: no rendering, no
publishing.

Single operator, runs locally.

## Setup

Two steps, and they are genuinely separate. Missing the second is the most common
way to get a working install that fails on the first job.

### 1. Python dependencies

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
```

### 2. ffmpeg — a system binary, NOT a pip package

`pip install -r requirements.txt` does **not** install ffmpeg, and no requirements
file ever will. Audio extraction shells out to the `ffmpeg` and `ffprobe`
executables, so both must be on `PATH`.

```powershell
winget install Gyan.FFmpeg
```

Other platforms and manual builds: <https://ffmpeg.org/download.html>

Verify — both must print a version:

```powershell
ffmpeg -version
ffprobe -version
```

If they do not, open a new shell so the updated `PATH` is picked up.

Without ffmpeg the app fails at extraction with an error naming the missing
binary and repeating these instructions, and the `integration` tests skip rather
than fail.

### Optional extras

Install these only if you need what they enable:

| File | Enables | Needed for |
| --- | --- | --- |
| `requirements-local-asr.txt` | local ASR engine | running transcription without a cloud API |
| `requirements-diarization.txt` | speaker labels | multi-speaker / interview jobs |

They are kept separate on purpose: they pull heavy model dependencies, and a unit
test run should not have to download PyTorch.

## Running the tests

```powershell
.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"
.venv\Scripts\python.exe -m mypy src tests
```

The default run never calls a billed API and never loads model weights. Markers:

| Marker | Meaning | In the default run |
| --- | --- | --- |
| *(none)* | domain and use cases against fakes | yes |
| `integration` | real filesystem or ffmpeg subprocess — free and fast | yes, skipped if ffmpeg is absent |
| `localmodel` | loads real ASR/diarization weights | no |
| `paid` | calls a billed cloud API | no |

Run one file, or one test:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/usecases/test_plan_chunks.py
.venv\Scripts\python.exe -m pytest tests/unit/usecases/test_plan_chunks.py::test_byte_cap_shortens_the_stride
```

## Running it

```powershell
$env:TRANSCRIBE_DATA_DIR = ".\data"
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m uvicorn transcribe.runtime.app:get_app --factory
```

Then, against the running server:

| Step | Request |
| --- | --- |
| Create a job | `POST /api/jobs` with `{"engine": "local"}` |
| Upload the sermon | `PUT /api/jobs/{id}/media`, raw body, filename percent-encoded in `X-Filename` |
| Watch it | `GET /api/jobs/{id}` — chunk-level progress, ETA once a chunk has finished |

The upload spawns one worker process for that job. `transcript.txt` and
`transcript.json` land in `data\jobs\{id}\` when it finishes.

`TRANSCRIBE_DATA_DIR` has no default on purpose: multi-hour sermons should not go
somewhere you did not choose.

### Running a job directly

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m transcribe.runtime.worker --job-id <ulid> --data-dir .\data
```

One process per job — not a thread, not a queue. A process can be killed when a
three-hour job goes wrong, and while it lives it is the only writer of that job's
record.

`PYTHONPATH=src` is needed because the package is deliberately not installed:
`requirements.txt` pins dependencies and there is no `pyproject.toml`. `pytest`
sets the same path itself via `pytest.ini`.

**It will not transcribe anything yet.** No real ASR engine is wired, so a
spawned worker exits `3` and says so rather than failing later. Exit codes:
`0` completed, `1` failed, `2` cancelled, `3` nothing usable to run.

Killing the worker mid-job is safe. Re-running the same command resumes: every
finished chunk is already committed, and the loop only picks up what is still
owed. Resume is not a separate mode — it is the same command.

## Status

Under construction, delivered in reviewable slices. The whole path from an HTTP
upload to a transcript on disk exists and is exercised end to end by
`tests/integration/test_ingest_to_transcript.py` — real HTTP, real filesystem,
real ffmpeg, fake ASR.

**Not built yet: either real ASR engine, script generation, and any browser UI.**
The HTTP API is there; nothing renders it, and nothing transcribes for real.

## Layout

```
src/transcribe/
  domain/     entities and errors; zero third-party imports
  ports/      the Protocol definitions; imports domain only
  usecases/   orchestration; imports domain and ports only
  adapters/   ffmpeg and filesystem storage today; web, ASR and LLM to come
  runtime/    composition root — the only place adapters are constructed
```

`tests/test_architecture.py` fails the build if `domain`, `usecases` or `ports`
ever imports `adapters` or `runtime`. The boundary is a test, not a convention.
