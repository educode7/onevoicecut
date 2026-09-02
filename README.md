# OneVoiceCut

OneVoiceCut is an intelligent tool designed to automate cutting and optimizing long
videos into short fragments (Reels, TikToks, and YouTube Shorts). This project is
focused on spreading the Everlasting Gospel and the Three Angels' Message
(Revelation 14), aligned with the principles of the Seventh-day Adventist Church,
unifying the prophetic message in a single digital voice.

Several operators, one shared server. Everyone can see every job on the board;
only the operator who created a job can change it. The server runs on one machine
and processes a bounded number of jobs at a time — the rest wait in a queue.

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
$env:ONEVOICECUT_DATA_DIR = ".\data"
$env:ONEVOICECUT_OPERATOR_TOKENS = "maria:<token>;jose:<token>"
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m uvicorn onevoicecut.runtime.app:get_app --factory
```

### Operator tokens

`ONEVOICECUT_OPERATOR_TOKENS` is `name:token;name:token`. Names are
`[a-z0-9_-]`, up to 64 characters. Generate tokens with real entropy:

```powershell
.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

**The server refuses to boot without it.** An absent, empty, malformed, or
duplicated map is a startup error naming the problem — never a server that comes
up with authentication off. Rotation is editing the variable and restarting;
tokens are not stored anywhere else, and no token value ever reaches a job
record, a log line, or a worker's argv.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `ONEVOICECUT_DATA_DIR` | *(none — required)* | Where jobs live. No default on purpose: multi-hour sermons should not go somewhere you did not choose. |
| `ONEVOICECUT_OPERATOR_TOKENS` | *(none — required)* | The operator/token map above. |
| `ONEVOICECUT_MAX_CONCURRENT_JOBS` | `1` | How many jobs transcribe at once. Local ASR saturates a machine by itself, so two mostly time-slice and make both slower. Raise it only against a measurement. Must be ≥ 1; `0` is a queue with no exit and is refused at boot. |

### The routes

Every request needs `Authorization: Bearer <token>`. There is no anonymous route.

| Step | Request |
| --- | --- |
| Create a job | `POST /api/jobs` with `{"engine": "local"}` |
| Upload the sermon | `PUT /api/jobs/{id}/media`, raw body, filename percent-encoded in `X-Filename` |
| See the board | `GET /api/jobs` — every job with its owner; `?mine=true` narrows to yours |
| Watch one | `GET /api/jobs/{id}` — chunk-level progress, ETA once a chunk has finished |
| Stop one | `POST /api/jobs/{id}/cancel` |

```powershell
curl -H "Authorization: Bearer $token" http://localhost:8000/api/jobs
```

Reading is shared, changing is not: **401** if the token is missing or unknown,
**403** if you are not the job's owner, **404** if the id is unknown *or*
malformed — the two are deliberately indistinguishable.

### What happens after an upload

The upload does not start a worker. It stores the file, records the media, and
sets the job to **QUEUED**; a supervisor inside the server sweeps every five
seconds and starts queued jobs oldest-first, up to
`ONEVOICECUT_MAX_CONCURRENT_JOBS`. So a job can sit at QUEUED for a few seconds on
an idle machine, or much longer on a busy one — that is the queue doing its job
rather than a failure. `transcript.txt` and `transcript.json` land in
`data\jobs\{id}\` when it finishes.

### Rolling back to a pre-queue build

**Drain the queue first.** A build that predates the capacity gate has no
`queued` state and will refuse to read those records — loudly, by design, rather
than guessing at them. Before downgrading, either let the queue empty, or move
the QUEUED job directories out of `data\jobs\` and put them back afterwards.

Everything else survives a rollback untouched: the `owner` field is simply a key
an older build does not read.

### Running a job directly

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m onevoicecut.runtime.worker --job-id <ulid> --data-dir .\data
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
src/onevoicecut/
  domain/     entities and errors; zero third-party imports
  ports/      the Protocol definitions; imports domain only
  usecases/   orchestration; imports domain and ports only
  adapters/   ffmpeg and filesystem storage today; web, ASR and LLM to come
  runtime/    composition root — the only place adapters are constructed
```

`tests/test_architecture.py` fails the build if `domain`, `usecases` or `ports`
ever imports `adapters` or `runtime`. The boundary is a test, not a convention.
