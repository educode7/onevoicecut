"""Turning a `{candidate_index, targets}` request into `PENDING` exports.

This is the first production caller of `load_artifacts` (its port docstring
says so) and of `generate_clip_id`. The candidate resolution and the
networks-to-profiles fan-out are both landed already, in `load_artifacts` and
`group_variants_by_profile` respectively — this module joins them and decides
what an operator's mistake looks like on the way through.

**`targets` is checked against the candidate's own variants, never against
the render-profile registry directly.** `group_variants_by_profile` already
refuses a variant naming a network no `ScriptTarget` maps — but only for
variants it is handed, and pre-filtering to "networks the operator asked for"
happens before that function ever runs. A mistyped or ungenerated network
would simply vanish from the filtered set and never reach that check, so this
module owns its own refusal for it.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from onevoicecut.domain.errors import (
    ArtifactsNotAvailable,
    ClipCandidateNotFound,
    ClipTargetsInvalid,
    RenderProfileInvalid,
)
from onevoicecut.domain.generation import ClipCandidate, GenerationResult, ScriptVariant
from onevoicecut.domain.ids import ClipId, JobId, make_clip_id, make_job_id
from onevoicecut.domain.rendering import ClipState, OutputSpec, RenderProfile, SafeArea
from onevoicecut.usecases.generate_artifacts import ScriptTarget
from onevoicecut.usecases.request_clip_export import request_clip_export
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
CLIP_ID = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")

VERTICAL = RenderProfile(
    name="vertical",
    output=OutputSpec(width=1080, height=1920),
    safe_area=SafeArea(top=0.06, bottom=0.18, left=0.05, right=0.14),
    max_duration_s=90.0,
)
SQUARE = RenderProfile(
    name="square",
    output=OutputSpec(width=1080, height=1080),
    safe_area=SafeArea(top=0.04, bottom=0.10, left=0.04, right=0.04),
    max_duration_s=60.0,
)
RENDER_PROFILES = {"vertical": VERTICAL, "square": SQUARE}

SCRIPT_TARGETS = {
    "tiktok": ScriptTarget(name="tiktok", format="plain", duration_target_s=45.0, profile="vertical"),
    "instagram": ScriptTarget(
        name="instagram", format="plain", duration_target_s=45.0, profile="vertical"
    ),
    "facebook": ScriptTarget(name="facebook", format="plain", duration_target_s=45.0, profile="square"),
}


def a_variant(target: str) -> ScriptVariant:
    return ScriptVariant(target=target, format="plain", body=f"Hola {target}", duration_target_s=45.0)


def a_candidate(
    *, start_s: float = 120.0, end_s: float = 150.0, variants: tuple[ScriptVariant, ...] | None = None
) -> ClipCandidate:
    return ClipCandidate(
        start_s=start_s,
        end_s=end_s,
        hook="Hermanos, escuchen",
        quote="Un momento del sermon",
        rationale="Resume el argumento central",
        score=0.9,
        variants=(
            variants
            if variants is not None
            else (a_variant("tiktok"), a_variant("instagram"), a_variant("facebook"))
        ),
    )


def new_clip_id_fixed(clip_id: ClipId = CLIP_ID) -> Callable[[], ClipId]:
    return lambda: clip_id


def _store(*candidates: ClipCandidate) -> FakeTranscriptStoragePort:
    store = FakeTranscriptStoragePort(Path("unused"))
    store.save_artifacts(
        JOB_ID, GenerationResult(job_id=JOB_ID, summary="s", clip_candidates=candidates)
    )
    return store


def request(
    store: FakeTranscriptStoragePort,
    *,
    candidate_index: int = 0,
    targets: tuple[str, ...] = ("tiktok", "facebook"),
) -> tuple[ClipId, tuple[RenderProfile, ...]]:
    return request_clip_export(
        JOB_ID,
        candidate_index,
        targets,
        storage=store,
        new_clip_id=new_clip_id_fixed(),
        script_targets=SCRIPT_TARGETS,
        render_profiles=RENDER_PROFILES,
    )


class TestResolvingTheCandidate:
    def test_a_completed_candidate_yields_a_fresh_clip_id(self) -> None:
        store = _store(a_candidate())

        clip_id, _ = request(store)

        assert clip_id == CLIP_ID

    def test_absent_artifacts_are_refused(self) -> None:
        store = FakeTranscriptStoragePort(Path("unused"))

        with pytest.raises(ArtifactsNotAvailable):
            request(store)

    def test_an_out_of_range_candidate_index_is_refused(self) -> None:
        store = _store(a_candidate())

        with pytest.raises(ClipCandidateNotFound) as caught:
            request(store, candidate_index=5)

        assert "5" in str(caught.value)

    def test_a_negative_candidate_index_is_refused(self) -> None:
        """Bounds are checked, not Python's negative-index wraparound."""
        store = _store(a_candidate())

        with pytest.raises(ClipCandidateNotFound):
            request(store, candidate_index=-1)


class TestTheProfileFanOut:
    def test_two_networks_resolving_to_two_profiles_write_two_exports(self) -> None:
        store = _store(a_candidate())

        clip_id, profiles = request(store, targets=("tiktok", "facebook"))

        assert {p.name for p in profiles} == {"vertical", "square"}
        assert len(store.load_clip_exports(JOB_ID, clip_id)) == 2

    def test_the_response_reports_resolved_profiles_not_requested_networks(self) -> None:
        """Two networks sharing one profile resolve to one profile name, not two."""
        store = _store(a_candidate())

        _, profiles = request(store, targets=("tiktok", "instagram"))

        assert [p.name for p in profiles] == ["vertical"]

    def test_every_written_export_is_pending_with_no_clip(self) -> None:
        store = _store(a_candidate())

        clip_id, _ = request(store, targets=("tiktok",))

        (export,) = store.load_clip_exports(JOB_ID, clip_id)
        assert export.state is ClipState.PENDING
        assert export.clip is None

    def test_every_written_export_carries_the_candidates_source_range(self) -> None:
        store = _store(a_candidate(start_s=200.0, end_s=240.0))

        clip_id, _ = request(store, targets=("tiktok", "facebook"))

        for export in store.load_clip_exports(JOB_ID, clip_id):
            assert (export.source_start_s, export.source_end_s) == (200.0, 240.0)

    def test_an_unmatched_target_network_is_refused(self) -> None:
        """A network naming no variant on this candidate at all -- a typo, or
        a network this candidate was never scripted for."""
        store = _store(a_candidate())

        with pytest.raises(ClipTargetsInvalid) as caught:
            request(store, targets=("tiktok", "bluesky"))

        assert "bluesky" in str(caught.value)

    def test_an_unmatched_target_writes_nothing(self) -> None:
        """The whole request is refused, not a partial write of the valid half."""
        store = _store(a_candidate())

        with pytest.raises(ClipTargetsInvalid):
            request(store, targets=("tiktok", "bluesky"))

        assert store.load_clip_exports(JOB_ID, CLIP_ID) == ()

    def test_a_variant_naming_a_network_the_registry_no_longer_maps_is_refused(
        self,
    ) -> None:
        """Registry drift between generation and this request -- caught by
        `group_variants_by_profile`, not re-implemented here."""
        store = _store(
            a_candidate(variants=(a_variant("tiktok"), a_variant("a-network-nobody-configured")))
        )

        with pytest.raises(RenderProfileInvalid):
            request(store, targets=("tiktok", "a-network-nobody-configured"))
