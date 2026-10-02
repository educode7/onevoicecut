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

Three steps, and they are genuinely separate. Missing the second is the most
common way to get a working install that fails on the first job; missing the
third is the most common way to get a server that refuses to boot.

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

### 3. Configuration — `.env`

```powershell
Copy-Item .env.example .env
```

Each composition root loads the `.env` beside the app at startup with
`override=False`, so a real exported variable always wins over the file: the
file is a floor, not a mask.

**Leave unused assignments commented out.** An empty assignment
(`ONEVOICECUT_MAX_UPLOAD_BYTES=`) is a *value*, not a blank — it lands in the
process environment, pydantic refuses to parse it as an integer, and every later
`Settings()` fails. It looks like dozens of runtime-test failures that pass when
run in isolation. `.env.example` ships every assignment commented for exactly
this reason; uncomment only what you have filled in.

### Optional extras

Install these only if you need what they enable:

| File | Enables | Needed for |
| --- | --- | --- |
| `requirements-local-asr.txt` | local ASR engine | running transcription without a cloud API |
| `requirements-diarization.txt` | speaker labels | multi-speaker / interview jobs (`speaker_mode: "multi"`) |
| `requirements-vision.txt` | subject tracking | **rendering a clip at all** — see below |

They are kept separate on purpose: they pull heavy model dependencies, and a unit
test run should not have to download PyTorch. Each capability is *declared*
rather than silently degraded: a machine without `requirements-vision.txt`
still transcribes and still generates artifacts, but **no clip renders** —
`render_worker` turns the tracker's `REQUIRES_SETUP` into a `FAILED` export
whose reason names the remedy, instead of a process that cannot start. So
`requirements-vision.txt` (plus its weights, cached on first use) is required
for the last step of the walkthrough below.

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
.venv\Scripts\python.exe -m pytest tests/systems/pipeline/transcripts/application/use_cases/commands/test_plan_chunks.py
.venv\Scripts\python.exe -m pytest tests/systems/pipeline/transcripts/application/use_cases/commands/test_plan_chunks.py::test_byte_cap_shortens_the_stride
```

## Running it

```powershell
$env:ONEVOICECUT_DATA_DIR = ".\data"
$env:ONEVOICECUT_OPERATOR_TOKENS = "maria:<token>;jose:<token>"
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m uvicorn onevoicecut.main:get_app --factory
```

The same two variables in `.env` (step 3) do the same job and survive a new
shell; an exported variable still wins over the file. `PYTHONPATH` is required
either way — the package is deliberately not installed.

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

Read by `Settings`, all under the `ONEVOICECUT_` prefix:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ONEVOICECUT_DATA_DIR` | *(none — required)* | Where jobs live. No default on purpose: multi-hour sermons should not go somewhere you did not choose. |
| `ONEVOICECUT_OPERATOR_TOKENS` | *(none — required)* | The operator/token map above. |
| `ONEVOICECUT_MAX_UPLOAD_BYTES` | 16 GiB | A ceiling for one upload on a machine whose normal input is hours of video. |
| `ONEVOICECUT_MAX_CONCURRENT_JOBS` | `1` | How many jobs transcribe at once. Local ASR saturates a machine by itself, so two mostly time-slice and make both slower. Raise it only against a measurement. Must be ≥ 1; `0` is a queue with no exit and is refused at boot. |
| `ONEVOICECUT_MAX_CONCURRENT_RENDERS` | `1` | How many clips render at once. **Independent of the job cap on purpose**: a render is minutes of ffmpeg, a job is hours of ASR, and the two have no reason to move together. |
| `ONEVOICECUT_CHUNK_TIMEOUT_SECONDS` | `1800` | Per-chunk watchdog timeout, also accepted as `..._CHUNK_TIMEOUT_S`. |
| `ONEVOICECUT_SCRIPT_TARGETS` | `tiktok,instagram,youtube,facebook` | Which networks each candidate gets a script for. Also the generation cost: one `complete()` call per candidate per target, not one overall. |

The engine and generation half is read **outside** `Settings` — by the worker,
from its own environment — which is why `.env.example` carries them too:

