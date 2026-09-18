"""One sweep of the render drain gate: what it starts, and what it never
exceeds.

`render_drain_once` is `drain_once`'s render-side twin, closing `13b.29`/
`13b.30`: a `PENDING` `ClipExport` with no live render worker is picked up,
and the sweep never exceeds a render concurrency cap. The active count is
derived exactly the way the job drain's is -- listed off storage, grouped by
clip, filtered by whether a `RENDERING` group's claim is still fresh -- so a
render worker that dies frees its slot the moment the claim goes stale, with
nothing to reconcile first.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from onevoicecut.domain.framing import TrackingConfidence
from onevoicecut.domain.generation import ScriptVariant
from onevoicecut.domain.ids import ClipId, JobId, make_clip_id, make_job_id
from onevoicecut.domain.rendering import (
    CaptionCoverage,
    ClipExport,
    ClipState,
    DurationCompliance,
    DurationComplianceKind,
    OutputQuality,
    OutputQualityKind,
    RenderedClip,
    SubtitleTimingSource,
)
from onevoicecut.runtime.app import render_drain_once
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

# ULIDs sort by creation time, so these three sort oldest-first -- the same
# fixture shape `test_drain_once.py` uses for the job drain.
OLDEST = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA1")
MIDDLE = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA2")
NEWEST = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA3")

CLIP_A = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCB1")
CLIP_B = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCB2")
CLIP_C = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCB3")

NOW = 1_700_000_000.0
DURATION_S = 30.0  # render_timeout_for(30.0) == max(60.0, 20.0 * 30.0) == 600.0
STALE_AFTER_S = 600.0


def _a_rendered_clip(job_id: JobId, clip_id: ClipId) -> RenderedClip:
    return RenderedClip(
        clip_id=clip_id,
        job_id=job_id,
        path=Path("clip.mp4"),
        source_start_s=0.0,
        source_end_s=DURATION_S,
        quality=OutputQuality(kind=OutputQualityKind.NATIVE, factor=1.0),
        subtitle_timing=SubtitleTimingSource.WORD_LEVEL,
        captions=CaptionCoverage.CONFIRMED_SPEECH,
        tracking=TrackingConfidence.WELL_TRACKED,
        duration=DurationCompliance(kind=DurationComplianceKind.WITHIN_CEILING, overrun_s=0.0),
    )


def an_export(
    job_id: JobId,
    clip_id: ClipId,
    *,
    profile: str = "vertical",
    state: ClipState = ClipState.PENDING,
) -> ClipExport:
    return ClipExport(
        job_id=job_id,
        clip_id=clip_id,
        profile=profile,
        source_start_s=0.0,
        source_end_s=DURATION_S,
        title="Hermanos, escuchen",
        description="Un momento del sermon",
        variants=(
            ScriptVariant(
                target="tiktok", format="plain", body="Hola", duration_target_s=45.0
            ),
        ),
        state=state,
        clip=_a_rendered_clip(job_id, clip_id) if state is ClipState.DONE else None,
        failure="synthetic terminal failure" if state is ClipState.FAILED else None,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


@pytest.fixture
def launched() -> list[tuple[JobId, ClipId]]:
    return []


def sweep(
    storage: FakeTranscriptStoragePort,
    launched: list[tuple[JobId, ClipId]],
    *,
    cap: int = 1,
    spawned: set[tuple[JobId, ClipId]] | None = None,
    now: Callable[[], float] = lambda: NOW,
) -> tuple[tuple[JobId, ClipId], ...]:
    return render_drain_once(
        storage,
        max_concurrent_renders=cap,
        launch=lambda job_id, clip_id: launched.append((job_id, clip_id)),
        spawned=set() if spawned is None else spawned,
        now=now,
    )


class TestAPendingExportIsPickedUp:
    def test_a_pending_export_with_no_claim_is_launched(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
    ) -> None:
        storage.save_clip_export(an_export(OLDEST, CLIP_A))

        sweep(storage, launched, cap=1)

        assert launched == [(OLDEST, CLIP_A)]

    def test_a_clip_rendered_under_two_profiles_is_one_slot_not_two(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
    ) -> None:
        """One process claims a whole clip's pending profiles at once, so the
        cap must count the clip, never the row."""
        storage.save_clip_export(an_export(OLDEST, CLIP_A, profile="vertical"))
        storage.save_clip_export(an_export(OLDEST, CLIP_A, profile="square"))

        sweep(storage, launched, cap=1)

        assert launched == [(OLDEST, CLIP_A)]

    def test_a_group_with_only_terminal_exports_is_never_launched(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
    ) -> None:
        storage.save_clip_export(an_export(OLDEST, CLIP_A, state=ClipState.DONE))
        storage.save_clip_export(an_export(OLDEST, CLIP_B, state=ClipState.FAILED))

        sweep(storage, launched, cap=3)

        assert launched == []


class TestTheCapIsNeverExceeded:
    @pytest.mark.parametrize("cap", [1, 2, 3])
    def test_a_sweep_never_launches_past_the_cap(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
        cap: int,
    ) -> None:
        for job_id, clip_id in ((OLDEST, CLIP_A), (MIDDLE, CLIP_B), (NEWEST, CLIP_C)):
            storage.save_clip_export(an_export(job_id, clip_id))

        sweep(storage, launched, cap=cap)

        assert len(launched) == min(cap, 3)

    def test_queued_work_waits_while_the_cap_is_full(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
    ) -> None:
        storage.save_clip_export(an_export(OLDEST, CLIP_A, state=ClipState.RENDERING))
        storage.write_render_claim(OLDEST, CLIP_A, at_s=NOW)
        storage.save_clip_export(an_export(MIDDLE, CLIP_B))

        sweep(storage, launched, cap=1)

        assert launched == []

    def test_free_slots_are_filled_up_to_the_cap_and_no_further(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
    ) -> None:
        storage.save_clip_export(an_export(OLDEST, CLIP_A, state=ClipState.RENDERING))
        storage.write_render_claim(OLDEST, CLIP_A, at_s=NOW)
        storage.save_clip_export(an_export(MIDDLE, CLIP_B))
        storage.save_clip_export(an_export(NEWEST, CLIP_C))

        sweep(storage, launched, cap=2)

        assert launched == [(MIDDLE, CLIP_B)]


class TestLivenessIsAClaimNotAPid:
    def test_a_fresh_claim_makes_the_group_live_and_it_is_not_relaunched(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
    ) -> None:
        storage.save_clip_export(an_export(OLDEST, CLIP_A, state=ClipState.RENDERING))
        storage.write_render_claim(OLDEST, CLIP_A, at_s=NOW - 100.0)

        sweep(storage, launched, cap=5, now=lambda: NOW)

        assert launched == []

    def test_a_stale_claim_is_an_abandoned_render_and_is_relaunched(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
    ) -> None:
        """The claim was written well past `render_timeout_for`'s own bound
        for this export's duration -- the worker that wrote it is gone, and
        nothing else will ever notice except this sweep."""
        storage.save_clip_export(an_export(OLDEST, CLIP_A, state=ClipState.RENDERING))
        storage.write_render_claim(OLDEST, CLIP_A, at_s=NOW - (STALE_AFTER_S + 1.0))

        sweep(storage, launched, cap=5, now=lambda: NOW)

        assert launched == [(OLDEST, CLIP_A)]

    def test_a_rendering_export_with_no_claim_at_all_is_treated_as_abandoned(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
    ) -> None:
        """`RENDERING` with no claim on file is not a state a real worker
        leaves -- the claim step always writes one -- but the sweep must
        still fail closed toward eligibility rather than toward silence."""
        storage.save_clip_export(an_export(OLDEST, CLIP_A, state=ClipState.RENDERING))

        sweep(storage, launched, cap=5, now=lambda: NOW)

        assert launched == [(OLDEST, CLIP_A)]


class TestOrdering:
    def test_eligible_clips_launch_oldest_first(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
    ) -> None:
        for job_id, clip_id in ((NEWEST, CLIP_C), (OLDEST, CLIP_A), (MIDDLE, CLIP_B)):
            storage.save_clip_export(an_export(job_id, clip_id))

        sweep(storage, launched, cap=3)

        assert launched == [(OLDEST, CLIP_A), (MIDDLE, CLIP_B), (NEWEST, CLIP_C)]


class TestTheSpawnedSet:
    """Between the launcher call and the render worker's own claim write, a
    group still reads `PENDING`. Without this memory the next sweep, five
    seconds later, would start a second worker on the same clip."""

    def test_an_issued_spawn_is_not_repeated_on_the_next_sweep(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
    ) -> None:
        storage.save_clip_export(an_export(OLDEST, CLIP_A))
        spawned: set[tuple[JobId, ClipId]] = set()

        sweep(storage, launched, cap=1, spawned=spawned)
        sweep(storage, launched, cap=1, spawned=spawned)

        assert launched == [(OLDEST, CLIP_A)]

    def test_a_claimed_group_is_pruned_from_the_spawned_set(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
    ) -> None:
        """Once the worker's own claim lands, the group reads as live and no
        longer needs the launcher's memory to stay uncounted."""
        storage.save_clip_export(an_export(OLDEST, CLIP_A))
        spawned: set[tuple[JobId, ClipId]] = set()
        sweep(storage, launched, cap=1, spawned=spawned)

        storage.save_clip_export(an_export(OLDEST, CLIP_A, state=ClipState.RENDERING))
        storage.write_render_claim(OLDEST, CLIP_A, at_s=NOW)
        sweep(storage, launched, cap=1, spawned=spawned)

        assert spawned == set()

    def test_a_finished_group_is_pruned_from_the_spawned_set(
        self,
        storage: FakeTranscriptStoragePort,
        launched: list[tuple[JobId, ClipId]],
    ) -> None:
        storage.save_clip_export(an_export(OLDEST, CLIP_A))
        spawned: set[tuple[JobId, ClipId]] = set()
        sweep(storage, launched, cap=1, spawned=spawned)

        storage.save_clip_export(an_export(OLDEST, CLIP_A, state=ClipState.DONE))
        sweep(storage, launched, cap=1, spawned=spawned)

        assert spawned == set()
