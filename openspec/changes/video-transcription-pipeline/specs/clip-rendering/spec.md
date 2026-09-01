# Clip Rendering Specification

## Purpose

Defines `VideoRenderPort`: cutting a selected clip candidate from the source, applying its
`CropTrajectory` as a single native ffmpeg pass, burning in subtitles derived from the structured
transcript, declaring output quality rather than silently upscaling, and exporting the rendered clip
plus its metadata to the job directory. [BINDING: every frame and word in the output comes from the
source sermon — no synthesis, no dubbing, no composited footage]

## Requirements

### Requirement: VideoRenderPort Contract

`VideoRenderPort` MUST accept a source media reference, a time range, a `CropTrajectory`, and subtitle
cues, and MUST return a rendered vertical clip file. The port MUST know nothing about why the
trajectory says what it says — trajectory construction is a separate concern owned by
`subject-tracking`.

#### Scenario: Render produces a vertical file

- GIVEN a source media reference, a clip time range, a `CropTrajectory`, and subtitle cues
- WHEN the port renders the clip
- THEN it MUST produce a 9:16 vertical video file covering that time range

### Requirement: Single Native ffmpeg Pass

Cropping, reframing, and subtitle burn-in MUST be applied within a single native ffmpeg process
invocation. Raw decoded frames MUST NOT be piped between separate processes at any point in the render
pipeline — the same constraint that already governs `AudioExtractorPort`'s ffmpeg usage, applied to
rendering.

#### Scenario: Render pipeline invokes ffmpeg once

- GIVEN a clip render request
- WHEN rendering executes
- THEN the crop, reframe, and subtitle burn-in MUST all be applied by a single ffmpeg process invocation
- AND no intermediate raw-frame file or pipe MUST cross a process boundary between decode and encode

### Requirement: Clip Cut From Source Time Range Only

`VideoRenderPort` MUST cut only the selected clip candidate's time range from the source. It MUST NOT
require decoding or processing the full multi-hour source to render one clip.

#### Scenario: Only the candidate's range is processed

- GIVEN a clip candidate with a start and end timestamp from a multi-hour source
- WHEN the clip is rendered
- THEN only that time range MUST be extracted and processed
- AND the render MUST NOT depend on processing footage outside the candidate's range

### Requirement: Crop Trajectory Applied As Given

The renderer MUST apply the `CropTrajectory` it is given as opaque geometric data. It MUST NOT
recompute, override, or second-guess the smoothing, clamping, or fallback decisions already made by the
trajectory-building use case.

#### Scenario: Renderer does not alter trajectory geometry

- GIVEN a `CropTrajectory` produced by the trajectory-building use case
- WHEN the clip is rendered
- THEN the applied crop geometry MUST match the trajectory's keyframes
- AND the renderer MUST NOT independently recompute smoothing, dead-zone, or clamping

### Requirement: Low-Confidence Trajectory Is Not Delivered as an Ordinary Success

A render whose input `CropTrajectory` was reported low-confidence (see `subject-tracking`:
Mostly-Fallback Trajectory Reported, Not Delivered as Success) MUST propagate that signal on the
rendered result rather than discard it. The render result MUST NOT present a mostly-fallback reframe
indistinguishably from a well-tracked one.

#### Scenario: Low-confidence trajectory carries through to the render result

- GIVEN a `CropTrajectory` flagged low-confidence
- WHEN the clip is rendered
- THEN the render result MUST carry that low-confidence signal
- AND it MUST NOT be presented as an ordinary successful render

### Requirement: Subtitle Burn-In From Structured Transcript

Subtitles MUST be burned into the rendered video frame, derived from the structured transcript's
word-level timing (see `transcript-artifacts`: Word-Level Timing). A sidecar `.srt` file MUST NOT be
the only subtitle delivery — vertical social video is watched muted, and the burned-in caption is the
clip's sole channel in that state.

#### Scenario: Rendered clip carries burned-in captions

- GIVEN a clip candidate's transcript range with word-level timing
- WHEN the clip is rendered
- THEN the output file MUST contain burned-in subtitle text
- AND the subtitle timing MUST derive from the transcript's word-level timestamps, not from a manually
  authored source

#### Scenario: Cues are split for on-screen readability

- GIVEN a transcript segment spanning several seconds of speech
- WHEN subtitle cues are built for burn-in
- THEN the segment MUST be split into cues sized for on-screen readability using word-level timing
- AND a single cue MUST NOT span the segment's full multi-second duration if that exceeds what the
  frame can hold at a readable size

### Requirement: Cue Eligibility Is Decided by `SegmentKind`, and Coverage Is Declared

Which transcript segments become subtitle cues MUST be decided by `SegmentKind`, using the same
message-facing selector every other consumer uses: `MUSIC` segments MUST NOT become cues, while `SPEECH`
and `UNCERTAIN` segments MUST. A `MUSIC` segment MUST keep its timestamps and remain addressable as clip
material — it is excluded from captioning, never filtered out of the transcript.

`UNCERTAIN` segments MUST be included rather than excluded, because an adapter that cannot classify marks
every segment `UNCERTAIN`, and excluding them would leave a muted vertical clip with a silently blank
caption channel. Their uncertainty MUST NOT be rendered as an in-frame marker; it MUST be declared as
structured metadata on the render result instead.

The render result MUST therefore declare its caption coverage, and that declaration MUST be computed from
**one basis only: the eligible segments overlapping the clip's span.** It declares that every eligible
segment was confirmed speech, that at least one was unverified audio, or that the span carried no eligible
segment at all.

