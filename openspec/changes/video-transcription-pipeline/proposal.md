# Proposal: Video Transcription Pipeline

> Phase: `sdd-propose` (rev 4 — vertical clip rendering) · Artifact store: hybrid (mirror of Engram `sdd/video-transcription-pipeline/proposal`)
> Inputs: `exploration.md`, Engram 637/639/640/641, the answered proposal question round (rev 2), the
> **rev 3 answer to Open Question 8** (music and singing in the source audio), plus the **rev 4 scope
> decision** taken after slice 4a shipped: the operator's real outcome is a publishable vertical clip,
> not a script.
> **[BINDING]** = user decision, not re-openable. **[ANSWERED]** = resolved in the question round.

## Why This Revision Exists

Rev 1-3 stopped at the script artifact, and said so as a **[BINDING]** non-goal. That boundary was
drawn on a stated intent — "the next step (cutting and producing the clip)" was assumed to be a human
with an editor.

That assumption was wrong about the actual job. The source material is **sermons filmed by the
operator's church**, and the outcome wanted is a short vertical video published to social networks.
Cutting each clip by hand is exactly the manual labor this change exists to remove; stopping at a
script relocates the work rather than eliminating it. The operator confirmed the revised outcome
directly.

**A binding non-goal is reversible only by the person who bound it, and only deliberately.** This
revision records that reversal rather than quietly widening scope — the same treatment speaker
diarization received in rev 2, which moved from non-goal to opt-in path with the change stated in
line. What was learned from that precedent applies here too: the cost is not in the feature, it is in
the honest asymmetry the feature exposes. See *Vertical Reframing Reality*.

## Intent

The user produces high-impact short-form social video from **multi-hour church sermon footage**. Today
extracting it is manual end to end: watch the video, take notes, retype quotes, guess where the good
moments are, then cut and reframe each clip by hand.

The outcome wanted is: drop a sermon in, get back a summary, a shortlist of **clip-worthy moments with
timestamps that point back into the source footage**, and — as of rev 4 — **the rendered vertical clips
themselves**, subtitled and ready to upload. The transcript is the intermediate representation that
makes the selection possible; the crop trajectory is the intermediate representation that makes the
reframe possible. Neither is the deliverable.

**The defining operational fact: source videos are always multi-hour** **[ANSWERED]**. Multi-hour is
the normal case, not the tail. This is not a scaling concern to handle later — it is the constraint
that shapes the job model, the progress reporting, the failure semantics, and the storage design from
slice 1 onward.

## Scope

### In Scope

- **Local web UI with HTTP upload** as the ingest path **[BINDING]**, plus **asynchronous job handling**:
  job creation, chunk-level status/progress polling, terminal success/failure. A multi-hour
  transcription MUST NOT block an HTTP request. Upload size limits and request timeouts are in scope.
- **Hexagonal decomposition** with **eight ports — seven implemented, one declared** (see Approach); five
  through rev 3, plus `SubjectTrackerPort`, `VideoRenderPort` and a declared-only `PublishPort` **[rev 4]**.
  ASR is a port with **two interchangeable
  adapters — one local engine, one cloud API engine** **[BINDING]**.
- **Per-job ASR engine selection** **[ANSWERED]**: the operator chooses local or cloud **per job**,
  because the choice is content-dependent — sensitive material goes local, the rest may go cloud.
  There is no single global default engine.
- **Per-job speaker mode** **[ANSWERED]**: default is **single voice, talking-head**. The operator may
  declare "two or more speakers" on a job, which enables diarization. See the honest capability
  asymmetry in *Diarization Reality* below.
- **Spanish-only source audio** **[ANSWERED]** — promoted from assumption to stated requirement. No
  multi-language handling, no code-switching support. This narrows model and provider selection.
- **Mixed speech and non-speech audio** **[ANSWERED]** — source footage routinely contains a singer
  accompanying the speaker, or music under/between spoken passages. Non-speech audio is a **normal
  property of the input, not a defect**. Segments MUST therefore carry a content classification
  (speech / music / uncertain), and the spoken *message* MUST be separable from sung or musical audio.
  See *Non-Speech Audio Reality* below.
- **Structured transcript** — segments with start/end timestamps — as the internal domain object;
  plain `.txt` is one **export** of it **[BINDING]**. Timestamps MUST NOT be discarded at the ASR boundary.
- **Long-audio handling as a first-class constraint** (see dedicated section), in the use-case layer,
  not inside adapters: chunk planning, overlap stitching, chunk-level progress, chunk-level failure and
  resume, intermediate chunk persistence, job timeouts, and map-reduce summarization.
- **Generation output contract** **[BINDING]**: summary + list of candidate clip moments (each carrying
  source timestamps and a short script), designed so **N script variants** (per network/format) is a
  natural shape, not a later bolt-on.
- **Vertical (9:16) clip rendering** **[rev 4]** — a selected clip candidate is cut from the source and
  reframed to 9:16 with **subject tracking**, because the source is a fixed wide camera and the speaker
  moves within the frame **[ANSWERED — Open Question 10]**. Detection and rendering are separated: see
  *Vertical Reframing Reality*.
