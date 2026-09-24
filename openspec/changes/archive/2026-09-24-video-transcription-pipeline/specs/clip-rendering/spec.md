# Clip Rendering Specification

## Purpose

Defines `VideoRenderPort`: cutting a selected clip candidate from the source, applying its
`CropTrajectory` as a single native ffmpeg pass, burning in subtitles derived from the structured
transcript, declaring output quality rather than silently upscaling, and exporting the rendered clip
plus its metadata to the job directory. [BINDING: every frame and word in the output comes from the
source sermon — no synthesis, no dubbing, no composited footage]

**[rev 5 — proposal Open Question 3 answered]** Delivery is to more than one network, and the networks
do not want identical files. Rendering is therefore parameterised by a **render profile**: the output
spec, the caption safe area, and the duration ceiling that a destination implies. Profiles are named
by script targets (see `script-generation`) and resolved here, where the frame is known. A clip is
rendered once per *distinct profile*, never once per network — two networks that want the same file
share it.

## Requirements

### Requirement: VideoRenderPort Contract

`VideoRenderPort` MUST accept a source media reference, a time range, a `CropTrajectory`, subtitle
cues, and the **render profile** the output is being delivered under, and MUST return a rendered clip
file matching that profile's output spec. The port MUST know nothing about why the trajectory says
what it says, nor which networks the profile serves — trajectory construction is owned by
`subject-tracking`, and the network-to-profile mapping by `script-generation`.

Every profile delivered by this change is vertical 9:16. The port MUST NOT hardcode that: the aspect
is a property of the profile's output spec, and a profile declaring another shape MUST render to it
without a change to the port. The trajectory arithmetic is already aspect-parametric, so this costs
nothing to honour and prevents an assumption from being welded into the adapter.

#### Scenario: Render matches the profile's output spec

- GIVEN a source media reference, a clip time range, a `CropTrajectory`, subtitle cues, and a render
  profile
- WHEN the port renders the clip
- THEN it MUST produce a video file covering that time range at the profile's declared output
  dimensions

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

### Requirement: One Render Per Distinct Profile, Not Per Network

A clip candidate MUST be rendered once for each **distinct** render profile among its script targets,
not once per target. Where several networks name the same profile, one rendered file MUST serve all of
them.

This is a correctness requirement before it is an efficiency one. Rendering per network would put N
byte-identical files in a job directory with nothing to distinguish them, and an operator with four
copies of one clip has no way to know they are the same — which is how the wrong one gets published
after a re-render. It is also the cost control: a render is a full ffmpeg pass over the clip range,
and multi-hour sources with no retention policy (proposal Open Question 6) already make disk the
sharpest operational constraint in this change.

A render profile MUST declare its output spec, its caption safe area, and its duration ceiling. Its
aspect ratio MUST be derived from the output spec rather than declared alongside it — two fields that
can disagree about one fact will eventually disagree, and the derived one is the one nobody can
contradict.

#### Scenario: Networks sharing a profile share one file

- GIVEN a clip candidate with script variants for four networks, three of which name the same render
  profile
- WHEN the clip is rendered
- THEN exactly two rendered files MUST be produced
- AND the three variants naming the shared profile MUST reference the same rendered file

#### Scenario: Distinct profiles produce distinct files

- GIVEN a clip candidate with script variants naming two different render profiles
- WHEN the clip is rendered
- THEN each profile MUST produce its own rendered file
- AND each file MUST carry its own quality, caption and tracking declarations

### Requirement: Detection Is Shared Across Profiles; Trajectory Planning Is Not

Subject detection MUST run at most once per clip span regardless of how many profiles that clip is
rendered under. Trajectory planning MUST run once per distinct aspect ratio among those profiles.

The split is not an optimisation choice — it is what `SubjectTrackerPort` already is.
`detect(media, span, sample_hz)` takes no aspect and no policy: it answers where a person was found in
the source frame, which is the same answer whatever shape is cropped around it. Aspect enters only at
`build_trajectory`, through the policy, where it decides the crop size and therefore the clamping.

So the expensive half (vision weights, span-bounded decode) is invariant across profiles, and the half
that must be repeated is pure arithmetic provable against a fake detector. Re-detecting per profile
would multiply the one cost in this pipeline that model weights dominate, to obtain an identical
answer.

#### Scenario: Multiple profiles do not multiply detection

- GIVEN a clip rendered under three profiles
- WHEN rendering runs
- THEN `SubjectTrackerPort.detect` MUST be invoked at most once for that clip span

#### Scenario: A differing aspect gets its own trajectory

- GIVEN two profiles whose output specs imply different aspect ratios
- WHEN trajectories are planned from one shared detection set
- THEN each aspect MUST receive its own `CropTrajectory`
- AND neither trajectory MUST be reused for the other aspect

### Requirement: Caption Safe Area Is Declared Per Profile, Never Defaulted

Burned-in caption placement MUST be derived from the rendering profile's declared safe area. A profile
MUST NOT inherit a caption margin from another profile, and a profile that declares no safe area MUST
be refused at configuration time rather than rendered with a fallback margin.

**This is a fifth no-silent-degradation axis, and it is the least visible of the five.** The other four
are discoverable by inspecting the file. A caption sitting under a destination's interface overlay is
correct in the file, correct in a local player, and wrong only in the app it was made for — which is
to say, wrong only after it is published, and only to the audience. Nothing in the rendered artifact
announces it. A shared default margin is exactly how that failure gets introduced: it is right for the
profile it was measured against and silently wrong for every profile that inherited it.