| Variable | Default | Meaning |
| --- | --- | --- |
| `ONEVOICECUT_LOCAL_MODEL_SIZE` | *(none)* | `tiny`…`large-v3`. No default on purpose: it decides quality *and* hours of runtime. Unset registers no local engine at all. |
| `ONEVOICECUT_LOCAL_DEVICE` | `auto` | `auto`, `cpu`, or `cuda`. On a machine that reports a GPU but lacks the CUDA runtime libraries, `auto` picks CUDA and the engine refuses at construction with `EngineUnavailable` naming this variable — set `cpu`. |
| `ONEVOICECUT_LLM_MODEL` | *(none)* | The model that writes `artifacts.json` (scripts, summary, candidates). Unset registers no generator: the job still ends **COMPLETED** with its transcript and no artifacts. |
| `ONEVOICECUT_OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama is a system service, like ffmpeg: installed by you, reached over localhost, never a pip dependency. |
| `CLOUD_ASR_API_KEY` | *(none)* | **Secret.** Only the `cloud` engine. Read at adapter construction, so a missing key fails fast before a three-hour run. |
| `HUGGING_FACE_TOKEN` | *(none)* | **Secret.** Gates the pyannote diarization weights; only `speaker_mode: "multi"`. |

### The routes

Every **API** request needs `Authorization: Bearer <token>`. The only routes
without the gate are the three that describe the surface — `/docs` (Swagger
UI), `/redoc` and `/openapi.json` — and none of them can read a job.

| Step | Request |
| --- | --- |
| Create a job | `POST /api/v1/jobs` with `{"engine": "local"}` (or `"cloud"`); optional `"speaker_mode": "multi"` |
| Upload the sermon | `PUT /api/v1/jobs/{id}/media`, raw body, filename percent-encoded in `X-Filename` |
| See the board | `GET /api/v1/jobs` — every job with its owner; `?mine=true` narrows to yours |
| Watch one | `GET /api/v1/jobs/{id}` — chunk-level progress, ETA once a chunk has finished |
| Stop one | `POST /api/v1/jobs/{id}/cancel` |
| Request a clip | `POST /api/v1/jobs/{id}/clips` with `{"candidate_index": 0, "targets": ["tiktok"]}` → **202**, `{clip_id, profiles}` |
| Watch a clip | `GET /api/v1/jobs/{id}/clips/{clip_id}` — **every** profile's export, never just one |
| One profile | `GET /api/v1/jobs/{id}/clips/{clip_id}/{profile}` — exactly one |

`targets` names **networks**, not profiles — an operator reasons about
destinations. The system resolves them: all four default networks map to the
single shipped profile `vertical` (1080×1920, 90 s ceiling), so asking for four
destinations returns `profiles: ["vertical"]` and renders **one** file that all
four can use. Requesting a clip writes one `PENDING` export per distinct
profile and renders nothing; a separate render worker picks it up within one
five-second sweep.

```powershell
curl -H "Authorization: Bearer $token" http://localhost:8000/api/v1/jobs
```

Reading is shared, changing is not: **401** if the token is missing or unknown,
**403** if you are not the job's owner, **404** if the id is unknown *or*
malformed — the two are deliberately indistinguishable. Unknown keys in a JSON
body are a **422**, not a silent default.

That contract is machine-readable too: `/openapi.json` declares the
`HTTPBearer` scheme against **every** operation, so `/docs` draws the lock and
a client generated from the document sends the header without being told
twice.

### Where everything lands

```text
data\
  jobs\
    {job_id}\
      job.json          the record
      control.json      cancellation request, polled at chunk boundaries
      media.json        the ffprobe result
      source            the upload — extensionless, so the client never picks a path
      audio.flac        extracted once, from source
      plan.json         the chunk plan
      chunks\           per-chunk audio slices
      results\          per-chunk ASR results — progress is derived by listing these
      transcript.json   the transcript — the source of truth
      transcript.txt    one export of it
      artifacts.json    summary + clip candidates (only when an LLM was configured)
      render\
        {clip_id}\{profile}.json   export record: title, description, scripts, state
        {profile}\{clip_id}.mp4    ← the file you upload by hand
        {profile}\{clip_id}.ass    burnt-in captions
        {profile}\{clip_id}.cmds   the crop trajectory ffmpeg drives
