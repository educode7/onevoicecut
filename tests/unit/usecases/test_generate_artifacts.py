"""The target registry after Open Question 3, and the one string it may hold.

Four destinations, four rows. The requirement they close is not "these four
exist" -- it is that a fifth is a row rather than a code change, which is why
almost nothing here asserts a platform fact. What it asserts is the shape: a
target declares its label, its script format, its duration target and the *name*
of the render profile its clips are delivered under, and it declares all four
before the model is asked.

**The link to rendering is a name and nothing else.** That is the
`Scope Boundary -- No Rendering` requirement holding under a multi-target
delivery: generation decides which moments are worth cutting and what to say
about them, and stays unable to express an opinion about how a frame is cropped
even by accident. A `width` on `ScriptTarget` would be that opinion, so the field
set is asserted structurally -- an absence cannot be proven by calling something.

Two destinations naming one profile is the case the render dedup exists for:
networks whose clips are framed identically differ only in their scripts, and
they say so by naming the same profile. Two *targets* with one label is the
opposite case and is refused, because a candidate carrying two variants labelled
`tiktok` is an artifact nobody can tell apart.
"""

import dataclasses

import pytest

from onevoicecut.domain.errors import GenerationFailed
from onevoicecut.usecases.generate_artifacts import (
    DEFAULT_SCRIPT_TARGETS,
    SCRIPT_TARGETS,
    ScriptTarget,
    resolve_script_targets,
)

CONFIRMED_DESTINATIONS = ("tiktok", "instagram", "youtube", "facebook")


def _fields() -> dict[str, dataclasses.Field[object]]:
    return {field.name: field for field in dataclasses.fields(ScriptTarget)}


class TestWhatATargetDeclares:
    def test_it_names_a_render_profile(self) -> None:
        """The whole linkage, in one string. Resolving it to geometry belongs to
        `clip-rendering`, which is where the frame is known."""
        assert _fields()["profile"].type in ("str", str)

    def test_the_profile_name_cannot_be_omitted(self) -> None:
        """No default, for the reason `RenderProfile.safe_area` has none: a
        default is the failure this link exists to prevent wearing the shape of
        a convenience. A row added without a profile would be delivered under
        whichever one the default happened to name."""
        field = _fields()["profile"]

        assert field.default is dataclasses.MISSING
        assert field.default_factory is dataclasses.MISSING

    def test_every_shipped_row_names_one(self) -> None:
        assert all(target.profile for target in SCRIPT_TARGETS.values())


class TestTheFourConfirmedDestinations:
    def test_all_four_are_defined(self) -> None:
        """Open Question 3's answer, as data. A fifth network is another row."""
        assert set(CONFIRMED_DESTINATIONS) <= set(SCRIPT_TARGETS)

    def test_the_default_selection_is_all_four(self) -> None:
        """The operator publishes to every one of them, so a build that wrote
        scripts for one would silently deliver three fewer than asked."""
        assert [t.name for t in resolve_script_targets(DEFAULT_SCRIPT_TARGETS)] == list(
            CONFIRMED_DESTINATIONS
        )

    def test_a_row_is_known_by_the_key_it_is_filed_under(self) -> None:
        """Otherwise `resolve_script_targets` returns a target whose `name` --
        the label that reaches `ScriptVariant.target` -- is not the name the
        operator wrote."""
        assert all(key == target.name for key, target in SCRIPT_TARGETS.items())


class TestItHoldsNoGeometry:
    # Every word a pixel-level opinion could arrive under. Substrings rather
    # than exact names, so `caption_margin_v` or `output_width` is caught too.
    # Not `ratio` — "duration" contains it, and a matcher that fires on a field
    # this type is required to have is a matcher somebody deletes.
    FORBIDDEN = ("width", "height", "aspect", "caption", "margin", "safe", "crop")

    def test_no_field_names_a_frame_property(self) -> None:
        """Asserted over the field set rather than by rendering something and
        finding nothing, because an absence cannot be proven by calling
        something -- the same reason the scope boundary is parsed rather than
        run. A field here is a place a frame opinion can be written, and the
        boundary that only holds because nobody wrote one is not a boundary.
        """
        offending = [
            name
            for name in _fields()
            for word in self.FORBIDDEN
            if word in name.lower()
        ]

        assert offending == []

    def test_the_only_thing_it_says_about_rendering_is_a_name(self) -> None:
        """A string, not a `RenderProfile`. Holding the profile itself would put
        the output spec and the safe area inside generation by reference, which
        is the boundary crossed with an import instead of a field."""
        assert isinstance(SCRIPT_TARGETS["tiktok"].profile, str)


class TestTwoNetworksMayShareOneProfile:
    def test_sharing_a_profile_is_representable(self) -> None:
        """Not a property of the shipped four -- a property of the type. Two
        destinations framed identically differ only in their scripts."""
        reel = ScriptTarget(
            name="reel", format="plain", duration_target_s=30.0, profile="vertical"
        )
        short = ScriptTarget(
            name="short", format="plain", duration_target_s=30.0, profile="vertical"
        )

        assert reel.profile == short.profile
        assert reel.name != short.name


class TestOneLabelPerCandidate:
    def test_a_repeated_target_resolves_once(self) -> None:
        """Two variants on one candidate must not carry the same label. Resolving
        the repeat away makes that structural rather than a rule somebody
        remembers -- and it also stops a typo billing the same script twice."""
        assert len(resolve_script_targets("tiktok,tiktok")) == 1

    def test_distinct_targets_still_all_resolve(self) -> None:
        assert len(resolve_script_targets("tiktok,instagram")) == 2

    def test_the_surviving_order_is_the_one_asked_for(self) -> None:
        resolved = resolve_script_targets("youtube,tiktok,youtube")

        assert [t.name for t in resolved] == ["youtube", "tiktok"]


class TestItStillRefusesRatherThanSubstituting:
    """Four rows instead of one changed what a fallback would look like, not
    whether there is one. There is not."""

    def test_an_unknown_name_is_refused_naming_what_exists(self) -> None:
        with pytest.raises(GenerationFailed) as refusal:
            resolve_script_targets("myspace")

        assert "tiktok" in str(refusal.value)

    def test_an_empty_selection_is_refused(self) -> None:
        """The script artifact is this system's stopping point, so a build
        configured to write none of them produces nothing and says so."""
        with pytest.raises(GenerationFailed):
            resolve_script_targets("  ,  ")

    def test_one_bad_name_refuses_the_whole_selection(self) -> None:
        """Not the three that resolved. Dropping the fourth is the silent
        degradation stated backwards: the operator asked for four destinations
        and would get three artifacts with nothing saying why."""
        with pytest.raises(GenerationFailed):
            resolve_script_targets("tiktok,instagram,myspace")

    def test_nothing_stands_in_for_a_name_that_did_not_resolve(self) -> None:
        """The refusal is the point. An operator who asked for a Reels script
        and silently got somebody else's would have no way to tell."""
        with pytest.raises(GenerationFailed):
            resolve_script_targets("reels")


class TestGenericIsGone:
    def test_the_placeholder_no_longer_resolves(self) -> None:
        """It existed only while Q3 was open. Keeping it alongside the answer
        would let an operator configure a destination that is nowhere, and the
        prompt would say `Destino: generic` -- the unlabelled generic script the
        requirement refuses, produced on purpose."""
        with pytest.raises(GenerationFailed):
            resolve_script_targets("generic")
