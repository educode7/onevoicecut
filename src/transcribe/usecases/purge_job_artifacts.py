"""A seam for the retention policy that does not exist yet. Nothing calls this.

Multi-hour video is the normal input, so every job leaves a large source file, a
normalized track, and one audio chunk per ten minutes of sermon. Disk consumption
therefore grows without bound, and the proposal records that as the assumption
most likely to become a real operational problem (Open Question 6).

It is deliberately unanswered and deliberately not enforced. What that question
needs is the operator's judgement about what is safe to lose — and the honest
default until then is to delete nothing, because a discarded source file cannot
be re-derived while everything else can.

This exists so the eventual answer lands as a caller and a policy rather than as
surgery on the storage adapter. `keep` says what survives; the omissions are the
point:

- The **source video** is never purgeable here. It is the only artifact the
  system cannot regenerate.
- The **transcript and artifacts** are never purgeable here. They are small, and
  they are the product.

Which leaves exactly the two large, regenerable intermediates.
"""

from dataclasses import dataclass
from enum import StrEnum

from transcribe.domain.ids import JobId


class PurgeableArtifact(StrEnum):
    """What a retention policy is allowed to reclaim. Not a list of everything."""

    NORMALIZED_AUDIO = "normalized_audio"  # regenerable by re-extraction
    AUDIO_CHUNKS = "audio_chunks"  # regenerable by re-slicing the track


@dataclass(frozen=True, slots=True)
class PurgeJobArtifacts:
    """A request, not an action. No use case consumes it yet.

    `keep` rather than `remove`: a policy that lists what to delete silently grows
    to cover new artifact kinds as they are added, while one that lists what to
    keep fails closed — a kind nobody thought about is not swept up by default.
    """

    job_id: JobId
    keep: frozenset[PurgeableArtifact] = frozenset(PurgeableArtifact)
