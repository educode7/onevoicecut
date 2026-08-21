# Transcript Artifacts Specification

## Purpose

Defines the structured transcript domain model, intermediate chunk result persistence, and the `.txt`
export, all behind `TranscriptStoragePort`.

## Requirements

### Requirement: Structured Transcript Domain Object

The system MUST model `Transcript` as an ordered collection of `TranscriptSegment`s, each with start
timestamp, end timestamp, text, an optional speaker field, and a content classification (`SegmentKind`:
speech, music, or uncertain). This structured form MUST be the canonical internal representation; the
delivered `.txt` artifact MUST NOT be the source of truth.

The classification MUST be carried on the segment rather than applied as a filter at assembly time.
Filtering discards information the product needs: a musical range still points into the source footage
and remains valid material for a clip. Marking preserves that, and lets each consumer decide
independently what to include.

#### Scenario: Segment retains timestamps, optional speaker, and classification

- GIVEN a completed transcription
- WHEN the `Transcript` is assembled
- THEN each `TranscriptSegment` MUST carry start, end, text, speaker (nullable), and a content
  classification

#### Scenario: Non-speech segments are retained in the structured transcript

- GIVEN a transcription over audio containing both spoken message and music
- WHEN the `Transcript` is assembled
- THEN segments classified as music MUST be present in the structured `Transcript`
- AND their timestamps MUST remain resolvable against the source media

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

### Requirement: Message Export Contains Speech Only

The `.txt` export represents **the spoken message**. It MUST be built from segments classified as
speech, and MUST exclude segments classified as music. Segments classified as uncertain MUST NOT be
silently presented as message text; the export MUST either exclude them or mark them distinguishably,
and the choice MUST be consistent rather than per-segment.

Excluding a segment from the export MUST NOT remove it from the structured `Transcript`, which remains
the source of truth per the requirement above.

#### Scenario: Sung lyrics do not enter the message export

- GIVEN a structured `Transcript` containing both speech-classified and music-classified segments
- WHEN a `.txt` message export is produced
- THEN the exported text MUST NOT contain text from music-classified segments
- AND those segments MUST still be retrievable from the structured `Transcript`

#### Scenario: Uncertain segments are never presented as plain message text

- GIVEN a structured `Transcript` containing uncertain-classified segments
- WHEN a `.txt` message export is produced
- THEN those segments MUST either be excluded, or marked distinguishably from speech-classified text
- AND they MUST NOT appear indistinguishable from confirmed spoken message

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