- **Burned-in subtitles** **[rev 4]** on the rendered clip, derived from the same structured transcript.
  A sidecar `.srt` is not a substitute — vertical social video is watched muted, and the caption is the
  clip's only channel in that state. This requires **word-level timestamps**, which the current domain
  model does not carry; see the retrofit risk below.
- **Clip export to disk** **[rev 4]** — the rendered clip plus its metadata (title, description, the
  script variant, source timestamps) written to the job directory, ready for the operator to upload.
  **Automatic publishing to social networks is a declared seam, not in this delivery** — see
  Open Question 11.
- **Bootstrapping**: dependency manager = **venv + pip + `requirements.txt`** (skill default — no
  `uv.lock`/`pyproject.toml`/`Pipfile`/`environment.yml` present); test runner = **pytest** recorded as
  `test_command` in `openspec/config.yaml`; **ffmpeg as a system binary** with its own install/verify
  step — it is NOT a pip dependency.

### Out of Scope (explicit non-goals)

- **Generating footage that was never filmed.** AI avatar generation (HeyGen/Synthesia), AI footage
  generation (Veo/Sora/Runway), synthetic actors, AI voice dubbing. Every frame and every word in an
  output clip MUST come from the source sermon. This is the non-goal that actually matters for this
  product: the artifact is a record of something a person said in a church, and a synthesized version
  of it is a different thing wearing its face.
- **Automatic publishing or scheduling to social networks** — declared as `PublishPort` and deliberately
  left unimplemented in this delivery. See Open Question 11.
- Compositing that is not a reframe: B-roll insertion, Ken Burns effects, stock footage, background
  music beds, transitions between unrelated shots.
- Real-time / streaming transcription.
- Multi-user authentication, hosted storage, remote access. This is a single-operator local app.
- Multi-language and code-switching transcription — Spanish only **[ANSWERED]**.
- **Automatic** speaker detection. Diarization is opt-in per job, never inferred from the audio.

> **Speaker diarization** is no longer a non-goal (rev 2). It moved into scope as a conditional, opt-in path.
>
> **Rendering and assembling video** is no longer a non-goal (rev 4). The rev 1-3 text read: *"Rendering,
> assembling, or publishing video. The script/summary artifact is the stopping point and the seam for a
> future change **[BINDING]**."* That boundary was drawn assuming a human editor performed the cut. The
> operator reversed it deliberately: cutting and reframing by hand is the manual labor this change exists
> to remove. **Publishing remains out**, so the seam moved rather than disappeared — it now sits after the
> rendered clip instead of after the script.
>
> Note what did **not** move: programmatic composition of footage that was never filmed. Rev 1-3 bundled
> "programmatic rendering (Remotion/Hyperframes)" together with AI generation in one non-goal. Rev 4
> separates them, because they are not the same risk. Reframing real footage is editing. Synthesizing a
> speaker is fabrication.

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

## Non-Speech Audio Reality (music and singing are input, not noise)

**[ANSWERED]** The source footage is a speaker who is *sometimes* accompanied by a singer, and who
*sometimes* has music underneath or between spoken passages. This is normal material, not a rare defect,
and it breaks three assumptions that were implicit everywhere else in this proposal.

| Failure | Why it happens | Where the damage lands |
| --- | --- | --- |
| **Hallucinated text on non-speech audio** | Whisper-family decoders are autoregressive. Given music or near-silence there is no speech to condition on, so the decoder falls back on its training prior — which was saturated with subtitle tracks. In Spanish it emits plausible subtitle boilerplate (channel sign-offs, thanks-for-watching, subtitle-credit lines) that **was never said**. | The transcript, then everything downstream of it. |
| **Sung lyrics transcribed as speech** | A singing voice is speech-like enough to decode. The lyrics enter the transcript indistinguishable from the speaker's message. | The `.txt` "message" export and the LLM input. |
| **Diarization labels the singer as a speaker** | Diarizers segment by voice, not by intent. In interview mode with a singer present, `SpeakerMode.MULTI` cannot distinguish "second interviewee" from "person singing". | Speaker labels, and any clip candidate attributed to a speaker. |

**Why this is the dangerous class of failure, not a cosmetic one.** It is the same shape as undeclared
diarization and as a hallucinated timestamp, both already called out in this change: *the artifact looks
correct*. A summary built over transcribed lyrics reads fluently. A clip candidate pointing at a chorus
carries a real, resolvable timestamp. Nothing in the output announces that the "message" being summarized
was a song. Under map-reduce this is worse than a local defect — a polluted MAP window produces a polluted
partial summary, which is folded into the REDUCE output, at which point the contamination is no longer
traceable to its source.

**Design response: classify and mark, never silently drop.** Deleting non-speech audio would be the
obvious fix and it is the wrong one for this product. The operator is producing short-form video: a
musical passage or the singer's moment can be *excellent* clip material. The requirement is not "remove
music", it is "**do not let music enter the message**".

Therefore:

- `TranscriptSegment` gains a content classification (`speech` / `music` / `uncertain`). Timestamps are
  retained for every segment regardless of class, so a non-speech range still points back into the footage.
