"""Plan chunk boundaries for a multi-hour track, independent of the ASR engine.

Pure arithmetic, no I/O. Living above the port is what lets long-audio
correctness be proven before either real engine exists — and what stops chunk
mechanics from leaking into adapters, where each engine would solve it
differently and none of it would be testable.
"""

import math

from transcribe.domain.chunking import ChunkPlan, PlannedChunk
from transcribe.domain.errors import ChunkTooLarge
from transcribe.domain.ids import JobId
from transcribe.domain.media import AudioTrack
from transcribe.ports.capabilities import TranscriptionCapabilities

DEFAULT_TARGET_CHUNK_S = 600.0
DEFAULT_OVERLAP_S = 5.0

# Below this, a trailing chunk is absorbed by its predecessor: very short tail
# chunks are where Whisper-family models hallucinate most, and on this material
# a hallucinated tail is indistinguishable from the speaker's closing words.
DEFAULT_MIN_CHUNK_S = 30.0

# Headroom against container overhead and bitrate variance: a plan is derived
# from an average rate, but any individual chunk can encode above it.
BYTE_CAP_HEADROOM = 0.9


def _stride_for(
    track: AudioTrack, capabilities: TranscriptionCapabilities, target_chunk_s: float
) -> float:
    """Reconcile a byte cap and a duration cap into one stride, in seconds.

    A provider limit is expressed in bytes but a plan is expressed in time, so
    the measured bitrate of the normalized track is what bridges them.
    """
    limits = [target_chunk_s]

    if capabilities.max_chunk_bytes is not None:
        bytes_per_second = track.size_bytes / track.duration_s
        if bytes_per_second > 0:
            cap_s = math.floor(
                capabilities.max_chunk_bytes * BYTE_CAP_HEADROOM / bytes_per_second
            )
            if cap_s <= 0:
                raise ChunkTooLarge(
                    f"{bytes_per_second:.0f} B/s exceeds the "
                    f"{capabilities.max_chunk_bytes} B per-request cap of "
                    f"{capabilities.engine_id} even for a one-second chunk"
                )
            limits.append(float(cap_s))

    if capabilities.max_chunk_duration_s is not None:
        limits.append(capabilities.max_chunk_duration_s)

    return min(limits)


def plan_chunks(
    job_id: JobId,
    track: AudioTrack,
    capabilities: TranscriptionCapabilities,
    *,
    target_chunk_s: float = DEFAULT_TARGET_CHUNK_S,
    overlap_s: float = DEFAULT_OVERLAP_S,
    min_chunk_s: float = DEFAULT_MIN_CHUNK_S,
) -> ChunkPlan:
    if track.duration_s <= 0:
        raise ValueError(
            f"cannot plan chunks for a track of duration {track.duration_s}s"
        )

    stride_s = _stride_for(track, capabilities, target_chunk_s)
    count = math.ceil(track.duration_s / stride_s)
    chunks = [
        PlannedChunk(
            index=i,
            start_s=i * stride_s,
            # The tail carries the overlap; the final chunk clamps to the track.
            end_s=min(track.duration_s, i * stride_s + stride_s + overlap_s),
        )
        for i in range(count)
    ]

    # Absorb a too-short tail. Only possible with a predecessor to absorb it, so a
    # track shorter than min_chunk_s stays a single chunk rather than vanishing.
    # The predecessor grows by less than min_chunk_s, which the byte cap's 0.9
    # headroom already covers at any realistic bitrate.
    if len(chunks) > 1 and track.duration_s - chunks[-1].start_s < min_chunk_s:
        absorbed = chunks.pop()
        predecessor = chunks[-1]
        chunks[-1] = PlannedChunk(
            index=predecessor.index,
            start_s=predecessor.start_s,
            end_s=absorbed.end_s,
        )

    return ChunkPlan(
        job_id=job_id, stride_s=stride_s, overlap_s=overlap_s, chunks=tuple(chunks)
    )