```

Nothing is ever published from here: `PublishPort` is declared and deliberately
unimplemented. Delivery is you, by hand.

### A real case, end to end

Prerequisites beyond Setup — each one buys a specific step:

```powershell
# step 3-4: the transcription engine
.venv\Scripts\python.exe -m pip install -r requirements-local-asr.txt
# step 5: script generation (Ollama is a system service, not a pip package)
ollama pull qwen2.5:7b-instruct
# step 6: reframing around the speaker
.venv\Scripts\python.exe -m pip install -r requirements-vision.txt
```

`.env` for this run:

```dotenv
ONEVOICECUT_DATA_DIR=.\data
ONEVOICECUT_OPERATOR_TOKENS=maria:<token>
ONEVOICECUT_LOCAL_MODEL_SIZE=small
ONEVOICECUT_LOCAL_DEVICE=cpu
ONEVOICECUT_LLM_MODEL=qwen2.5:7b-instruct
```

`LOCAL_MODEL_SIZE` decides both quality and hours of runtime — `small` is a
reasonable first run, `tiny` is the fastest way to see the whole path work.

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m uvicorn onevoicecut.main:get_app --factory
```

Then, in a second shell:

```powershell
$base  = "http://localhost:8000/api/v1"
$head  = @{ Authorization = "Bearer $token" }

# 1. Admit — 201, before anything expensive happens
$job = Invoke-RestMethod -Uri "$base/jobs" -Method Post -Headers $head `
         -ContentType "application/json" -Body '{"engine":"local"}'
$id  = $job.job_id

# 2. Upload — raw body; the filename is percent-encoded metadata, nothing more
$fn  = [uri]::EscapeDataString("predicacion del domingo.mp4")
Invoke-WebRequest -Uri "$base/jobs/$id/media" -Method Put `
  -Headers ($head + @{ "X-Filename" = $fn }) -InFile ".\sermon.mp4" | Out-Null

# 3. Poll until completed. It sits at `queued` for up to one five-second sweep.
do {
  Start-Sleep 10
  $s = Invoke-RestMethod -Uri "$base/jobs/$id" -Headers $head
  if ($s.progress) {
    $eta = if ($s.progress.eta_s) { ", eta $([int]$s.progress.eta_s)s" } else { "" }
    "{0}  {1}/{2} chunks{3}" -f $s.state, $s.progress.chunks_done, $s.progress.chunks_total, $eta
  } else {
    $s.state
  }
} while ($s.state -notin "completed","failed","cancelled")
```

The states you will see, in order:
`pending → queued → extracting → planned → transcribing → stitching → completed`.
`pending` is admission; the upload is what moves it to `queued`.

**`completed` does not mean `artifacts.json` is there yet.** The record turns
`completed` when the transcript is stitched, and artifact generation runs
*after* that, in the same worker process. So if you poll straight to
`completed` and immediately ask for a clip, you can race generation and get a
409 that resolves itself a few seconds later. Wait for `artifacts.json` to
appear in the job directory — that is the honest signal — before step 5.

```powershell
# 4. The transcript
Get-Content ".\data\jobs\$id\transcript.txt" -TotalCount 20

# 5. Request a clip. 409 here means no artifacts.json — see the note below.
$clip = Invoke-RestMethod -Uri "$base/jobs/$id/clips" -Method Post -Headers $head `
          -ContentType "application/json" `
          -Body '{"candidate_index":0,"targets":["tiktok","instagram","youtube","facebook"]}'
$clip.clip_id      # e.g. 01J...
$clip.profiles     # ["vertical"] — four networks, one file

# 6. Poll the export until state is `done`
do {
  Start-Sleep 5
  $c = Invoke-RestMethod -Uri "$base/jobs/$id/clips/$($clip.clip_id)" -Headers $head
  ($c.exports | ForEach-Object { "$($_.profile): $($_.state)" }) -join ", "
} while ($c.exports.state -contains "pending" -or $c.exports.state -contains "rendering")

# 7. The file
Get-Item ".\data\jobs\$id\render\vertical\$($clip.clip_id).mp4"
```

Each export also carries what you need to publish it by hand: the per-network
`variants` (`target`, `format`, `body`, `duration_target_s`) come back in the
response, and the clip's `title` and `description` are in the persisted export
record at `render\{clip_id}\{profile}.json`.