- The `.txt` **message** export and the LLM summarization input are built from speech segments only.
- Clip candidates MAY reference ranges containing non-speech audio, because the timestamps remain valid.
- **An adapter that cannot classify MUST report `uncertain`, never `speech`.** This is the same
  no-silent-degradation invariant already binding on diarization, applied to a second axis. An adapter
  asserting "this is speech" when it merely failed to check is precisely the failure this section exists
  to prevent.

**The audio path makes this worse, not better** **[rev 4]**. Both cameras record a **soundboard feed**,
not a room mic. That is excellent news for the spoken message — the preacher is close-mic'd and clean,
which is the input Whisper handles best and the condition under which it hallucinates least. But the
worship music arrives on that same feed **at full mixed level**, not attenuated by distance across a
room. A room mic at least buries the band under reverb and crowd noise; a board feed delivers it as a
clean, confident, highly decodable signal — sung lyrics in Spanish, well articulated, indistinguishable
from speech to a decoder. **The cleaner the audio path, the more convincing the transcribed lyrics.**
`SegmentKind` therefore matters *more* on this footage than it would on room audio, not less.

**Adapter asymmetry, again.** Non-speech handling is a second axis on which the two adapters are *not* at
parity, and it does not partition the same way diarization does. The local engine (`faster-whisper`)
exposes real controls — a VAD filter, plus the decoder guards (`no_speech_threshold`,
`compression_ratio_threshold`, and disabling `condition_on_previous_text`) that break the degenerate
repetition loops characteristic of hallucination on non-speech. A raw cloud Whisper API exposes far fewer
of these knobs; other cloud providers apply their own VAD server-side and expose different signals again.
The concrete controls per provider are an implementation question for slices 7a/8a, but the *contract*
consequence is settled here: classification is declared per adapter and asserted in the shared contract
suite, not assumed.

## Vertical Reframing Reality (the camera does not move; the preacher does)

**[ANSWERED — Open Question 10]** The sermons are filmed by **one fixed camera on a wide shot** of the
platform. Nobody operates it. The preacher walks, steps away from the pulpit, turns to the
congregation. There are no cuts between angles, because there is only one angle.

That single fact settles three things and creates one real problem.

**Settled — scene detection is unnecessary.** A fixed uncut camera has no shot boundaries to detect.
PySceneDetect and the whole scene-segmentation layer that a general-purpose tool needs is dead weight
here. Likewise the multi-layout machinery (split-screen for two speakers, screencast with webcam inset,
speaker-cut alternation) solves problems this footage does not have.

**Settled — a static centered crop is not viable.** It is the cheap option and it fails on exactly this
input: a 9:16 window carved out of a wide platform shot will hold the pulpit and lose the preacher the
moment he steps left. Subject tracking is required, not a refinement.

**Settled — detection and rendering must be separate concerns.** The technique worth adopting (observed
in `mutonby/openshorts`, `reframe_v2.py`) is *analyze in Python, render natively in ffmpeg*: decode at
reduced resolution (≤640px) purely to locate the subject, emit a **crop trajectory**, then apply that
trajectory in a single native ffmpeg pass. Raw frames are never piped between processes.

This maps onto the existing hexagon without straining it:

| Concern | Where it lives | Proven by |
| --- | --- | --- |
| Where the subject is, frame by frame | `SubjectTrackerPort` adapter (YOLO/MediaPipe) | `localmodel`-marked tests only |
| Smoothing, dead-zone, clamping to frame edges, interpolation across gaps | **Use case, pure** | Default suite, against a fake detector |
| Turning a trajectory into a rendered file | `VideoRenderPort` adapter (ffmpeg `sendcmd`) | `integration`-marked tests |

**The trajectory is a domain object, and that is what protects the existing success criterion.** All the
behavior that is easy to get wrong — jitter, a crop that leaves the frame, what happens across a gap in
detections — is arithmetic over a list of keyframes. It is testable with no weights, no GPU, and no
video, exactly as chunk planning was testable with no ASR. The default `pytest` run continues to load
no model weights; the vision dependency goes in its own `requirements-vision.txt`, alongside the
already-established `requirements-local-asr.txt` and `requirements-diarization.txt`.

**The third no-silent-degradation axis.** A tracker that lost the subject MUST NOT return a centered
crop indistinguishable from a tracked one. This is the same failure shape as an adapter that cannot
diarize returning unlabeled segments, and an adapter that cannot classify returning `speech`: *the
output looks fine*. A clip silently centered on an empty pulpit for nine seconds is a defect nobody
notices until it is published. Therefore each keyframe records its origin — `TRACKED`, `INTERPOLATED`,
or `FALLBACK_CENTER` — and a clip whose trajectory is mostly fallback is reported as such rather than
delivered as a successful reframe. **Mark, never silently substitute**, on a third axis.

**The real problem: a wide shot spends resolution, and the source is not one resolution.**
**[ANSWERED — Open Question 12]** The church records at **1080p on the fixed video camera, and at 4K on
a second camera** (a photography body also used for video) when that one is present. Both are normal
input; neither is the exception.

Cropping 9:16 out of a 16:9 frame keeps at most `height × 9/16` of the width, so the two sources are not
close to equivalent:

