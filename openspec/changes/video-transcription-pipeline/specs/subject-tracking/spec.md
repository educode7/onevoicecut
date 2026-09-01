# Subject Tracking Specification

## Purpose

Defines `SubjectTrackerPort`: the provider-neutral subject-detection contract for locating the preacher
within a fixed wide shot, the `CropTrajectory` domain object that turns raw detections into a
9:16-ready reframe, and the pure trajectory arithmetic (smoothing, dead-zone, clamping, gap
interpolation) that a fake detector proves with no model weights loaded. [BINDING: detection and
rendering are separate concerns — see `clip-rendering`]

## Requirements

### Requirement: SubjectTrackerPort Contract

`SubjectTrackerPort` MUST accept a source media reference and a time range, and MUST return subject
detections sampled across that range. The port MUST know nothing about cropping, smoothing, aspect
ratio, or rendering — it answers where the subject is, nothing more.

#### Scenario: Detections returned for a time range

- GIVEN a source media reference and a clip candidate's time range
- WHEN the port is asked to detect the subject over that range
- THEN it MUST return a detection (or explicit no-detection) for each sampled point in the range
- AND detections MUST NOT extend beyond the requested time range

#### Scenario: Detection is scoped to the clip, not the whole source

- GIVEN a multi-hour source video and a clip candidate covering a few minutes of it
- WHEN detection runs for that clip
- THEN sampling MUST be limited to the clip's time range
- AND the port MUST NOT be required to process the full source duration

### Requirement: Capability Declaration

Every `SubjectTrackerPort` adapter MUST declare its detection capability, queryable before a clip is
dispatched for rendering, mirroring the capability-declaration pattern already binding on
`TranscriptionPort`.

#### Scenario: Adapter declares capability

- GIVEN a subject-tracker adapter implementation
- WHEN its capabilities are queried
- THEN it MUST report whether it can produce subject detections at all

### Requirement: A Miss Is Reported, Never Guessed

A detector that could not locate the subject at a sampled point MUST report an explicit no-detection
result. It MUST NOT return a plausible-looking centered or last-known position disguised as a genuine
detection — that judgment belongs to the trajectory-building use case (see Gap Interpolation and
Fallback below), not to the detector itself.

#### Scenario: Detector reports a miss explicitly

- GIVEN a sampled point where the subject cannot be located (occlusion, off-frame, low confidence)
- WHEN the detector processes that point
- THEN it MUST report no-detection rather than a synthesized position
- AND the no-detection result MUST be distinguishable from a low-confidence true detection

### Requirement: CropTrajectory Domain Object

The system MUST model `CropTrajectory` as an ordered sequence of `CropKeyframe`s, each carrying a
timestamp, a crop rectangle, and an origin (`KeyframeOrigin`: `TRACKED`, `INTERPOLATED`, or
`FALLBACK_CENTER`). `CropTrajectory` and `CropKeyframe` MUST be immutable domain objects, consistent
with every other domain entity in the system.

#### Scenario: Keyframe carries timestamp, crop rectangle, and origin

- GIVEN a computed trajectory
- WHEN a keyframe is inspected
- THEN it MUST expose a timestamp, a crop rectangle, and one of the three defined origins

### Requirement: Smoothing

The trajectory-building use case MUST smooth raw per-sample detections into a keyframe sequence free of
frame-to-frame jitter, without discarding genuine subject movement. This MUST be pure logic testable
against a fake detector, with no model weights loaded.

#### Scenario: Jittery detections produce a smoothed trajectory

- GIVEN a sequence of raw detections that oscillate by a small amount frame to frame around a stable
  position
- WHEN the trajectory is built
- THEN the resulting keyframes MUST NOT reproduce that frame-to-frame oscillation
- AND the test MUST run against a fake detector, loading no model weights

### Requirement: Dead-Zone

Small subject movements within a configured tolerance MUST NOT shift the crop window. The crop MUST
move only once the subject's displacement exceeds the dead-zone threshold.

#### Scenario: Movement within tolerance does not move the crop

- GIVEN consecutive detections whose displacement is within the configured dead-zone
- WHEN the trajectory is built
- THEN the crop rectangle MUST remain unchanged across those keyframes

#### Scenario: Movement beyond tolerance moves the crop

- GIVEN consecutive detections whose displacement exceeds the configured dead-zone
- WHEN the trajectory is built
- THEN the crop rectangle MUST shift to follow the subject

### Requirement: Clamping to Frame Edges

A crop rectangle MUST NOT extend beyond the source frame's bounds at any keyframe, regardless of where
the raw detection places the subject.

