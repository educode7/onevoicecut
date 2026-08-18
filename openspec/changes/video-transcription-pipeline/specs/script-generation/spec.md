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

### Requirement: Clip Candidate Output

The system MUST produce a list of candidate clip moments, each referencing source timestamps
(start/end) from the original transcript and carrying a short script.

#### Scenario: Clip candidate references source timestamps

- GIVEN a generated clip candidate
- WHEN it is inspected
- THEN it MUST include a start and end timestamp that map back into the source transcript
- AND it MUST include a short script

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
