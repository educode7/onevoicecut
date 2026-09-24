"""Resolving profile names, and the three ways it refuses.

A render profile carries what a destination implies about the *file*: the output
spec, the caption safe area, the duration ceiling. Resolution is where a
configuration mistake becomes visible, and it has to become visible **before** a
job runs — a dangling profile name that only failed at render time would fail
after the transcription hours were already spent.

**There is no fallback profile, and that is the whole point.** The caption safe
area is the fifth no-silent-degradation axis and the least visible of them: a
caption under a destination's interface overlay is correct in the file, correct
in a local player, and wrong only in the app it was made for. A shared default
margin is exactly how that failure gets introduced — right for the profile it was
measured against, silently wrong for every profile that inherited it. So an
unmeasured profile is refused rather than given somebody else's margin.
"""

import pytest

from onevoicecut.domain.errors import DomainError, RenderProfileInvalid
from onevoicecut.domain.generation import ScriptVariant
from onevoicecut.domain.rendering import OutputSpec, RenderProfile, SafeArea
from onevoicecut.usecases.generate_artifacts import ScriptTarget
from onevoicecut.usecases.render_profiles import (
    RENDER_PROFILES,
    group_variants_by_profile,
    resolve_render_profiles,
)

MEASURED = RenderProfile(
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
# Declared but never measured — a destination somebody added to the registry
# before sitting down with the app. Legal to record, refused to render.
UNMEASURED = RenderProfile(
    name="unmeasured",
    output=OutputSpec(width=1080, height=1920),
    safe_area=None,
    max_duration_s=60.0,
)

REGISTRY = {p.name: p for p in (MEASURED, SQUARE, UNMEASURED)}


class TestTheShippedRegistryIsMeasured:
    """The shipped `vertical` profile is the whole point of this change: it is
    what every real clip request resolves against, and it used to declare no
    safe area — so `resolve_render_profiles` refused it by name and the render
    pipeline never ran end to end. These two pin the measured intersection and
    the fact that resolution now succeeds against the *shipped* registry, not an
    injected one. The refusal proofs above deliberately keep using injected
    registries so a future unmeasured profile still has a pinned refusal path."""

    def test_the_shipped_vertical_profile_carries_the_measured_intersection(
        self,
    ) -> None:
        """Exact values, not approximations: these are a measurement against the
        four destinations' 2026 interfaces (see odd/tasks/measure-safe-areas.md),
        and a silent drift here would move every caption this system renders."""
        assert RENDER_PROFILES["vertical"].safe_area == SafeArea(
            top=0.115, bottom=0.252, left=0.080, right=0.130
        )

    def test_resolving_the_shipped_vertical_profile_succeeds(self) -> None:
        """No `registry=` argument: this resolves against the shipped mapping the
        way production does. Before the safe area was populated this raised
        `RenderProfileInvalid`; the whole gap between a finished transcript and a
        rendered file was exactly this call refusing."""
        resolved = resolve_render_profiles("vertical")

        assert resolved == (RENDER_PROFILES["vertical"],)


class TestWhatResolvesCleanly:
    def test_one_name_resolves_to_its_profile(self) -> None:
        assert resolve_render_profiles("vertical", registry=REGISTRY) == (MEASURED,)

    def test_several_names_resolve_in_the_order_asked(self) -> None:
        """Order is the caller's, not the registry's, for the same reason
        `resolve_script_targets` preserves it: the operator wrote the list."""
        resolved = resolve_render_profiles("square,vertical", registry=REGISTRY)

        assert resolved == (SQUARE, MEASURED)

    def test_whitespace_around_a_name_is_tolerated(self) -> None:
        """Same comma-separated shape the operator token map already uses."""
        assert resolve_render_profiles(" vertical , square ", registry=REGISTRY) == (
            MEASURED,
            SQUARE,
        )

    def test_a_repeated_name_resolves_once(self) -> None:
        """Distinctness is the render dedup made structural. Two networks naming
        one profile must share a file, and a resolver that returned it twice
        would put two byte-identical renders in the job directory with nothing
        to tell them apart."""
        assert resolve_render_profiles("vertical,vertical", registry=REGISTRY) == (
            MEASURED,
        )


class TestTheThreeRefusals:
    def test_an_unknown_name_is_refused_and_the_error_names_what_exists(self) -> None:
        """Never a fallback. An operator who asked for a profile that does not
        exist and silently got another one would have no way to tell — the
        artifact looks fine, which is this change's whole failure shape."""
        with pytest.raises(RenderProfileInvalid) as caught:
            resolve_render_profiles("reels", registry=REGISTRY)

        message = str(caught.value)
        assert "reels" in message
        assert "vertical" in message

    def test_an_empty_selection_is_refused(self) -> None:
        """A build configured to render under no profile produces nothing, and
        should say so at the call rather than completing with an empty job
        directory."""
        with pytest.raises(RenderProfileInvalid):
            resolve_render_profiles("", registry=REGISTRY)

    def test_a_whitespace_only_selection_is_refused(self) -> None:
        assert_refused = pytest.raises(RenderProfileInvalid)
        with assert_refused:
            resolve_render_profiles("  ,  ", registry=REGISTRY)

    def test_a_profile_with_no_measured_safe_area_is_refused_by_name(self) -> None:
        """The spec's own scenario. `None` records that nobody has measured this
        destination yet — a real state the registry must be able to hold — and
        resolution is where that gap stops the job instead of becoming an
        inherited margin."""
        with pytest.raises(RenderProfileInvalid) as caught:
            resolve_render_profiles("unmeasured", registry=REGISTRY)

        assert "unmeasured" in str(caught.value)

    def test_one_unmeasured_profile_refuses_the_whole_selection(self) -> None:
        """Not a partial resolution. Rendering the two that were measured and
        quietly dropping the third is the silent degradation stated backwards:
        the operator asked for three destinations and would get two files with
        nothing saying why."""
        with pytest.raises(RenderProfileInvalid):
            resolve_render_profiles("vertical,unmeasured,square", registry=REGISTRY)


class TestTheErrorType:
    def test_it_is_a_domain_error(self) -> None:
        """Raised across a port boundary like every other refusal in this
        system, so no caller has to catch a library exception."""
        assert issubclass(RenderProfileInvalid, DomainError)

    def test_it_is_distinct_from_a_render_failure(self) -> None:
        """The worker classifies on the type. A bad profile configuration fails
        identically on every retry; a render may not. One type would make
        "retry or refuse" undecidable without reading a message — the same
        reasoning that separated `ClipRangeInvalid` from `RenderFailed`."""
        from onevoicecut.domain.errors import RenderFailed

        assert not issubclass(RenderProfileInvalid, RenderFailed)
        assert not issubclass(RenderFailed, RenderProfileInvalid)


# [moved from runtime/render_worker.py's TestProfileFanOut] Three networks
# sharing `vertical`, one naming `square` -- the shape the spec's own scenario
# uses: four networks, two distinct profiles.
GROUPING_SCRIPT_TARGETS = {
    "tiktok": ScriptTarget(
        name="tiktok", format="plain", duration_target_s=45.0, profile="vertical"
    ),
    "instagram": ScriptTarget(
        name="instagram", format="plain", duration_target_s=45.0, profile="vertical"
    ),
    "youtube": ScriptTarget(
        name="youtube", format="plain", duration_target_s=45.0, profile="vertical"
    ),
    "facebook": ScriptTarget(
        name="facebook", format="plain", duration_target_s=45.0, profile="square"
    ),
}
GROUPING_REGISTRY = {"vertical": MEASURED, "square": SQUARE}


def a_variant(target: str) -> ScriptVariant:
    return ScriptVariant(
        target=target, format="plain", body=f"Hola {target}", duration_target_s=45.0
    )


class TestGroupVariantsByProfile:
    """A candidate's variants, grouped by the distinct render profile they
    resolve to -- moved here from `runtime/render_worker.py`'s
    `_grouped_profiles` because it is pure logic over these two registries and
    no port; `13b-iv`'s HTTP route needs the identical join to write one
    `PENDING` export per distinct profile before any worker process exists."""

    def test_four_networks_resolving_to_two_profiles_produce_two_groups(
        self,
    ) -> None:
        grouped = group_variants_by_profile(
            tuple(a_variant(t) for t in ("tiktok", "instagram", "youtube", "facebook")),
            script_targets=GROUPING_SCRIPT_TARGETS,
            render_profiles=GROUPING_REGISTRY,
        )

        assert {profile.name for profile, _ in grouped} == {"vertical", "square"}

    def test_three_networks_sharing_one_profile_land_in_one_group(self) -> None:
        grouped = group_variants_by_profile(
            tuple(a_variant(t) for t in ("tiktok", "instagram", "youtube", "facebook")),
            script_targets=GROUPING_SCRIPT_TARGETS,
            render_profiles=GROUPING_REGISTRY,
        )
        vertical = next(variants for profile, variants in grouped if profile.name == "vertical")

        assert {v.target for v in vertical} == {"tiktok", "instagram", "youtube"}

    def test_the_other_profiles_group_carries_only_its_own_network(self) -> None:
        grouped = group_variants_by_profile(
            tuple(a_variant(t) for t in ("tiktok", "instagram", "youtube", "facebook")),
            script_targets=GROUPING_SCRIPT_TARGETS,
            render_profiles=GROUPING_REGISTRY,
        )
        square = next(variants for profile, variants in grouped if profile.name == "square")

        assert {v.target for v in square} == {"facebook"}

    def test_dedup_is_on_the_profile_not_the_network(self) -> None:
        """Three networks all naming `vertical` -- one group, not three."""
        grouped = group_variants_by_profile(
            tuple(a_variant(t) for t in ("tiktok", "instagram", "youtube")),
            script_targets=GROUPING_SCRIPT_TARGETS,
            render_profiles=GROUPING_REGISTRY,
        )

        assert len(grouped) == 1
        assert grouped[0][0].name == "vertical"

    def test_order_is_first_seen_among_the_variants(self) -> None:
        """The same rule `resolve_render_profiles` applies to an operator's
        comma list, reused here rather than re-implemented. `tiktok` before
        `facebook` deliberately disagrees with alphabetical order
        (`square` < `vertical`), so a caller that sorted instead of
        preserving first-seen order would still pass a fixture that happened
        to agree with it."""
        grouped = group_variants_by_profile(
            tuple(a_variant(t) for t in ("tiktok", "facebook")),
            script_targets=GROUPING_SCRIPT_TARGETS,
            render_profiles=GROUPING_REGISTRY,
        )

        assert [profile.name for profile, _ in grouped] == ["vertical", "square"]

    def test_one_unmeasured_profile_refuses_the_whole_selection(self) -> None:
        """Resolving the measured profile and quietly dropping the unmeasured
        one is the silent degradation stated backwards."""
        script_targets = {
            **GROUPING_SCRIPT_TARGETS,
            "unmeasured-net": ScriptTarget(
                name="unmeasured-net",
                format="plain",
                duration_target_s=45.0,
                profile="unmeasured",
            ),
        }
        render_profiles = {**GROUPING_REGISTRY, "unmeasured": UNMEASURED}

        with pytest.raises(RenderProfileInvalid):
            group_variants_by_profile(
                (a_variant("tiktok"), a_variant("unmeasured-net")),
                script_targets=script_targets,
                render_profiles=render_profiles,
            )

    def test_a_variant_naming_an_unconfigured_network_is_refused(self) -> None:
        """A network no `ScriptTarget` maps to a profile at all -- distinct
        from an unmeasured *profile*, which at least resolves to a name."""
        with pytest.raises(RenderProfileInvalid) as caught:
            group_variants_by_profile(
                (a_variant("a-network-no-script-target-maps"),),
                script_targets=GROUPING_SCRIPT_TARGETS,
                render_profiles=GROUPING_REGISTRY,
            )

        assert "a-network-no-script-target-maps" in str(caught.value)