**If step 5 answers 409 `ArtifactsNotAvailable`:** the job completed without
`artifacts.json`. That is a legitimate outcome, not a crash — generation runs
after transcription, inside the worker, and a missing `ONEVOICECUT_LLM_MODEL`,
an Ollama server that is not running, or a model the preflight probe does not
find all skip it with a line on stderr rather than failing a finished
three-hour transcription. Check `ollama list` and the model name in `.env`.

**Configure the LLM before you start the job.** Generation happens in the same
worker run that finishes transcription, and a terminal job is never restarted —
there is no route that regenerates artifacts for a job already `COMPLETED`. A
run that finished without them has to be started again as a new job over the
same source file. The 409 keeps the two situations apart on purpose:
`ArtifactsNotAvailable` means *the artifacts are not there yet*, a
non-`COMPLETED` job means *transcription is not finished*, and both answer 409
because both are "come back later", for different reasons.

**`candidate_index`** points into `artifacts.json`'s `clip_candidates` array.
Open the file to see what was found and pick an index; an out-of-range index is
a 404 naming how many candidates exist.

### What happens after an upload

The upload does not start a worker. It stores the file, records the media, and
sets the job to `queued`; a supervisor inside the server sweeps every five
seconds and starts queued jobs oldest-first, up to
`ONEVOICECUT_MAX_CONCURRENT_JOBS`. So a job can sit at `queued` for a few seconds on
an idle machine, or much longer on a busy one — that is the queue doing its job
rather than a failure. `transcript.txt` and `transcript.json` land in
`data\jobs\{id}\` when it finishes.

### Rolling back to a pre-queue build

**Drain the queue first.** A build that predates the capacity gate has no
`queued` state and will refuse to read those records — loudly, by design, rather
than guessing at them. Before downgrading, either let the queue empty, or move
the `queued` job directories out of `data\jobs\` and put them back afterwards.

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

**It transcribes only when an engine is configured.** With neither
`ONEVOICECUT_LOCAL_MODEL_SIZE` nor `CLOUD_ASR_API_KEY` set, a spawned worker exits `3`
and says so rather than failing later. Exit codes:
`0` completed, `1` failed, `2` cancelled, `3` nothing usable to run.

Killing the worker mid-job is safe. Re-running the same command resumes: every
finished chunk is already committed, and the loop only picks up what is still
owed. Resume is not a separate mode — it is the same command.

## Status

Under construction, delivered in reviewable slices. **The full path has been
run end to end on a real machine** — real HTTP, real filesystem, real ffmpeg,
real faster-whisper, real Ollama: admit → upload → transcribe →
`artifacts.json` → clip request → rendered vertical MP4. The walkthrough above
is that run, written down.

The default suite proves the same path with fakes and a synthesised fixture
(`tests/integration/test_ingest_to_transcript.py`) so it stays green with no
model weights and no network.

**Not built yet: any browser UI.** The HTTP API is there and authenticated;
nothing renders it. Nothing is ever published either — there is no publish
route and none is planned. Delivery is you, by hand.

Known gaps, named rather than hidden:

- **Artifacts cannot be regenerated for a job already `completed`** — see the
  note in the walkthrough. Configure the LLM before the run.
- **Real singing is unproven.** Every ASR fixture in the suite is synthesised
  with ffmpeg, and no synthetic signal reproduces a human singing over a
  sermon — the case `SegmentKind` exists for.
  `scripts\try_local_asr.py RECORDING.mp4 --model small --start 42:10 --seconds 90`
  runs the real local engine against real material and prints each segment's
  kind, the per-kind totals, and the `transcript.txt` that would be delivered.
  Media must never be committed, which is why the tool takes a path you pass in.

## Layout

```
src/onevoicecut/
  main.py     web composition root — entrypoint onevoicecut.main:get_app
  shared/     domain kernel, principal, settings, security, storage core, ffmpeg runner
  systems/
    pipeline/ one bounded context: jobs, transcripts, clips — each with domain/ and
              domain/interfaces/, application/use_cases/{commands,queries}/,
              infrastructure/ and presentation/
  runtime/    parallel composition roots — worker, render worker, supervisor, resolvers
```

`tests/test_architecture.py` fails the build if a layer imports outward (AB-01..AB-05),
if `shared/` ever imports `systems.*` (AB-08), or if adapter construction appears
outside a composition root (AB-09, AB-10). The boundary is a test, not a convention.