| Source | 9:16 crop | Against a 1080×1920 target | Pixels in the delivered clip |
| --- | --- | --- | --- |
| 4K — 3840×2160 | **1214×2160** | downscale 0.89x — **native, with headroom to punch in** | 2.62 MP |
| 1080p — 1920×1080 | **606×1080** | **upscale 1.78x** — visibly soft | 0.65 MP |

Both crop widths are rounded **down to the nearest even integer** — H.264 requires even dimensions, and
rounding down is what keeps the crop inside the frame. `2160 × 9/16` is exactly `1215`, which is odd;
`1080 × 9/16` is `607.5`. The rule and its consequences are stated once in `design.md`.

The 4K path delivers **4.0x the pixel area** (2.62 MP against 0.65 MP). That is the difference between a clip that holds up
full-screen and one that only holds up as a thumbnail.

**Consequence: the renderer must declare its output quality rather than silently upscaling.** A clip
upscaled 1.78x from a 606-pixel-wide crop looks unremarkable in a file listing and soft only once it is
full-screen on a phone — which is to say, once it is published. This is the same failure shape as the
three silent-degradation axes already in this proposal, arriving on a fourth: *the artifact looks fine*.
The render reports whether the clip is native or upscaled, and by how much.

**This requires a domain change that rev 1-3 had no reason to make.** `MediaProbe` currently carries
`duration_s`, `container` and `has_audio` — **no frame dimensions at all**. That was correct while the
product ended at a transcript and the only question about a file was whether it had audio worth
decoding. With rendering in scope it is a gap: neither the crop geometry nor the quality declaration can
be computed without width and height, and `ffprobe` already returns both in the payload the adapter
currently discards.

## Capabilities

### New Capabilities

- `project-bootstrap`: dependency manager, pytest + marker policy, `test_command`, ffmpeg system-binary install/verify.
- `media-ingest`: local web UI HTTP upload, accepted containers, size limits, timeout behavior, **plus the two per-job inputs — speaker mode (default single) and ASR engine selection (local vs cloud)** — including their validation at the boundary.
- `transcription-jobs`: async job lifecycle; **chunk-level progress**, chunk-level failure, **resume**, per-chunk timeouts; **persistence of speaker mode and engine choice on the job record** and their propagation into the use case.
- `audio-extraction`: video container to normalized audio track via ffmpeg adapter, **plus chunk slicing**.
- `speech-transcription`: `TranscriptionPort` contract, two adapters, **capability declaration**, chunk planning, overlap stitching, timestamp preservation, opt-in diarization, **non-speech segment classification and hallucination containment**.
- `transcript-artifacts`: structured `Transcript`/`TranscriptSegment` (with optional speaker **and a content classification**), **intermediate chunk results**, storage, `.txt` export **restricted to the spoken message**.
- `script-generation`: summary + timestamped clip candidates + N script variants, map-reduce over long transcripts, **summarizing speech segments only**.
- `subject-tracking` **[rev 4]**: `SubjectTrackerPort` contract, crop-trajectory domain object, smoothing/clamping/interpolation as pure use-case logic, keyframe provenance (`TRACKED`/`INTERPOLATED`/`FALLBACK_CENTER`), capability declaration and the low-confidence rejection path.
- `clip-rendering` **[rev 4]**: cut a clip candidate from the source, apply the crop trajectory as a single native ffmpeg pass, burn in subtitles derived from the structured transcript, export the clip plus its metadata to the job directory.

### Modified Capabilities

- `transcript-artifacts` **[rev 4]**: `TranscriptSegment` gains **word-level timing**. Burned-in captions cannot be built from segment-level timestamps alone — a Whisper segment routinely spans 5-10 seconds, which is far more text than a vertical frame can hold at a readable size, and re-splitting it without word times means guessing where the words fall. This is a domain-model change on a type that already crosses ports, storage, both ASR adapters and generation. See the retrofit risk.
- `speech-transcription` **[rev 4]**: adapters must surface word timings where the engine provides them (`faster-whisper` supports `word_timestamps=True`), and declare the capability where it does not — the same declaration pattern as diarization and classification, on a fourth axis.

## Approach

Hexagonal architecture. The swappability requirement is the reason for the shape, not decoration.

| Port | Contract intent |
| --- | --- |
| `MediaSourcePort` | Accept an uploaded media stream, persist it, return a stable media reference. Hides HTTP entirely from the core. |
| `AudioExtractorPort` | `SourceMedia` → normalized `AudioTrack`, and `AudioTrack` + chunk plan → `AudioChunk`s. ffmpeg lives behind this and nowhere else. |
| `TranscriptionPort` | `AudioChunk` + requested speaker mode → segments with **start/end timestamps**, text, optional speaker, **and a content classification (speech/music/uncertain)**. Provider-neutral. **Declares its capabilities** (diarization: yes/no; non-speech classification: yes/no) so the use case can reject an impossible job up front instead of degrading silently. An adapter that cannot classify reports `uncertain`, never `speech`. |
| `TextGenerationPort` | Prompt/context → text completion. Provider-neutral. Knows nothing about summaries, clips, or chunking. |
| `TranscriptStoragePort` | Persist and retrieve the job record, **per-chunk intermediate results**, the assembled `Transcript`, and generated artifacts, by job id. Resume is built on this. |
| `SubjectTrackerPort` **[rev 4]** | `SourceMedia` + time range → subject detections at a sampling rate. Provider-neutral (YOLO, MediaPipe, or anything else). **Declares its capabilities**, and reports where it failed to detect rather than substituting a centered guess. Knows nothing about cropping, smoothing, or aspect ratio — it answers "where is the person", nothing more. |
| `VideoRenderPort` **[rev 4]** | `SourceMedia` + time range + `CropTrajectory` + subtitle cues → a rendered vertical file. ffmpeg lives behind this, as it already does for extraction. Knows nothing about *why* the trajectory says what it says. |
| `PublishPort` **[rev 4, declared not implemented]** | Rendered clip + metadata → a published or queued post. Declared now so the shape of `ClipExport` is not accidentally hostile to it later; deliberately unimplemented in this delivery (Open Question 11). |