The safe area MUST be expressed as fractions of the output frame rather than pixels, so that a profile
retargeted to another resolution keeps its meaning instead of silently changing where the caption
lands.

The burned-in subtitle script MUST declare the resolution its typography is measured against, so that
font size resolves against the profile's actual output frame. Without that declaration a font size is
interpreted against a renderer default, which is invisible while one profile exists and becomes a
different apparent caption size per profile the moment a second one does.

The concrete safe-area values per destination are configuration, not specification: they are measured
against a destination's current interface and change when that interface does. This requirement fixes
that they MUST be declared, measured, and per profile — not what they are.

#### Scenario: Caption placement follows the profile

- GIVEN two profiles declaring different caption safe areas
- WHEN a clip is rendered under each
- THEN each rendered file's caption placement MUST derive from its own profile's safe area
- AND neither MUST use the other's margin

#### Scenario: A profile without a declared safe area is refused

- GIVEN a render profile configuration that declares no caption safe area
- WHEN profiles are resolved
- THEN the system MUST refuse with an error naming that profile
- AND it MUST NOT render it with a default or inherited margin

#### Scenario: Typography resolves against the output frame

- GIVEN a rendered clip carrying burned-in captions
- WHEN the generated subtitle script is inspected
- THEN it MUST declare the reference resolution its font sizing is measured against
- AND that resolution MUST match the profile's output spec

### Requirement: Duration Ceiling Is Declared, Not Silently Trimmed

A render profile MUST declare the maximum clip duration its destination accepts. Where a selected clip
candidate's range exceeds that ceiling, the render result MUST declare the overrun rather than trim the
range to fit.

Trimming is an editorial act. Cutting eight seconds off a clip to satisfy a limit removes either the
setup or the payoff, and which one is a judgement about the material that no rule in this system is
positioned to make. A silently trimmed clip is also the familiar shape: it plays cleanly, it is the
right length, and the sentence it was built around is gone.

The candidate's range remains the source of truth. This requirement adds a declaration, never a second
place where a timestamp changes.

#### Scenario: An over-length clip is declared, not cut

- GIVEN a clip candidate whose range exceeds its profile's declared duration ceiling
- WHEN the clip is rendered under that profile
- THEN the render result MUST declare that it exceeds the ceiling, and by how much
- AND the rendered range MUST still be the candidate's full range

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

Cue construction MUST be total over that same eligible set: every eligible segment MUST yield at least
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

#### Scenario: Every eligible segment yields at least one cue

- GIVEN a clip span containing eligible segments, including one carrying no word-level timing
- WHEN subtitle cues are built
- THEN every eligible segment MUST contribute at least one cue
- AND a clip declaring confirmed-speech or unverified coverage MUST therefore carry a non-empty cue set

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

**The declaration is per profile, because the factor is.** Quality is `target_width / crop_width`, and
the target is the profile's. One clip cut from one crop can be native under a profile that asks for
fewer pixels and upscaled under one that asks for more, and both statements are true at once. A single
quality value per clip would have to pick one of them to report, which makes it wrong for the other
profile without saying so.

This is also where the 1080p ceiling stops being a single number and becomes one per destination. It
does not get better with more profiles: a 606-pixel-wide crop is the camera's limit, and every profile
targeting more than that width declares its own upscale factor over the same soft pixels. Adding
profiles multiplies the reporting of that constraint, never relieves it.

#### Scenario: One clip declares quality per profile

- GIVEN a clip rendered under two profiles whose output widths differ
- WHEN both render results are inspected
- THEN each MUST carry its own quality declaration computed against its own profile's target width
- AND one MUST be able to read native while the other reads upscaled

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

The rendered clip file and its metadata (title, description, source timestamps, the output-quality
declaration, and the script variants the file delivers) MUST be written to the job directory,
consistent with the per-job storage isolation already required of transcript artifacts.

**An export MUST be identified by clip and profile together, not by clip alone.** One clip candidate
now yields one export per distinct profile, so a clip-only key cannot name a file. This applies to the
persisted export, to its retrieval, and to any route that addresses a rendered clip.

Because networks sharing a profile share a file, an export MUST carry **the set** of script variants
delivered by it, not a single variant. A file serving three networks with one variant recorded loses
the other two, and reconstructing them later means re-running generation against a transcript that may
have been re-stitched since — the same reasoning that put title and description on the export rather
than leaving them derivable.

#### Scenario: Clip and metadata land in the job directory

- GIVEN a completed render for a job
- WHEN the export runs
- THEN the rendered clip file MUST be written under that job's directory
- AND its metadata MUST be written alongside it, retrievable by job id, clip id and profile

#### Scenario: An export is addressable by clip and profile

- GIVEN a clip rendered under two profiles
- WHEN each export is retrieved
- THEN clip id and profile together MUST resolve to exactly one export
- AND clip id alone MUST NOT be treated as identifying a single rendered file

#### Scenario: A shared file records every variant it delivers

- GIVEN a rendered file serving three networks that name one profile
- WHEN its export metadata is inspected
- THEN it MUST carry all three script variants
- AND each variant MUST remain attributable to the network it was written for

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
