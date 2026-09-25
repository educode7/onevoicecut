"""Whether a job is transcribed as one voice or many — kernel vocabulary.

Three modules speak this enum: the job record stores it, the transcription port
types against it, and the ASR adapters classify over it. Declaring it inside
`jobs/domain` would force the transcripts interface to import another module's
business rules for a two-value enum — the same AB-07 pressure that moved
`ids.py` into the kernel (design, OQ3-adjacent decision).
"""

from enum import StrEnum


class SpeakerMode(StrEnum):
    SINGLE = "single"
    MULTI = "multi"
