# Slice 6: Speaker Mode + Engine Selection + Diarization Rejection

## Purpose

Validates engine/speaker-mode compatibility at admission time — before any chunk is dispatched,
before any audio is extracted, and before any billable API call or local model invocation occurs.
An incompatible combination fails in milliseconds, not after hours of processing. The check is
extracted into a pure helper shared by the use-case admission guard and the port-level
defense-in-depth invariant.

This slice closes three `media-ingest` requirements (Per-Job Speaker Mode Input, Per-Job ASR
Engine Selection, Reject Incompatible Engine/Speaker-Mode Combination at Admission) and the
defense-in-depth half of one `speech-transcription` requirement (Reject Speaker-Mode Jobs the
Adapter Cannot Satisfy). The delta specs below describe what this slice implements; the main
specs in `media-ingest/spec.md` and `speech-transcription/spec.md` remain the normative
source for the overall behavior.

## ADDED Requirements

### Requirement: Admission Validates Speaker Mode and Engine Before Storage

`AdmitJob` MUST validate the operator's speaker-mode and engine inputs before any storage
operation. Speaker mode MUST default to SINGLE when omitted. Engine MUST be required with no
default. The guard MUST run before `storage.create_job()` — IDs are not minted, no storage
is touched, when validation fails.

#### Scenario: Speaker mode omitted defaults to single-voice

- GIVEN the operator submits a job request without specifying speaker mode
- WHEN `AdmitJob` processes the request
- THEN `speaker_mode` MUST default to `SpeakerMode.SINGLE`
- AND the job record MUST be created with `speaker_mode=SINGLE`

#### Scenario: Multi-speaker declared propagates to job record

- GIVEN the operator submits a job request with `speaker_mode=MULTI`
- WHEN `AdmitJob` processes the request
- THEN the job record MUST store `speaker_mode=MULTI`

#### Scenario: Engine not selected rejected with validation error

- GIVEN the operator submits a job request without selecting an engine
- WHEN the request is validated
- THEN the system MUST reject the request with a validation error
- AND no job MUST be created

#### Scenario: Engine selected propagates to job record

- GIVEN the operator submits a job request with `engine=LOCAL`
- WHEN `AdmitJob` processes the request
- THEN the job record MUST store `engine=LOCAL`

### Requirement: Incompatible Combination Rejected at Admission

The system MUST validate, at job admission time, that the selected engine's diarization
capability supports the requested speaker mode. If `speaker_mode=MULTI` and the engine
declares `diarization != AVAILABLE`, the system MUST reject the request and MUST NOT create
a job. The rejection error MUST name the missing capability and MUST be actionable — telling
the operator to switch engine or drop speaker mode. This validation MUST NOT be deferred to
chunk processing.

#### Scenario: Incompatible combination rejected before any job exists

- GIVEN an operator selects an engine whose adapter declares `diarization=UNSUPPORTED`
- AND selects `speaker_mode=MULTI`
- WHEN the job request is submitted
- THEN the system MUST reject the request and no job MUST be created
- AND the error MUST name the missing diarization capability
- AND the error MUST instruct the operator to switch engine or drop speaker mode

#### Scenario: Incompatible combination with REQUIRES_SETUP also rejected

- GIVEN an operator selects an engine whose adapter declares `diarization=REQUIRES_SETUP`
- AND selects `speaker_mode=MULTI`
- WHEN the job request is submitted
- THEN the system MUST reject the request identically to UNSUPPORTED

#### Scenario: Zero chunks processed for a rejected multi-hour job

- GIVEN a multi-hour source video submitted with an incompatible engine/speaker-mode combination
- WHEN the job is submitted
- THEN the system MUST reject it before any chunk is transcribed
- AND zero chunks MUST have been processed
- AND no billable API call or local model invocation MUST have occurred

#### Scenario: Compatible combination admitted normally

- GIVEN an operator selects an engine whose adapter declares `diarization=AVAILABLE`
- AND selects `speaker_mode=MULTI`
- WHEN the job request is submitted
- THEN the system MUST create the job normally

#### Scenario: SINGLE mode is always compatible

- GIVEN any engine regardless of its diarization capability
- AND `speaker_mode=SINGLE`
- WHEN the job request is submitted
- THEN the system MUST create the job normally

