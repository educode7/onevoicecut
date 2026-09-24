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

#### Scenario: Every variant identifies its target

- GIVEN a clip candidate and a configured set of target networks
- WHEN variants are generated
- THEN each variant MUST carry an identifying label naming the target it was written for
- AND two variants for the same candidate MUST NOT carry the same target label

### Requirement: Target Networks Are Configuration Data, Not Structure

**[ANSWERED — proposal Open Question 3]** The operator publishes to more than one network, and each
network wants a differently-written script. The set of targets MUST therefore be configuration data:
adding, removing, or retuning a network MUST be a change to that data, never a change to the
generation contract, the clip-candidate schema, or any type crossing a port.

Each target MUST declare three things before the model is asked: the label it is known by, the script
format, and its duration target. **None of the three may be obtained from the model.** An LLM asked
for a duration produces a plausible number, and a fabricated duration is a ninety-second script
labelled forty-five — the label being the thing an operator cuts against. These are properties of what
a target *is*, not observations about the text that came back.

A target name the configuration does not define MUST be refused at resolution time, and an empty
target set MUST be refused likewise. Falling back to a default target would hand an operator who asked
for a Reels script a generic one with nothing in the artifact to say so — the same silent-degradation
shape this change refuses on every other axis.

#### Scenario: A network is added without a structural change

- GIVEN a configured set of target networks
- WHEN a further network is added to that configuration
- THEN generation MUST produce a variant for it without any change to `ClipCandidate`,
  `ScriptVariant`, or `GenerationResult`

#### Scenario: Duration and format are decided before the model is asked

- GIVEN a target declaring a script format and a duration target
- WHEN a variant is generated for it
- THEN the variant's format and duration target MUST be the target's declared values
- AND neither value MUST be parsed or inferred from the model's response

#### Scenario: Unknown or empty target selection is refused

- GIVEN a configured target set
- WHEN resolution is requested for a name that set does not define, or for an empty selection
- THEN the system MUST refuse with an error naming the available targets
- AND it MUST NOT substitute a default target

### Requirement: A Target Names Its Render Profile Without Knowing What One Is

A script target MUST carry the **name** of the render profile its clips are delivered under, and MUST
carry nothing else about rendering. Generation MUST NOT hold output dimensions, aspect ratio, caption
geometry, or any other pixel-level property.

This is the `Scope Boundary — No Rendering` requirement holding under a multi-target delivery. Two
networks whose clips are cut and framed identically differ only in their scripts, and the way that
fact is expressed is that both targets name the same profile. Resolving that name to actual geometry
belongs to `clip-rendering`, which is where the frame is known.

The linkage is a name precisely so the boundary survives: generation decides *which* moments are worth
cutting and *what to say about them*, and remains unable to express an opinion about how a frame is
cropped even by accident.

#### Scenario: Target carries a profile name, not geometry

- GIVEN a configured script target
- WHEN its declared fields are inspected
- THEN it MUST carry the name of a render profile
- AND it MUST NOT carry output width, height, aspect ratio, or caption placement

#### Scenario: Two networks may share one render profile

- GIVEN two script targets whose clips are delivered identically framed
- WHEN their configurations are inspected
- THEN both MUST be able to name the same render profile
- AND each MUST still produce its own script variant

### Requirement: Scope Boundary — No Rendering

Script generation MUST stop at the summary and clip-candidate/script artifact. It MUST NOT render,
assemble, or publish video.

Through proposal rev 3 this was a system-wide non-goal. Rev 4 put vertical clip rendering in scope, so
this is now a **capability** boundary rather than a delivery boundary: rendering is specified in
`clip-rendering` and MUST be reached through `VideoRenderPort`, never from inside generation. The
separation is load-bearing — generation decides *which* moments are worth cutting, and knows nothing
about how a frame is cropped. Publishing remains out of scope system-wide: `PublishPort` is declared
and deliberately unimplemented.

#### Scenario: Output is a data artifact, not a video

- GIVEN generation completes
- WHEN the output is inspected
- THEN it MUST consist of summary text, clip candidates, and script variants only
- AND no video file MUST be produced by this capability