**Why tracking is a port and not a function inside the renderer.** The renderer takes a trajectory as
data. That keeps every decision worth testing — smoothing, dead-zone, clamping, gap interpolation,
what counts as "lost the subject" — in a use case above the ports, provable against a fake detector
with no model weights. It also means swapping YOLO for MediaPipe, or for a hand-authored trajectory,
never touches the renderer. The alternative (a detect-and-render adapter) would put that arithmetic
behind a `localmodel` marker and out of the default suite, which is precisely the trade the hexagon
exists to refuse.

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
| `src/onevoicecut/domain/` | New | `SourceMedia`, `AudioTrack`, `AudioChunk`, `ChunkPlan`, `ChunkResult`, `Transcript`, `TranscriptSegment`, `SegmentKind`, `SpeakerMode`, `EngineChoice`, `JobRecord`, `ClipCandidate`, `ScriptVariant`; **[rev 4]** `WordTiming`, `CropKeyframe`, `CropTrajectory`, `KeyframeOrigin`, `SubtitleCue`, `RenderedClip` |
| `src/onevoicecut/ports/` | New | Five port protocols + capability declaration; **[rev 4]** `SubjectTrackerPort`, `VideoRenderPort`, and `PublishPort` declared but unimplemented |
| `src/onevoicecut/usecases/` | New | Ingest, chunk plan/transcribe/stitch/resume, map-reduce generate; **[rev 4]** trajectory planning (smoothing/clamping/interpolation), subtitle cue building, clip render orchestration |
| `src/onevoicecut/adapters/` | New | web/upload, ffmpeg, local ASR, cloud ASR, LLM, filesystem storage; **[rev 4]** vision tracker, ffmpeg vertical renderer |
| `requirements-vision.txt` | **New [rev 4]** | Object/face detection weights, kept out of the default install for the same reason as local ASR and diarization |
| `pytest.ini` | Modified **[rev 4]** | The existing `localmodel` marker now also covers vision weights; no new marker needed |
| `tests/` | New | Fast unit suite (fakes) + marked adapter contract tests |
| `README.md` | New | ffmpeg install step, diarization setup caveats, run instructions; **[rev 4]** vision model setup, and the source-resolution guidance from Open Question 12 |

## Size Forecast (800-line review budget)

**800-line budget risk: High. This does NOT fit in one 800-line review, and it grew.**

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
| 11 **[rev 4]** | Domain gaps rendering exposed: `WordTiming` on the segment (through storage codec, stitcher and fakes) **plus frame dimensions on `MediaProbe`** | ~400 | Captions and crop geometry become possible at all |
| 12 **[rev 4]** | `SubjectTrackerPort` + `CropTrajectory` + trajectory planning use case, against a fake detector | ~600 | The reframe arithmetic, with no model weights |
| 13 **[rev 4]** | Vision tracker adapter (`localmodel`) + ffmpeg vertical renderer + subtitle burn-in + clip export | ~700 | A real 9:16 subtitled clip on disk |

**Slice 10 remains above budget** and should be split at `sdd-tasks` time (map-reduce summary, then
clip candidates + variants). **Slices 1 and 4 sit at the ceiling** and have no headroom for surprises.
`delivery_strategy: ask-on-risk` → **a delivery decision is required before apply.**

### Rev 4 estimate honesty (read this before trusting the three numbers above)

Every slice measured so far overran: slice 1 by 3.35x, 1b by 3.2x, 3a and 3b by 3.9x, **4a by 4.8x**.
The measured test share is now **68%**, not the 56% the rev-3 calibration assumed. Applying the measured
factor rather than the estimate, slices 11-13 are realistically **~4,000-6,000 lines, not ~1,650** —
which would roughly double the whole change.

Two of the three carry categories with no measured comparable, which is exactly what produced the 4a
miss:

- **Slice 12** is pure geometry and should behave like the well-measured pure-logic slices (2a, 2b). It
  is the one number here with real grounding.
- **Slice 13** introduces *two* first-of-their-kind adapters at once (a vision runtime and a video
  renderer). The 4a retro established that an adapter which also owns its own format runs ~1,600 lines.
  Two of them is not ~700. **This slice must be split at `sdd-tasks` time, not at apply time.**
