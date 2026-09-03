"""Chunk planning arithmetic — pure functions, no I/O, no fakes needed.

This is the long-audio correctness that must hold before any real engine exists.
Multi-hour input is the normal case, so every property asserted here (full
coverage, overlap at every internal boundary, no zero-length chunk) is a routine
requirement rather than an edge case.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.errors import ChunkTooLarge
from onevoicecut.domain.ids import make_job_id, make_media_id
from onevoicecut.domain.media import AudioTrack
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DiarizationSupport,
    TranscriptionCapabilities,
)
from onevoicecut.usecases.plan_chunks import (
    DEFAULT_OVERLAP_S,
    DEFAULT_TARGET_CHUNK_S,
    plan_chunks,
)

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")

# 16 kHz mono FLAC on speech sits near this rate; used so byte maths stays realistic.
FLAC_BYTES_PER_SECOND = 16_000


def _track(duration_s: float, bytes_per_second: float = FLAC_BYTES_PER_SECOND) -> AudioTrack:
    return AudioTrack(
        media_id=MEDIA_ID,
        path=Path("audio.flac"),
        duration_s=duration_s,
        size_bytes=int(duration_s * bytes_per_second),
    )


def _caps(
    max_chunk_bytes: int | None = None,
    max_chunk_duration_s: float | None = None,
    engine_id: str = "fake-asr",
) -> TranscriptionCapabilities:
    return TranscriptionCapabilities(
        engine_id=engine_id,
        diarization=DiarizationSupport.UNSUPPORTED,
        non_speech_classification=ClassificationSupport.AVAILABLE,
        max_chunk_bytes=max_chunk_bytes,
        max_chunk_duration_s=max_chunk_duration_s,
    )


def test_uncapped_plan_uses_target_stride() -> None:
    plan = plan_chunks(JOB_ID, _track(1500.0), _caps())
    assert plan.stride_s == DEFAULT_TARGET_CHUNK_S
    assert plan.overlap_s == DEFAULT_OVERLAP_S
    assert plan.job_id == JOB_ID


def test_chunk_bounds_follow_the_design_formula() -> None:
    """25 minutes at stride 600 / overlap 5: three chunks, last clamped to duration."""
    plan = plan_chunks(JOB_ID, _track(1500.0), _caps())
    assert [(c.index, c.start_s, c.end_s) for c in plan.chunks] == [
        (0, 0.0, 605.0),
        (1, 600.0, 1205.0),
        (2, 1200.0, 1500.0),
    ]


def test_every_internal_boundary_carries_overlap() -> None:
    """No hard cut anywhere: each chunk ends past where the next one starts."""
    plan = plan_chunks(JOB_ID, _track(3600.0), _caps())
    assert len(plan.chunks) > 1
    for previous, following in zip(plan.chunks, plan.chunks[1:]):
        assert previous.end_s > following.start_s
        assert previous.end_s - following.start_s == pytest.approx(plan.overlap_s)


def test_plan_covers_the_whole_track_with_no_gaps() -> None:
    plan = plan_chunks(JOB_ID, _track(3600.0), _caps())
    assert plan.chunks[0].start_s == 0.0
    assert plan.chunks[-1].end_s == 3600.0
    for previous, following in zip(plan.chunks, plan.chunks[1:]):
        assert following.start_s < previous.end_s  # covered, never skipped


def test_indices_are_contiguous_from_zero() -> None:
    plan = plan_chunks(JOB_ID, _track(3600.0), _caps())
    assert [c.index for c in plan.chunks] == list(range(len(plan.chunks)))


def test_no_chunk_is_empty() -> None:
    plan = plan_chunks(JOB_ID, _track(3600.0), _caps())
    assert all(c.end_s > c.start_s for c in plan.chunks)


def test_track_shorter_than_stride_is_one_chunk() -> None:
    plan = plan_chunks(JOB_ID, _track(120.0), _caps())
    assert [(c.start_s, c.end_s) for c in plan.chunks] == [(0.0, 120.0)]


def test_plan_is_independent_of_which_adapter_fulfils_it() -> None:
    """Two engines with identical limits plan identically, whatever else differs.

    The spec requires planning to live above the port. Capabilities still feed the
    plan — that is the point of declaring them — but engine identity must not.
    """
    track = _track(3600.0)
    local = plan_chunks(JOB_ID, track, _caps(engine_id="local-whisper"))
    cloud = plan_chunks(JOB_ID, track, _caps(engine_id="cloud-provider"))
    assert local.chunks == cloud.chunks
    assert local.stride_s == cloud.stride_s


def test_non_positive_duration_is_rejected() -> None:
    """Guards the bitrate division, and a zero-duration track is never plannable."""
    with pytest.raises(ValueError, match="duration"):
        plan_chunks(JOB_ID, _track(0.0), _caps())


# --- Byte cap: the cloud limit is in bytes, a plan is in time -----------------


def test_byte_cap_shortens_the_stride() -> None:
    """50 KB/s against a 25 MB cap: floor(25e6 * 0.9 / 50_000 - 30) = 420s.

    The 30 is the reserve for what gets appended after the stride is chosen —
    the overlap tail, or the absorbed short tail, whichever is larger. It used
    to be missing, which made the cap a promise about a chunk length no plan
    ever produced (see slice 8a-iii).
    """
    track = _track(3600.0, bytes_per_second=50_000)
    plan = plan_chunks(JOB_ID, track, _caps(max_chunk_bytes=25_000_000))
    assert plan.stride_s == 420.0


def test_realistic_flac_bitrate_is_not_constrained_by_the_cap() -> None:
    """16 kHz mono FLAC leaves substantial margin — the design's claim, pinned.

    If normalization ever changes format, this test fails and the planning
    consequence surfaces here rather than as a runtime ChunkTooLarge.
    """
    track = _track(3600.0, bytes_per_second=FLAC_BYTES_PER_SECOND)
    plan = plan_chunks(JOB_ID, track, _caps(max_chunk_bytes=25_000_000))
    assert plan.stride_s == DEFAULT_TARGET_CHUNK_S


def test_duration_cap_shortens_the_stride() -> None:
    plan = plan_chunks(JOB_ID, _track(3600.0), _caps(max_chunk_duration_s=300.0))
    assert plan.stride_s == 300.0


def test_tightest_constraint_wins() -> None:
    track = _track(3600.0, bytes_per_second=50_000)
    plan = plan_chunks(
        JOB_ID,
        track,
        _caps(max_chunk_bytes=25_000_000, max_chunk_duration_s=200.0),
    )
    assert plan.stride_s == 200.0  # duration cap beats the 450s byte cap


def test_planned_payload_stays_under_the_declared_byte_cap() -> None:
    """The property the stride derivation exists to guarantee."""
    bytes_per_second = 50_000
    cap = 25_000_000
    track = _track(3600.0, bytes_per_second=bytes_per_second)
    plan = plan_chunks(JOB_ID, track, _caps(max_chunk_bytes=cap))
    for chunk in plan.chunks:
        assert (chunk.end_s - chunk.start_s) * bytes_per_second <= cap


def test_bitrate_too_high_to_plan_is_rejected_not_silently_truncated() -> None:
    """If even a one-second chunk exceeds the cap, no plan exists.

    Without this guard the stride floors to zero and chunk generation never
    terminates. Failing loudly beats hanging on a multi-hour job.
    """
    track = _track(3600.0, bytes_per_second=50_000)
    with pytest.raises(ChunkTooLarge):
        plan_chunks(JOB_ID, track, _caps(max_chunk_bytes=1_000))


# --- Tail merge: short trailing chunks are where Whisper hallucinates most ----


def test_short_tail_merges_into_its_predecessor() -> None:
    """1210s at stride 600 would leave a 10s tail; it is absorbed instead."""
    plan = plan_chunks(JOB_ID, _track(1210.0), _caps())
    assert [(c.index, c.start_s, c.end_s) for c in plan.chunks] == [
        (0, 0.0, 605.0),
        (1, 600.0, 1210.0),
    ]


def test_tail_exactly_at_the_threshold_is_kept() -> None:
    """The bound is strict: 30s is long enough to decode on its own."""
    plan = plan_chunks(JOB_ID, _track(1230.0), _caps())
    assert len(plan.chunks) == 3
    assert plan.chunks[-1].start_s == 1200.0
    assert plan.chunks[-1].end_s == 1230.0


def test_tail_one_second_under_the_threshold_merges() -> None:
    plan = plan_chunks(JOB_ID, _track(1229.0), _caps())
    assert len(plan.chunks) == 2
    assert plan.chunks[-1].end_s == 1229.0


def test_merge_still_covers_the_whole_track() -> None:
    plan = plan_chunks(JOB_ID, _track(1210.0), _caps())
    assert plan.chunks[0].start_s == 0.0
    assert plan.chunks[-1].end_s == 1210.0


def test_merge_leaves_indices_contiguous() -> None:
    plan = plan_chunks(JOB_ID, _track(1210.0), _caps())
    assert [c.index for c in plan.chunks] == list(range(len(plan.chunks)))


def test_single_short_chunk_is_never_merged_away() -> None:
    """A 10s track has no predecessor to merge into — it must survive."""
    plan = plan_chunks(JOB_ID, _track(10.0), _caps())
    assert [(c.start_s, c.end_s) for c in plan.chunks] == [(0.0, 10.0)]


def test_merge_does_not_fire_on_a_long_tail() -> None:
    plan = plan_chunks(JOB_ID, _track(1500.0), _caps())
    assert len(plan.chunks) == 3


def test_merged_chunk_still_respects_the_byte_cap() -> None:
    """Absorbing the tail must not push the predecessor over the provider limit."""
    bytes_per_second = 50_000
    cap = 25_000_000
    # 420s stride, so an 840s+tail track leaves a tail under min_chunk_s (30s)
    # for the predecessor to absorb.
    track = _track(865.0, bytes_per_second=bytes_per_second)
    plan = plan_chunks(JOB_ID, track, _caps(max_chunk_bytes=cap))
    assert len(plan.chunks) == 2
    for chunk in plan.chunks:
        assert (chunk.end_s - chunk.start_s) * bytes_per_second <= cap
