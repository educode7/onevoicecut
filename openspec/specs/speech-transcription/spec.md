# Speech Transcription Specification

## Purpose

Defines `TranscriptionPort`: the provider-neutral ASR contract, its two adapters (local, cloud),
mandatory capability declaration, chunk planning and overlap stitching, and the opt-in diarization
path with its rejection semantics. [BINDING: two interchangeable adapters, local + cloud]

## Requirements

### Requirement: TranscriptionPort Contract

`TranscriptionPort` MUST accept an `AudioChunk` and a requested speaker mode, and MUST return segments
carrying start/end timestamps, text, an optional speaker label, and a content classification.
Timestamps MUST NOT be discarded at this boundary.

#### Scenario: Single-speaker transcription

- GIVEN an `AudioChunk` and speaker_mode=single-voice
- WHEN an adapter transcribes it
- THEN the result MUST contain segments with start/end timestamps and text
- AND no speaker label MUST be required
- AND each segment MUST carry a content classification

### Requirement: Capability Declaration

Every `TranscriptionPort` adapter MUST declare whether it supports diarization, and whether it supports
non-speech content classification. Both declarations MUST be queryable before a job is dispatched to
the adapter.

#### Scenario: Adapter declares capability

- GIVEN an ASR adapter implementation
- WHEN its capabilities are queried
- THEN it MUST report a boolean diarization capability
- AND it MUST report whether it can classify non-speech audio

### Requirement: Reject Speaker-Mode Jobs the Adapter Cannot Satisfy

An adapter that cannot diarize MUST NOT ever silently return unlabeled single-speaker output for a
speaker-mode request — this is a standing contract invariant on `TranscriptionPort`, not merely an
admission-time convenience. Silent degradation is the unacceptable failure mode: unlabeled output for
a speaker-mode request looks like a valid result but is not. The authoritative timing of the
operator-facing rejection is normative in `media-ingest`'s "Reject Incompatible Engine/Speaker-Mode
Combination at Admission" requirement: an incompatible combination MUST be rejected at job admission,
before any chunk is dispatched to this port. This port's own refusal behavior below is a defense-in-depth
contract invariant, and MUST NOT be the operator's first or only signal of the incompatibility — a job
should never legitimately reach dispatch with an incompatible combination in the first place.

#### Scenario: Non-diarizing adapter never returns unlabeled output as if valid

- GIVEN an ASR adapter that declares diarization=false
- WHEN it is asked to process a chunk with speaker_mode=multi-speaker (e.g. under fault-injection
  testing of this contract invariant, since admission-time validation should already have prevented
  this in normal operation)
- THEN it MUST refuse rather than return segments without speaker labels as if they satisfied the
  request
- AND the refusal error MUST name the missing diarization capability

#### Scenario: Diarizing adapter receives multi-speaker job

- GIVEN an ASR adapter that declares diarization=true
- WHEN it receives a chunk with speaker_mode=multi-speaker
- THEN the returned segments MUST include a speaker label per segment

### Requirement: Non-Speech Segment Classification

Source audio routinely contains music and singing alongside the spoken message. Every segment returned
by `TranscriptionPort` MUST carry a content classification of speech, music, or uncertain.

An adapter that cannot distinguish speech from non-speech MUST classify as **uncertain**. It MUST NOT
classify as speech by default. Asserting "this is the spoken message" on the basis of never having
checked is a silent degradation of the same class as returning unlabeled output for a diarization
request: the resulting transcript is indistinguishable from a correct one.

Classification MUST NOT cause a segment to be discarded at this boundary, and MUST NOT cause its
timestamps to be dropped. A musical range remains addressable in the source footage.

#### Scenario: Classifying adapter marks a musical passage

- GIVEN an ASR adapter that declares non-speech classification support
- AND an `AudioChunk` whose audio is music or singing rather than spoken message
- WHEN the adapter transcribes it
- THEN the returned segments covering that audio MUST be classified as music
- AND those segments MUST retain their start/end timestamps

#### Scenario: Non-classifying adapter never asserts speech

- GIVEN an ASR adapter that declares no non-speech classification support
- WHEN it transcribes any chunk
- THEN every returned segment MUST be classified as uncertain
- AND no returned segment MUST be classified as speech

