# Media Ingest Specification

## Purpose

Defines the local web UI's HTTP upload boundary: how a source video enters the system, and the two
per-job inputs (speaker mode, ASR engine) captured and validated at that boundary.
[BINDING: local web UI with HTTP upload]

## Requirements

### Requirement: Non-Blocking Upload Acceptance

The system MUST accept an HTTP video upload and create a job without waiting for transcription to
complete. A multi-hour transcription MUST NOT block the HTTP request/response cycle.

#### Scenario: Multi-hour video upload

- GIVEN an operator uploads a multi-hour video file
- WHEN the upload completes
- THEN the system MUST respond with a job identifier immediately
- AND transcription MUST proceed asynchronously

### Requirement: Upload Size Limit

The system MUST enforce a configured maximum upload size and MUST reject an oversized upload before
creating a job.

#### Scenario: Oversized upload rejected

- GIVEN a file larger than the configured size limit
- WHEN the operator attempts to upload it
- THEN the system MUST reject the request with an explicit error
- AND no job MUST be created

### Requirement: Per-Job Speaker Mode Input

The system MUST accept an optional speaker-mode input at ingest: single-voice (default) or "two or
more speakers". Automatic speaker detection is a non-goal; the mode MUST be an explicit operator
choice, never inferred from the audio.

#### Scenario: Speaker mode omitted

- GIVEN the operator does not specify a speaker mode
- WHEN the job is created
- THEN the system MUST default speaker mode to single-voice

#### Scenario: Multi-speaker declared

- GIVEN the operator selects "two or more speakers"
- WHEN the job is created
- THEN the job record MUST store speaker_mode=multi-speaker

### Requirement: Per-Job ASR Engine Selection

The system MUST require the operator to select an ASR engine (local or cloud) per job at ingest.
There MUST be no global default engine.

#### Scenario: Engine not selected

- GIVEN the operator submits an upload without selecting an ASR engine
- WHEN the request is validated
- THEN the system MUST reject the request with a validation error
- AND no job MUST be created

#### Scenario: Engine selected

- GIVEN the operator selects the local engine
- WHEN the job is created
- THEN the job record MUST store engine_choice=local

### Requirement: Reject Incompatible Engine/Speaker-Mode Combination at Admission

This is the single normative source for engine/speaker-mode compatibility enforcement; the
`transcription-jobs` and `speech-transcription` specs reference this requirement rather than
restating it. The system MUST validate, at job admission time — before any chunk is dispatched,
before any audio is extracted, and before any billable API call or local model invocation occurs —
that the selected ASR engine's declared capability (per `TranscriptionPort` capability declaration,
see `speech-transcription`: Capability Declaration) supports the requested speaker mode. If the
operator requests speaker_mode = "two or more speakers" and the selected engine declares
diarization=false, the system MUST reject the upload and MUST NOT create a job. This validation MUST
NOT be deferred to chunk processing; an incompatibility discovered only mid-transcription is a
defect, not an acceptable alternative path. The rejection error MUST name the missing capability and
MUST be actionable, telling the operator to either switch engine or drop speaker mode.

#### Scenario: Incompatible combination rejected before any job exists

- GIVEN the operator selects an engine whose adapter declares diarization=false
- AND selects speaker_mode = "two or more speakers"
- WHEN the upload is submitted
- THEN the system MUST reject the request and no job MUST be created
- AND the error MUST name the missing diarization capability and instruct the operator to switch
  engine or drop speaker mode

#### Scenario: Zero chunks processed for a rejected multi-hour job

- GIVEN a multi-hour source video submitted with an incompatible engine/speaker-mode combination
- WHEN the job is submitted
- THEN the system MUST reject it before any chunk is transcribed
- AND zero chunks MUST have been processed and no billable API call or local model invocation MUST
  have occurred

#### Scenario: Compatible combination admitted normally

- GIVEN the operator selects an engine whose adapter declares diarization=true
- AND selects speaker_mode = "two or more speakers"
- WHEN the upload is submitted
- THEN the system MUST create the job normally
