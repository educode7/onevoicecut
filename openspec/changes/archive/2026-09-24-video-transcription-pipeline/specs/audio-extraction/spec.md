# Audio Extraction Specification

## Purpose

Converts an uploaded video container into a normalized audio track and slices that track into chunks
according to the chunk plan, using ffmpeg as the sole extraction mechanism.

## Requirements

### Requirement: Video to Normalized Audio

The system MUST extract a normalized audio track from an uploaded video container via an ffmpeg-based
adapter, isolated behind `AudioExtractorPort`.

#### Scenario: Extraction produces audio track

- GIVEN a validated `SourceMedia` reference
- WHEN extraction runs
- THEN the system MUST produce a normalized `AudioTrack` usable by downstream chunking

### Requirement: Chunk Slicing

Given a chunk plan, the system MUST slice the normalized audio track into individual `AudioChunk`s
matching the plan's boundaries and overlap regions.

#### Scenario: Plan produces matching chunk files

- GIVEN a chunk plan with N segments including overlap regions
- WHEN slicing runs
- THEN the system MUST produce N `AudioChunk`s whose boundaries match the plan

### Requirement: ffmpeg Runtime Availability Check

The system MUST verify ffmpeg is available on PATH before attempting extraction and MUST fail with an
actionable error identifying the missing binary and remediation, rather than a generic subprocess
error. This is a runtime failure mode, not only an install-time concern.

#### Scenario: ffmpeg missing from PATH at runtime

- GIVEN ffmpeg is not present on the runtime PATH
- WHEN a job attempts audio extraction
- THEN the system MUST fail the extraction step with a clear message naming ffmpeg and instructing the
  operator to install it
- AND the failure MUST NOT surface as an unhandled low-level subprocess exception

### Requirement: Media Probe Reports Frame Dimensions

`MediaProbe`, returned by `AudioExtractorPort.probe()`, MUST additionally report the source video's
frame width and height in pixels, alongside its existing duration, container, and audio-presence
fields. Neither crop geometry (see `subject-tracking`) nor the render quality declaration (see
`clip-rendering`) is computable without them, and `ffprobe` already returns both in the stream metadata
the adapter consults.

Where the source has no video stream, or `ffprobe` cannot resolve frame dimensions, `MediaProbe` MUST
report that absence explicitly rather than defaulting to a fabricated resolution. A downstream consumer
that requires dimensions MUST be able to rely on the declared absence to refuse cleanly, rather than
computing crop or quality decisions against invented values — the same no-silent-degradation invariant
already binding elsewhere in this system, applied to frame geometry.

#### Scenario: Probe reports dimensions for a valid video

- GIVEN a validated `SourceMedia` reference with a video stream
- WHEN it is probed
- THEN `MediaProbe` MUST report frame width and height in pixels matching the source video

#### Scenario: Probe declares absence rather than fabricating dimensions

- GIVEN a `SourceMedia` reference for which `ffprobe` cannot resolve frame dimensions (no video stream,
  or an unreadable stream)
- WHEN it is probed
- THEN `MediaProbe` MUST report the absence of frame dimensions explicitly
- AND it MUST NOT report a default or estimated width/height as though it were read from the source

#### Scenario: Downstream consumers can rely on declared absence

- GIVEN a `MediaProbe` with no frame dimensions
- WHEN a consumer requiring dimensions (crop trajectory construction, render quality declaration)
  inspects it
- THEN it MUST be able to detect the absence and refuse cleanly
- AND it MUST NOT be forced to compute against a fabricated width/height