- **Slice 11** is a retrofit through a type that already crosses ports, storage, the stitcher and every
  fake — the shape the rev-3 risk table already flagged as expensive when it justified landing
  `SegmentKind` early as slice 1b. That warning applies here, minus the option of landing it early.

**Treat these three numbers as lower bounds.**

## Risks

| Risk | Likelihood | Mitigation |
| --- | --- | --- |
| ~~No git repository exists~~ — **RESOLVED after this proposal was written.** `git init -b main` was run and a `.gitignore` was added. Nothing is committed yet. | Resolved | Remaining prerequisite for slice 1: an initial commit, so per-slice rollback is `git revert`. |
| Total work far exceeds the 800-line budget, and grew from ~1,800 to ~2,850 lines | High | Ten chained slices above; confirm strategy before apply |
| **Multi-hour as the normal case makes every long-audio defect a routine defect, not an edge case** | High | Chunking and the chunk-aware job model move to slices 2 and 4, ahead of any real engine |
| **Local diarization needs an extra component (`pyannote.audio`/`WhisperX`, extra weights, gated license)** — not parity with the cloud flag | High | Stated plainly above; capability declaration on the port; rejection instead of silent single-speaker output |
| Silent degradation: an adapter returning unlabeled output for a speaker-mode job looks like success | Med | Explicit rejection path is a spec requirement, tested in slice 9 |
| **ASR hallucination on music/near-silence injects text that was never spoken** — and the output looks entirely plausible | **High** (music is normal input, not an edge case) | `SegmentKind` classification landed before any real engine; engine-level VAD/decoder guards in slices 7a/8a; hallucination-containment scenario in `speech-transcription` |
| **Sung lyrics enter the "message" and are summarized as if the speaker said them**; under map-reduce the pollution folds into REDUCE and stops being traceable. **Sharpened in rev 4**: the source is a soundboard feed, so worship music arrives clean and at full mixed level rather than buried in room reverb — the cleaner the path, the more convincing the transcribed lyrics | **High** | Message export and LLM input are speech-only by spec; classification asserted in the shared contract suite |
| **Diarization attributes segments to a singer** in interview-plus-music material | Med | Documented limitation; speaker labels stay namespaced per chunk; classification lets the operator see which labelled segments are musical |
| Retrofit cost if `SegmentKind` were deferred — `TranscriptSegment` crosses ports, stitcher, storage adapter, both ASR adapters and generation | Med | **Mitigated by ordering**: landed as slice 1b, before `adapters/` and `runtime/` exist at all |
| Disk consumption from multi-hour source video + extracted audio + chunk files | Med | Flagged as Open Question 6, now sharper; `.gitignore` already keeps media out of the repo |
| ffmpeg missing/not on PATH at runtime | Med | Explicit install step + startup verification with an actionable error |
| Resume correctness (partial chunk writes, duplicated work after crash) | Med | Chunk results written atomically; resume tested against fakes in slice 4 |
| Long-form ASR quality varies sharply by provider (~7% vs ~43.8% WER in one 2026 benchmark), and every job here is long-form | Med | Two adapters behind one port; evaluate on real multi-hour Spanish content, not short clips |
| Cloud ASR/LLM per-minute cost leaking into CI or default test runs — worse now, since diarization add-ons bill on top | Med | Marked integration tests excluded from default suite |
| ~~Scope creep toward actual video generation~~ — **SUPERSEDED in rev 4.** Rendering is now in scope by decision, not by drift. The residual risk is different: creep from *editing real footage* into *generating footage*. | Med | Redrawn non-goal: every frame and word in an output clip comes from the source sermon. No synthesis, no avatars, no dubbing, no B-roll |
| **Word-level timing is a retrofit through a type that already crosses every layer.** `TranscriptSegment` is in the storage codec, the stitcher, both fakes, and every construction site in the test suite. The rev-3 table flagged exactly this shape for `SegmentKind` and mitigated it by landing it as slice 1b, *before* adapters existed. That option is gone — `adapters/storage` and `adapters/ffmpeg` now exist | **High** | Land it as its own slice (11) before any rendering work, and treat the codec's explicit field-by-field decoding as the thing that makes the migration visible rather than silent |
| **The 1080p path cannot deliver a native vertical clip.** A 9:16 crop of 1920×1080 yields 606×1080 against a 1080×1920 target — a 1.78x upscale before any punch-in on a distant preacher | **High** on 1080p sources, **absent** on 4K | Not solvable in software; the camera sets the ceiling. Mitigated by *declaring* it: the render reports native vs upscaled rather than quietly producing a soft clip. Prefer the 4K source when the service has one |
| **`MediaProbe` carries no frame dimensions**, so neither the crop geometry nor the quality declaration is computable today | **High** (blocks slices 12-13 outright) | Small, well-bounded fix: `ffprobe` already returns width/height in the payload the adapter currently discards. Land it with the word-timing retrofit in slice 11 — both are domain-model gaps that rendering exposed |
| **A tracker that lost the subject returns a plausible centered crop** — a clip framed on an empty pulpit looks like a successful render | **High** (same shape as the two silent-degradation failures already in this table) | Keyframe provenance (`TRACKED`/`INTERPOLATED`/`FALLBACK_CENTER`); a mostly-fallback trajectory is reported, not delivered as success |
| Vision weights leaking into the default test run, breaking the "no model weights by default" criterion | Med | Detection behind `SubjectTrackerPort`; all trajectory arithmetic proven against a fake detector; real adapter tests carry the existing `localmodel` marker; `requirements-vision.txt` kept out of the default install |
| **Rev 4 roughly doubles the change**, and it is being added to a change already running 3-5x over every estimate | **High** | Estimate honesty section above; slice 13 split mandated at `sdd-tasks` time; the alternative of shipping rev 1-3 first and rendering as a separate change stays available |
| Render time on multi-hour source: detection sampling plus a native ffmpeg pass, per clip | Med | Detection runs only over the selected clip range, never the whole sermon — the clip candidates already narrowed it to minutes |
| Chunk-boundary word loss when stitching | Med | Overlap handling is an explicit spec requirement, not adapter discretion |