#### Scenario: Subject near the frame edge

- GIVEN a detection placing the subject close enough to a frame edge that a centered crop would extend
  past it
- WHEN the trajectory is built
- THEN the crop rectangle MUST be clamped to remain fully inside the source frame

### Requirement: Interpolation Across Detection Gaps

When the detector reports no-detection for a span bounded by a `TRACKED` keyframe before it and a
`TRACKED` keyframe after it, the trajectory MUST interpolate the crop position across that span, and
each interpolated keyframe's origin MUST be `INTERPOLATED`.

#### Scenario: Short gap is interpolated

- GIVEN a gap of no-detection bounded by tracked keyframes on both sides
- WHEN the trajectory is built
- THEN the keyframes filling the gap MUST have origin `INTERPOLATED`
- AND their crop positions MUST move continuously between the bounding tracked positions

### Requirement: Fallback to Center When Tracking Cannot Be Established

Where a gap has no bounding `TRACKED` keyframe to interpolate from — at the start of a clip, or across a
gap too long to bridge — the trajectory MUST fall back to a centered crop, and each such keyframe's
origin MUST be `FALLBACK_CENTER`. A fallback keyframe MUST NOT be reported as `TRACKED` or
`INTERPOLATED`.

#### Scenario: Untracked leading span falls back to center

- GIVEN no detection at the start of a clip before the first tracked position
- WHEN the trajectory is built
- THEN the leading keyframes MUST use a centered crop with origin `FALLBACK_CENTER`

#### Scenario: A gap with no bounding tracked keyframe falls back

- GIVEN a gap of no-detection with no tracked keyframe on at least one side
- WHEN the trajectory is built
- THEN the keyframes filling that gap MUST use origin `FALLBACK_CENTER`, not `INTERPOLATED`

### Requirement: Keyframe Provenance Is Marked, Never Silently Substituted

This is the third no-silent-degradation axis in the system, alongside diarization and non-speech
classification. A trajectory MUST always expose each keyframe's true origin. The system MUST NOT
present a `FALLBACK_CENTER` or `INTERPOLATED` keyframe as though it were a genuine `TRACKED` detection.

#### Scenario: Provenance is queryable per keyframe

- GIVEN a completed trajectory covering a full clip
- WHEN each keyframe is inspected
- THEN every keyframe MUST report exactly one of `TRACKED`, `INTERPOLATED`, or `FALLBACK_CENTER`
- AND no keyframe MUST report an origin other than the one that actually produced its position

### Requirement: Mostly-Fallback Trajectory Reported, Not Delivered as Success

A trajectory whose `FALLBACK_CENTER` proportion exceeds a configured threshold MUST be reported as
low-confidence rather than delivered indistinguishably from a well-tracked reframe. This is the
requirement that prevents a clip framed on an empty pulpit from looking like a successful render.

#### Scenario: Trajectory below the tracking threshold is flagged

- GIVEN a trajectory whose keyframes are predominantly `FALLBACK_CENTER`
- WHEN the trajectory is evaluated
- THEN it MUST be reported as low-confidence
- AND that report MUST be available to the consumer before rendering, not only observable after the
  fact in the rendered file

#### Scenario: Well-tracked trajectory is not flagged

- GIVEN a trajectory whose keyframes are predominantly `TRACKED` or `INTERPOLATED`
- WHEN the trajectory is evaluated
- THEN it MUST NOT be reported as low-confidence

### Requirement: Trajectory Arithmetic Is Testable With No Model Weights

Smoothing, dead-zone, clamping, gap interpolation, and the fallback threshold MUST all be implemented as
use-case logic exercised by the default `pytest` suite against a fake detector. None of this arithmetic
MUST require a real detection adapter or loaded model weights to be proven correct.

#### Scenario: Default suite proves trajectory logic without vision weights

- GIVEN the default test command (excluding `paid` and `localmodel`)
- WHEN it runs the trajectory-building use case tests
- THEN they MUST pass without loading any vision model weights

### Requirement: Real Detection Adapter Is Isolated Behind the `localmodel` Marker

The real `SubjectTrackerPort` adapter (backed by vision model weights) MUST be exercised only by tests
marked `localmodel`, consistent with the marker policy already governing local ASR and diarization
weights.

#### Scenario: Real adapter contract test is marked

- GIVEN the real vision-backed `SubjectTrackerPort` adapter
- WHEN its contract test is registered
- THEN the test MUST be marked `localmodel`
- AND it MUST NOT run as part of the default suite
