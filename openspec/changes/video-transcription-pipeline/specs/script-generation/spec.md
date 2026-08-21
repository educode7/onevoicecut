# Script Generation Specification

## Purpose

Produces the final product artifact from a completed transcript: a summary and a list of candidate
clip moments, each with source timestamps and a short script, shaped to support multiple script
variants without structural change. [BINDING: scope stops at this artifact — no rendering/publishing]

## Requirements

### Requirement: Map-Reduce Summarization

For transcripts whose length exceeds a practical single-call LLM context, the system MUST summarize
using a map-reduce strategy: summarize sub-ranges first, then reduce those summaries into a final
summary.

#### Scenario: Multi-hour transcript summarized

- GIVEN a transcript long enough to exceed the configured practical context size
- WHEN summarization runs
- THEN the system MUST produce sub-summaries per range before producing the final summary
- AND the final summary generation MUST NOT submit the full transcript in a single call exceeding
  practical context

### Requirement: Summarization Input Contains Speech Only

Summarization MUST be performed over segments classified as speech. Segments classified as music MUST
NOT be submitted to the LLM as message content, and segments classified as uncertain MUST NOT be
submitted as though they were confirmed speech.

This is a correctness requirement, not a cost optimization. Under map-reduce, a MAP window polluted by
transcribed lyrics produces a polluted partial summary, which is folded into the REDUCE output — at
which point the contamination is no longer traceable to the segment that caused it. The resulting
summary reads fluently and is wrong, which is the same class of silent failure as a hallucinated
timestamp.

#### Scenario: Lyrics excluded from a MAP window

- GIVEN a transcript window spanning both speech-classified and music-classified segments
- WHEN the MAP phase renders that window for the LLM
- THEN the rendered content MUST exclude the music-classified segments
- AND the resulting partial summary MUST NOT describe sung content as part of the speaker's message

#### Scenario: Summary of music-heavy material does not invent a message

- GIVEN a transcript in which a substantial portion is classified music or uncertain
- WHEN summarization runs
- THEN the summary MUST be derived only from speech-classified segments
- AND the system MUST NOT substitute non-speech content to fill the summary

### Requirement: Clip Candidate Output

The system MUST produce a list of candidate clip moments, each referencing source timestamps
(start/end) from the original transcript and carrying a short script.

A clip candidate MAY reference a time range that contains non-speech audio. Exclusion of music from the
*message* MUST NOT propagate into an exclusion of musical ranges from *candidate clips*: the timestamps
remain valid, and a musical or sung passage can be strong short-form material. (Whether generation
should additionally favor such ranges in ranking is open per proposal Open Question 9; permitting them
is settled, promoting them is not.)

#### Scenario: Clip candidate references source timestamps

- GIVEN a generated clip candidate
- WHEN it is inspected
- THEN it MUST include a start and end timestamp that map back into the source transcript
- AND it MUST include a short script

#### Scenario: Candidate may span a musical range

- GIVEN a transcript containing music-classified segments
- WHEN clip candidates are generated
- THEN a candidate whose time range covers non-speech audio MUST NOT be rejected on that basis alone
- AND its timestamps MUST resolve against the source transcript like any other candidate

### Requirement: N Script Variants Per Clip Candidate

The generation output contract MUST support an arbitrary number of script variants per clip candidate
(e.g. per target network/format), such that adding a variant is a data change, not a structural change
to the contract.

#### Scenario: Multiple variants for one candidate

- GIVEN a clip candidate
- WHEN more than one script variant is requested for it
- THEN the candidate MUST be able to carry a list of variants without changing its schema

#### Scenario: Target networks/formats (Assumption)

- GIVEN the proposal leaves target networks/formats open (proposal Open Question 3)
- WHEN a variant is generated
- THEN the variant MUST carry an identifying label for its target format
- AND (Assumption: the concrete set of target networks/formats is undetermined and MAY resolve to a
  single default variant until answered)

### Requirement: Scope Boundary — No Rendering

The system MUST stop at the summary and clip-candidate/script artifact. It MUST NOT render, assemble,
or publish video.

#### Scenario: Output is a data artifact, not a video

- GIVEN generation completes
- WHEN the output is inspected
- THEN it MUST consist of summary text, clip candidates, and script variants only
- AND no video file MUST be produced by this capability
