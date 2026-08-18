# Transcript Artifacts Specification

## Purpose

Defines the structured transcript domain model, intermediate chunk result persistence, and the `.txt`
export, all behind `TranscriptStoragePort`.

## Requirements

### Requirement: Structured Transcript Domain Object

The system MUST model `Transcript` as an ordered collection of `TranscriptSegment`s, each with start
timestamp, end timestamp, text, and an optional speaker field. This structured form MUST be the
canonical internal representation; the delivered `.txt` artifact MUST NOT be the source of truth.

#### Scenario: Segment retains timestamps and optional speaker

- GIVEN a completed transcription
- WHEN the `Transcript` is assembled
- THEN each `TranscriptSegment` MUST carry start, end, text, and speaker (nullable)

### Requirement: Intermediate Chunk Result Persistence

The system MUST persist each chunk's transcription result via `TranscriptStoragePort` as soon as that
chunk completes, independent of overall job completion. This persistence is what makes chunk-level
progress and resume real rather than in-memory only.

#### Scenario: Chunk result survives before job completion

- GIVEN chunk 10 of 87 completes
- WHEN its result is persisted
- THEN it MUST be retrievable via `TranscriptStoragePort` even if the job has not yet completed

### Requirement: Storage Location (Assumption)

Transcripts, extracted audio, and intermediate chunk results MUST be stored behind
`TranscriptStoragePort`, keyed by job id. (Assumption, open per proposal Open Question 5: local
filesystem, one directory per job, no database. This is an assumption, not a confirmed decision, and
MAY change at design time.)

#### Scenario: Per-job storage isolation

- GIVEN two distinct jobs
- WHEN their artifacts are stored
- THEN each job's artifacts MUST be retrievable independently by job id without cross-job interference

### Requirement: Plain-Text Export

The system MUST provide a `.txt` export derived from the structured `Transcript`, without timestamps
or speaker metadata having been discarded from the underlying structured object.

#### Scenario: Export from structured transcript

- GIVEN a completed structured `Transcript`
- WHEN a `.txt` export is requested
- THEN the system MUST produce a plain-text file
- AND the structured `Transcript` (with timestamps) MUST remain retrievable separately from the export

### Requirement: Retention Is Unbounded (Operational Risk)

The system MUST NOT silently delete uploaded video, extracted audio, or chunk files. (Assumption,
open per proposal Open Question 6: no automatic retention/cleanup policy exists yet.) This is flagged
as an operational risk: because multi-hour video is the normal case, each job accumulates a large
source file, extracted audio, and chunk files, and disk consumption grows without bound absent a
future retention policy.

#### Scenario: Artifacts persist after job completion

- GIVEN a completed job
- WHEN no retention policy is configured
- THEN the system MUST NOT automatically delete the job's source video, audio, or chunk files