#### Scenario: Classification never discards audio

- GIVEN a chunk containing both spoken message and a musical passage
- WHEN the adapter transcribes it
- THEN segments covering both MUST be returned
- AND the musical segments MUST NOT be omitted from the result

### Requirement: Non-Speech Hallucination Containment

Whisper-family decoders are known to emit fabricated text when given music or near-silence, because
with no speech to condition on the decoder falls back on its training prior — for Spanish audio,
typically subtitle boilerplate that was never spoken. Adapters MUST apply the engine-level controls
available to them (voice-activity filtering and the decoder guards that break degenerate repetition
loops) to suppress such output, and MUST NOT emit fabricated text as speech-classified segments.

Where an engine exposes no such control, the adapter MUST declare no classification support, so that
its output is classified uncertain and excluded from the message by the consumers downstream, rather
than silently entering the transcript as message text.

#### Scenario: Music-only audio produces no invented message text

- GIVEN an `AudioChunk` containing only music or silence
- WHEN a classifying adapter transcribes it
- THEN the result MUST NOT contain speech-classified segments carrying fabricated text
- AND any text returned for that audio MUST be classified as music or uncertain

#### Scenario: Engine without controls declares honestly

- GIVEN an ASR engine exposing no voice-activity or hallucination-suppression control
- WHEN its adapter's capabilities are queried
- THEN it MUST declare no non-speech classification support
- AND every segment it returns MUST be classified uncertain

### Requirement: Chunk Planning

The system MUST plan chunk boundaries for a multi-hour audio track independent of which ASR adapter is
selected, including overlap regions between adjacent chunks to prevent word loss at cut points.

#### Scenario: Plan includes overlap

- GIVEN an audio track exceeding one chunk's maximum duration
- WHEN a chunk plan is produced
- THEN adjacent chunks MUST share an overlap region
- AND the plan MUST be independent of the ASR adapter used to fulfill it

### Requirement: Overlap Stitching

When assembling chunk-level transcription results into a single transcript, the system MUST resolve
duplicated words/phrases in overlap regions so that no words are lost and no words are duplicated at
chunk boundaries.

#### Scenario: Word at boundary preserved once

- GIVEN two adjacent chunks whose overlap region both transcribe the same trailing/leading words
- WHEN results are stitched
- THEN the assembled transcript MUST contain those words exactly once at the correct timestamp

### Requirement: Contract Parity and Declared Divergence

A shared contract test suite MUST run against both the local and cloud adapters and MUST assert
identical behavior on the single-speaker default path. On the diarization path, the suite MUST assert
each adapter's declared, tested divergence (support or explicit rejection) rather than assuming parity.

Non-speech classification is a **second, independent axis of declared divergence**, and it does not
partition the adapters the same way diarization does. The suite MUST assert each adapter's classification
behavior against its own declaration, and MUST NOT assume that an adapter supporting one axis supports
the other.

#### Scenario: Shared single-speaker contract test

- GIVEN the shared contract test body and a fake or fixture input
- WHEN it runs against both the local and cloud adapter
- THEN both adapters MUST satisfy identical assertions for the single-speaker path

#### Scenario: Diarization divergence asserted

- GIVEN the shared contract test body
- WHEN it runs the diarization path against an adapter declaring diarization=false
- THEN the test MUST assert the explicit rejection behavior, not silent success

#### Scenario: Classification divergence asserted independently

- GIVEN the shared contract test body
- WHEN it runs the classification path against each adapter
- THEN the test MUST assert that adapter's behavior against its own declared classification support
- AND an adapter declaring no classification support MUST be asserted to return uncertain segments,
  never speech-classified ones
- AND classification support MUST NOT be inferred from diarization support, in either direction

### Requirement: Cloud Adapter Request-Size Handling

The cloud adapter MUST account for provider per-request size limits (e.g. a 25MB cap) by operating on
chunks sized within that limit, since chunking is already mandatory for multi-hour audio.

#### Scenario: Chunk within provider limit

- GIVEN a chunk plan sized so each chunk's audio payload is under the provider's per-request cap
- WHEN the cloud adapter submits a chunk
- THEN the request MUST NOT exceed the provider's documented size limit