### Requirement: Web Layer Returns 422 for DiarizationUnsupported

The web route MUST catch `DiarizationUnsupported` raised by `AdmitJob` and return HTTP 422
with a response body naming the missing capability and providing remediation text. The
response format MUST match existing error patterns in the web adapter.

#### Scenario: DiarizationUnsupported mapped to 422

- GIVEN the operator submits a job with `speaker_mode=MULTI` against a non-diarizing engine
- WHEN `AdmitJob` raises `DiarizationUnsupported`
- THEN the web route MUST return HTTP 422
- AND the response body MUST contain the capability name `"diarization"`
- AND the response body MUST contain remediation text suggesting engine switch or mode drop

#### Scenario: Existing error patterns preserved

- GIVEN other domain errors (`JobNotFound`, `UnsupportedContainer`, `UploadTooLarge`)
- WHEN raised by their respective use cases
- THEN the web routes MUST continue to handle them with their current status codes
- AND the `DiarizationUnsupported` handling MUST NOT disturb those paths

### Requirement: Port-Level Defense-in-Depth Guard

Every `TranscriptionPort.transcribe()` implementation MUST refuse a `MULTI` speaker-mode
request when its `diarization` capability is not `AVAILABLE`. This is a standing port
contract invariant — not merely an admission-time convenience. The refusal MUST raise
`DiarizationUnsupported` and MUST name the missing capability. This guard MUST NOT be the
operator's first or only signal of incompatibility; a job should never legitimately reach
dispatch with an incompatible combination in the first place.

#### Scenario: Non-diarizing adapter refuses multi-speaker at port level

- GIVEN a `TranscriptionPort` adapter whose `capabilities().diarization` is `UNSUPPORTED`
- WHEN it is asked to transcribe a chunk with `speaker_mode=MULTI`
- THEN it MUST raise `DiarizationUnsupported`
- AND the error MUST name the missing diarization capability

#### Scenario: REQUIRES_SETUP adapter also refuses at port level

- GIVEN a `TranscriptionPort` adapter whose `capabilities().diarization` is `REQUIRES_SETUP`
- WHEN it is asked to transcribe a chunk with `speaker_mode=MULTI`
- THEN it MUST raise `DiarizationUnsupported`

#### Scenario: Diarizing adapter processes multi-speaker normally

- GIVEN a `TranscriptionPort` adapter whose `capabilities().diarization` is `AVAILABLE`
- WHEN it receives a chunk with `speaker_mode=MULTI`
- THEN it MUST process the chunk without raising `DiarizationUnsupported`

#### Scenario: SINGLE mode always accepted at port level

- GIVEN any `TranscriptionPort` adapter regardless of diarization capability
- WHEN it receives a chunk with `speaker_mode=SINGLE`
- THEN it MUST process the chunk without raising `DiarizationUnsupported`

### Requirement: Extracted Compatibility Helper

The engine/speaker-mode compatibility check MUST be extracted into one pure function with
no side effects. This function MUST be used by both the use-case admission guard and the
port-level defense-in-depth guard, ensuring a single definition of compatibility.

#### Scenario: Helper returns compatible for supported combinations

- GIVEN a `TranscriptionCapabilities` with `diarization=AVAILABLE`
- AND `speaker_mode=MULTI`
- WHEN the compatibility helper is called
- THEN it MUST return compatible (no error)

#### Scenario: Helper returns incompatible for unsupported combinations

- GIVEN a `TranscriptionCapabilities` with `diarization=UNSUPPORTED`
- AND `speaker_mode=MULTI`
- WHEN the compatibility helper is called
- THEN it MUST raise `DiarizationUnsupported`

#### Scenario: Helper is pure — no storage, no I/O, no port calls

- GIVEN any inputs
- WHEN the compatibility helper is called
- THEN it MUST NOT call any port method
- AND it MUST NOT perform any I/O operation
- AND it MUST NOT create or mutate any domain entity

#### Scenario: SINGLE mode always compatible through helper

- GIVEN any `TranscriptionCapabilities` regardless of diarization value
- AND `speaker_mode=SINGLE`
- WHEN the compatibility helper is called
- THEN it MUST return compatible (no error)
