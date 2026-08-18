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