## Rollback Plan

1. **Prerequisite**: the repository now exists (`git init -b main`, `.gitignore` added). The remaining
   prerequisite before slice 1 is an **initial commit**, so rollback is `git revert <slice>` or
   `git reset --hard`.
2. Per-slice rollback: each slice is additive. Revert its commit; earlier slices keep passing because
   every slice ends with a green default suite.
3. ffmpeg is a system binary installed outside the project; uninstalling it is optional and independent.
   The same applies to any local diarization model weights pulled in at slice 9, and to the vision
   weights pulled in at slice 13 **[rev 4]** — they live outside the repository and are already
   `.gitignore`d.
4. No external state is mutated: no database, no remote service writes, no published artifacts.
   Rolling back a slice may leave per-job working directories, intermediate chunk files and **rendered
   clips [rev 4]** on disk; these are inert data, safe to delete manually.
   **This property is what Open Question 11 protects.** It holds only while publishing stays out of
   scope — the moment a clip is posted to a social network, rollback stops being a local operation and
   this clause becomes false. That is the real cost of flipping OQ 11, and it is not a technical
   inconvenience: an unpublish is a different act from a revert, and some networks do not offer one.

## Dependencies

- **ffmpeg** system binary (not pip) — required before slice 3.
- Python 3 + venv + pip.
- A cloud ASR API key (slice 8) and an LLM API key (slice 10) — required only for marked integration
  tests and real runs, never for the default suite.
- **Local diarization component** (`pyannote.audio` or `WhisperX`, plus model weights and a gated
  Hugging Face license acceptance) — required only for slice 9's local speaker-mode path.
- **Vision detection component** **[rev 4]** (`requirements-vision.txt`: a person/face detector plus its
  weights) — required only for slice 13's real tracker. The trajectory arithmetic in slice 12 needs none
  of it. ffmpeg is already a dependency and gains no new requirement: the vertical render is a filter
  pass, not a new binary.
- An **initial commit** — remaining prerequisite (see Rollback).

## Open Questions & Stated Assumptions

