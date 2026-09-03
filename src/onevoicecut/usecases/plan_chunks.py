"""Plan chunk boundaries for a multi-hour track, independent of the ASR engine.

Pure arithmetic, no I/O. Living above the port is what lets long-audio
correctness be proven before either real engine exists — and what stops chunk
mechanics from leaking into adapters, where each engine would solve it
differently and none of it would be testable.
"""

import math

from onevoicecut.domain.chunking import ChunkPlan, PlannedChunk
from onevoicecut.domain.errors import ChunkTooLarge
from onevoicecut.domain.ids import JobId
from onevoicecut.domain.media import AudioTrack
from onevoicecut.ports.capabilities import TranscriptionCapabilities

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
    track: AudioTrack,
    capabilities: TranscriptionCapabilities,
    target_chunk_s: float,
    appended_s: float,
) -> float:
    """Reconcile a byte cap and a duration cap into one stride, in seconds.

    A provider limit is expressed in bytes but a plan is expressed in time, so
    the measured bitrate of the normalized track is what bridges them.

    `appended_s` is the part that is easy to lose: **no chunk is one stride
    long.** Every chunk carries the overlap tail, and the chunk that absorbs a
    short final one grows by nearly `min_chunk_s` instead. Sizing the budget
    against the stride alone therefore sizes it against a chunk that does not
    exist, and the difference is charged to the headroom — which covers it only
    up to a bitrate nothing states or enforces. Reserving the seconds here makes
    the guarantee arithmetic rather than luck.
    """
    limits = [target_chunk_s]

    if capabilities.max_chunk_bytes is not None:
        bytes_per_second = track.size_bytes / track.duration_s
        if bytes_per_second > 0:
            budget_s = (
                capabilities.max_chunk_bytes * BYTE_CAP_HEADROOM / bytes_per_second
            )
            cap_s = math.floor(budget_s - appended_s)
            if cap_s <= 0:
                # Not "even for a one-second chunk" any more: the reserve is
                # mandatory, so the shortest chunk this plan shape can produce
                # is `appended_s` long before a single second of stride is
                # added. Naming the real floor keeps the message diagnosable.
                raise ChunkTooLarge(
                    f"{bytes_per_second:.0f} B/s exceeds the "
                    f"{capabilities.max_chunk_bytes} B per-request cap of "
                    f"{capabilities.engine_id} even for the shortest chunk a "
                    f"plan can produce ({appended_s:.0f}s of overlap and "
                    f"minimum-length reserve)"
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

    # The longest chunk any plan can contain is one stride plus whichever of
    # these two is larger — never both, because a chunk that absorbed a tail
    # clamps to the end of the track and carries no overlap past it.
    stride_s = _stride_for(
        track, capabilities, target_chunk_s, max(overlap_s, min_chunk_s)
    )
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
    # The predecessor grows by less than min_chunk_s, which the stride above has
    # already reserved room for — it used to be charged to the byte cap's 0.9
    # headroom instead, which covered it only below about 71 KB/s and said so
    # nowhere.
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
