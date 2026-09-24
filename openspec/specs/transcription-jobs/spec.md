# Transcription Jobs Specification

## Purpose

Defines the asynchronous job lifecycle for long-running, chunk-based transcription, including
chunk-level progress, chunk-level failure isolation, resume, and per-chunk timeouts. Multi-hour source
video is the normal case, not an edge case.

## Requirements

### Requirement: Asynchronous Job Lifecycle

The system MUST run transcription as a background job with states queued, running, completed, and
failed, decoupled from the HTTP request that created it.

#### Scenario: Job created and polled

- GIVEN a job has been created
- WHEN the operator polls the job status endpoint
- THEN the system MUST return the current job state without re-executing transcription

### Requirement: Chunk-Level Progress Reporting

The system MUST report progress at chunk granularity: chunks completed, total chunks, and elapsed
time. A single "running" state is insufficient for a multi-hour job.

#### Scenario: Mid-run progress poll

- GIVEN a job with 87 planned chunks and 40 completed
- WHEN the operator polls status
- THEN the response MUST include completed=40, total=87, and elapsed time

### Requirement: Chunk-Level Failure Isolation

A failure transcribing one chunk MUST NOT discard results from previously completed chunks. Failure
MUST be recorded per chunk.

#### Scenario: Late chunk failure preserves earlier work

- GIVEN a job with 87 chunks and chunks 1-83 completed successfully
- WHEN chunk 84 fails
- THEN the results for chunks 1-83 MUST remain persisted and intact
- AND chunk 84 MUST be marked failed without terminating the entire job record

### Requirement: Resume From First Incomplete Chunk

The system MUST allow a job to resume from the first incomplete chunk after a crash, process restart,
or transient cloud error, without redoing completed chunks.

#### Scenario: Resume after crash

- GIVEN a job with chunks 1-50 of 87 persisted as completed
- WHEN the process crashes and the job is resumed
- THEN transcription MUST continue starting at chunk 51
- AND chunks 1-50 MUST NOT be re-transcribed

#### Scenario: Resume after transient cloud error

- GIVEN a cloud ASR request for chunk 30 fails with a transient error
- WHEN the job retries
- THEN only chunk 30 MUST be retried
- AND chunks completed before it MUST remain unaffected

### Requirement: Per-Chunk Timeout

The system MUST apply a timeout per chunk, not a single timeout for the entire job. A job MUST be
allowed to run for hours as long as each chunk completes within its own timeout.

#### Scenario: One chunk times out

- GIVEN chunk transcription exceeds the configured per-chunk timeout
- WHEN the timeout elapses
- THEN only that chunk MUST be marked failed/timed-out
- AND the job MUST NOT be terminated as a whole

#### Scenario: Long job within chunk timeouts

- GIVEN a job runs for three hours because each of its chunks individually completes within its own
  timeout
- WHEN the job is evaluated for termination
- THEN the system MUST NOT terminate the job due to total elapsed time alone

### Requirement: Job Record Carries Speaker Mode and Engine Choice

The job record MUST persist the speaker mode and ASR engine choice captured at ingest, and MUST
propagate both to the transcription use case without requiring re-selection. The engine/speaker-mode
combination MUST already be validated for compatibility before a job record is admitted — see
`media-ingest`: Reject Incompatible Engine/Speaker-Mode Combination at Admission, which is the
normative source for that validation. This requirement does not restate that check; it only asserts
that no job record exists for an incompatible combination.

#### Scenario: Use case reads job configuration

- GIVEN a job record with engine_choice=cloud and speaker_mode=multi-speaker
- WHEN the transcription use case processes a chunk
- THEN it MUST use the cloud adapter and request diarized output for that chunk

#### Scenario: No job record for an incompatible combination

- GIVEN admission-time validation (see `media-ingest`) rejects an incompatible engine/speaker-mode
  combination
- WHEN the rejection occurs
- THEN no job record MUST be created for that submission