| # | Question | Status |
| --- | --- | --- |
| 1 | Source audio language(s)? Multi-language / code-switching? | **ANSWERED — Spanish only.** No multi-language, no code-switching. Promoted to a stated requirement in scope. |
| 2 | Is speaker diarization needed? | **ANSWERED — conditional and opt-in.** Mostly one voice to camera; multi-speaker material exists but is rare. Default is single-voice/talking-head; a per-job option declares two or more speakers. No longer a non-goal. |
| 3 | Which social networks/formats matter for script variants? | **OPEN — and it stays data, not structure, only because publishing is out.** With export-to-disk as the delivery, the answer sets duration and aspect presets and the number of script variants. It would become structural the moment Open Question 11 flips to automatic publishing, since each network is then its own adapter and its own credential. |
| 4 | Typical and worst-case video duration? | **ANSWERED — always multi-hour.** Multi-hour is the normal case, not the tail. Supersedes the previous "typical 10–60 min" assumption and drives the whole job model. |
| 5 | Where do transcripts and outputs live between steps? | **OPEN.** Assumed local filesystem, per-job directory behind `TranscriptStoragePort`, no database. Now also has to hold intermediate chunk results. |
| 6 | Is retention/cleanup of uploaded video required? | **OPEN — and sharper now.** With multi-hour video as the normal case, each job stores a large source file plus extracted audio plus chunk files. Without a retention policy, disk consumption grows without bound. Assumed for now: no automatic deletion. This assumption is the most likely to cause a real operational problem. |
| 7 | Local vs cloud ASR as the default adapter? | **ANSWERED — no global default; selectable per job.** Content-dependent: sensitive material goes local, the rest may go cloud. Both adapters are first-class from slice 6 onward. |
| 8 | Does the source audio contain non-speech (music, singing)? | **ANSWERED — yes, routinely.** The speaker is sometimes accompanied by a singer, sometimes over background music. Music is normal input. Drives `SegmentKind`, speech-only message export, speech-only summarization input, and hallucination containment. See *Non-Speech Audio Reality*. |
| 9 | Should musical/sung ranges be actively **promoted** as clip candidates, or merely permitted? | **OPEN.** Settled so far: they are *permitted* — timestamps are retained and a candidate MAY reference a non-speech range. Whether generation should additionally *favor* them (a singer's moment is often strong short-form material) is a ranking-policy question that changes prompt and scoring in slice 10b only, not any type. **Rev 4 raises the stakes**: with rendering in scope, a promoted musical range becomes a rendered worship clip, which is plausibly the single most shareable artifact this system could produce. |
| 10 | How is the source footage filmed? | **ANSWERED (rev 4) — one fixed camera, wide shot, no operator, no cuts.** The preacher moves within a static frame. Kills scene detection and multi-layout as unnecessary; makes subject tracking mandatory rather than optional. See *Vertical Reframing Reality*. |
| 11 | Automatic publishing to social networks, or export to disk? | **ASSUMED — export to disk for this delivery.** Stated so it is a live decision, not a buried one. Rationale: per-network OAuth, token refresh, rate limits and new stored credentials is heavy machinery for a single operator posting occasionally, and automatic publishing breaks the rollback property *"no external state is mutated"* that holds today. `PublishPort` is declared so flipping this later is an added adapter, not a reshape. **The operator's stated goal does include publishing** — this defers it, it does not deny it. |
| 12 | What is the source footage's actual resolution? | **ANSWERED (rev 4) — both. 1080p on the fixed video camera, 4K on a second (photography) camera when present.** Neither is the exception, so resolution is a **per-job property**, not a global assumption. 4K crops native with headroom; 1080p upscales 1.78x from a 608-pixel-wide crop. Drives the `MediaProbe` dimensions gap and the quality declaration. Still unmeasured: how large the preacher is within the wide frame, which decides how much punch-in the 1080p path can afford before it stops being usable. |
| 14 | Two cameras record the same sermon. Is a job **one file**, or **two files aligned to each other**? | **ANSWERED (rev 4) — one file per job. No alignment, no `AlignmentPort`, no drift estimation.** Both cameras take a **soundboard feed** **[ANSWERED]**, so the 4K file carries audio just as good as the 1080p one. There is nothing to gain by transcribing one camera and rendering the other: when a 4K file exists it is simply the better input on both axes and the job uses it alone. This removes an entire subsystem — offset and drift estimation across a 90-minute recording, plus a misalignment failure that cuts the wrong sentence while looking correct — from the change. The residual operator guidance is trivial by comparison: **prefer the 4K file when the service has one.** |
| 13 | Which clip candidates get rendered — all of them, or an operator selection? | **OPEN.** Rendering every candidate of a multi-hour sermon is expensive and mostly wasted; rendering none until asked adds a step. Assumed for now: the operator selects from the candidate list, and rendering is explicit. Changes the job model's terminal states, so it should be settled before slice 13. |

## Success Criteria

- [ ] `openspec/config.yaml` has a real, runnable `test_command`; `strict_tdd` is actually enforceable.
- [ ] A multi-hour video uploaded through the local web UI produces a `.txt` transcript without blocking the HTTP request.
- [ ] Job progress is reported at chunk granularity, not as a single "running" state.
- [ ] A job interrupted mid-run resumes from the first incomplete chunk without redoing completed work.
- [ ] The operator selects the ASR engine per job, and the selection is recorded on the job record.
- [ ] The speaker-mode option defaults to single voice, and declaring two or more speakers produces speaker-labelled segments — or an explicit rejection when the selected adapter cannot diarize.
- [ ] The same use-case tests pass against both ASR adapters on the single-speaker path, swapped by configuration only.
- [ ] Transcript segments retain start/end timestamps end to end, and clip candidates reference them.
- [ ] Every transcript segment carries a content classification, and an adapter that cannot classify reports `uncertain` rather than asserting `speech`.
- [ ] The `.txt` message export and the LLM summarization input contain no segment classified as music.
- [ ] A source passage that is music or singing does not contribute invented text to the transcript, and its timestamps remain available to clip candidates.
- [ ] A multi-hour transcript produces a summary via map-reduce without exceeding LLM context.
- [ ] The generation output can carry more than one script variant without a structural change.
- [ ] Every transcript segment carries word-level timing, or its adapter declares it cannot produce it.
- [ ] A crop trajectory is smoothed, clamped inside the source frame, and interpolated across detection gaps — all proven with no model weights loaded.
- [ ] Every trajectory keyframe records whether it was tracked, interpolated, or fell back to centre, and a mostly-fallback trajectory is reported rather than delivered as a successful reframe.
- [ ] A selected clip candidate renders to a 9:16 file with burned-in subtitles, in a single native ffmpeg pass, with the subject in frame throughout.
- [ ] `MediaProbe` reports frame dimensions, and the render declares whether the delivered clip is native or upscaled — a 1080p source never yields a soft clip that is presented as a successful render.
- [ ] Every frame and every word in a rendered clip comes from the source sermon — nothing synthesized, dubbed, or composited from elsewhere.
- [ ] The rendered clip and its metadata land in the job directory, and no external service is written to.
- [ ] **The default `pytest` run invokes no paid API and no real local model — including no vision weights.** This criterion predates rev 4 and survives it unchanged; it is the one rev 4 was most likely to break.