Cue construction SHOULD be total over that same eligible set. Where an eligible segment yields no cue, the clip MUST still declare its coverage honestly rather than report captions it does not carry.
one cue. Totality is what makes the declaration describe the delivered captions: zero cues and no eligible
segment are then the same condition, so a clip with zero cues MUST be reachable only because the span
contained no eligible segment, and MUST be declared as such rather than delivered as an ordinarily
captioned clip. Conversely, a clip declared as confirmed-speech or unverified coverage MUST carry at least
one cue.

#### Scenario: Music segments are excluded from cues but keep their timestamps

- GIVEN a clip span containing both `SPEECH` and `MUSIC` segments
- WHEN subtitle cues are built
- THEN no cue MUST be produced from a `MUSIC` segment
- AND the `MUSIC` segment MUST remain present in the transcript with its timestamps intact

#### Scenario: Unclassified audio is captioned and declared, not dropped

- GIVEN a clip span whose segments are all `UNCERTAIN`, as produced by an adapter that cannot classify
- WHEN subtitle cues are built
- THEN cues MUST be produced from those segments rather than yielding an empty caption set
- AND no uncertainty marker MUST appear in the burned-in caption text
- AND the render result MUST declare that its captions include unverified audio

#### Scenario: Confirmed-speech coverage is declared as such

- GIVEN a clip span whose eligible segments are all `SPEECH`
- WHEN the clip is rendered
- THEN the render result MUST declare that every eligible segment was confirmed speech
- AND the declared coverage MUST match the cues the clip actually carries

#### Scenario: A clip with no eligible segment declares zero coverage

- GIVEN a clip span containing only `MUSIC` segments
- WHEN the clip is rendered
- THEN the subtitle cue set MUST be empty
- AND the render result MUST declare that the clip carries no captions, rather than presenting it as an
  ordinarily captioned clip

### Requirement: Missing Word Timing Is Declared, Not Silently Degraded

Where the source segments for a clip's subtitle range lack word-level timing (an adapter declared no
word-timing support — see `transcript-artifacts`), the renderer MUST NOT silently fall back to
guessed or evenly-distributed word timings and present the result as ordinary captioning. The render
result MUST record whether its subtitle cues were built from word-level timing or degraded to
segment-level timing.

#### Scenario: Clip built from segment-level fallback is declared

- GIVEN a clip whose transcript range has no word-level timing available
- WHEN the clip is rendered with subtitles
- THEN the render result MUST declare that subtitle cues used segment-level timing, not word-level
  timing

#### Scenario: Clip built from word-level timing is declared as such

- GIVEN a clip whose transcript range has word-level timing available
- WHEN the clip is rendered
- THEN the render result MUST declare that subtitle cues used word-level timing

### Requirement: Output Quality Declaration

The renderer MUST report whether the delivered clip's resolution is native or upscaled relative to the
source crop, and by what factor, rather than silently producing a soft clip. This is the fourth
no-silent-degradation axis: a 1080p source upscaled 1.78x from a 606-pixel-wide crop looks unremarkable
in a file listing and soft only once published full-screen on a phone.

Crop dimensions MUST be even, and MUST be produced by rounding **down** to the nearest even integer, so
that the crop is never wider than the frame it is taken from. The reference derivations are `1214×2160`
from a 3840×2160 source and `606×1080` from a 1920×1080 source.

#### Scenario: 4K source declared native

- GIVEN a source whose 9:16 crop meets or exceeds the target output resolution
- WHEN the clip is rendered
- THEN the render result MUST declare the output as native resolution

#### Scenario: 1080p source declared upscaled with its factor

- GIVEN a source whose 9:16 crop is narrower than the target output resolution
- WHEN the clip is rendered
- THEN the render result MUST declare the output as upscaled
- AND it MUST report the upscale factor

#### Scenario: Quality declaration is queryable without inspecting the file

- GIVEN a completed render
- WHEN the render result is inspected
- THEN the native-vs-upscaled declaration MUST be available as structured metadata, not only inferable
  by visually inspecting the video

### Requirement: Clip Export to Job Directory

The rendered clip file and its metadata (title, description, the script variant used, source
timestamps, and the output-quality declaration) MUST be written to the job directory, keyed by job id,
consistent with the per-job storage isolation already required of transcript artifacts.

#### Scenario: Clip and metadata land in the job directory

- GIVEN a completed render for a job
- WHEN the export runs
- THEN the rendered clip file MUST be written under that job's directory
- AND its metadata MUST be written alongside it, retrievable by job id

#### Scenario: No external service is written to

- GIVEN a completed clip export
- WHEN the export runs
- THEN no request MUST be made to any social network or publishing service
- AND the export MUST be a purely local filesystem operation

### Requirement: Rendered Content Originates Only From the Source

Every frame and every word appearing in a rendered clip MUST originate from the source sermon media.
The renderer MUST NOT composite, synthesize, dub, or insert visual or audio content absent from the
source — this is the binding non-goal on generated footage, restated as a testable rendering
constraint.

#### Scenario: Render inputs are limited to source-derived material

- GIVEN a render request
- WHEN its inputs are inspected
- THEN they MUST consist only of the source media, a `CropTrajectory` computed from that same source,
  and subtitle cues derived from that same source's transcript
- AND no external image, audio, or video asset MUST be accepted as a render input
