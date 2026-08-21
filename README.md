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

## Status

Under construction, delivered in reviewable slices. Working today: the domain
model, all five ports, chunk planning, overlap stitching, and the ffmpeg
extraction adapter. Not built yet: the job runner, the web upload UI, either real
ASR engine, and script generation.

There is no application to run end to end yet — `pytest` is the entry point.

## Layout

```
src/transcribe/
  domain/     entities and errors; zero third-party imports
  ports/      the five Protocol definitions; imports domain only
  usecases/   orchestration; imports domain and ports only
  adapters/   ffmpeg today; web, ASR, LLM and storage to come
  runtime/    composition root (not built yet)
```

`tests/test_architecture.py` fails the build if `domain`, `usecases` or `ports`
ever imports `adapters` or `runtime`. The boundary is a test, not a convention.
