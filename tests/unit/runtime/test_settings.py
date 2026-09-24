"""The two registries are cross-checked at composition, and what that check is.

A script target names a render profile by string. Nothing in either registry
forces that string to mean anything -- a target row added with `profil="vertcal"`
type-checks, imports, and transcribes three hours of audio before anybody finds
out. So the two registries are read against each other while the server is
booting, which is the last moment before a job can start.

**The check is membership, not renderability, and the difference is the whole
design.** `resolve_render_profiles` refuses a profile whose caption safe area
nobody has measured -- deliberately, because the fractions are a measurement
against each destination's current interface rather than a value this project may
invent. The shipped profile is now measured, but an unmeasured one stays a legal
registry state that any future profile can be in. A boot check that called the
resolver would refuse to start the server over such a gap, and it would refuse it
for transcription, which does not render.

The two failures are different in both directions that matter. A dangling name
is a typo: it is fixed by editing a row, it fails identically forever, and
nothing downstream can recover from it -- so it is refused here. An unmeasured
safe area is a recorded, intended state that the render path already refuses by
name, at the point where a frame is actually needed -- so it is left alone here.
Escalating it would take the transcription pipeline down for a gap in a
capability the operator may not be using yet.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.errors import RenderProfileInvalid
from onevoicecut.domain.rendering import OutputSpec, RenderProfile, SafeArea
from onevoicecut.runtime import settings as settings_module
from onevoicecut.runtime.settings import Settings, check_target_profiles
from onevoicecut.usecases.generate_artifacts import SCRIPT_TARGETS, ScriptTarget
from onevoicecut.usecases.render_profiles import RENDER_PROFILES

MEASURED = RenderProfile(
    name="vertical",
    output=OutputSpec(width=1080, height=1920),
    safe_area=SafeArea(top=0.06, bottom=0.18, left=0.05, right=0.14),
    max_duration_s=90.0,
)
# A state the registry must still be able to hold: recorded, and not yet
# measured. The shipped `vertical` profile is measured now; this fixture stands
# in for the next profile somebody adds before sitting down with the app.
UNMEASURED = RenderProfile(
    name="vertical",
    output=OutputSpec(width=1080, height=1920),
    safe_area=None,
    max_duration_s=90.0,
)


def _target(name: str, profile: str) -> ScriptTarget:
    return ScriptTarget(
        name=name, format="plain", duration_target_s=45.0, profile=profile
    )


def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.setenv("ONEVOICECUT_DATA_DIR", str(tmp_path))
    return Settings()  # type: ignore[call-arg]


class TestTheCheckItself:
    def test_a_named_profile_that_exists_passes(self) -> None:
        check_target_profiles(
            {"tiktok": _target("tiktok", "vertical")}, {"vertical": MEASURED}
        )

    def test_two_targets_may_name_the_same_profile(self) -> None:
        """The case render dedup exists for. A check that insisted on a profile
        per target would forbid exactly the sharing this change is built on."""
        check_target_profiles(
            {
                "tiktok": _target("tiktok", "vertical"),
                "instagram": _target("instagram", "vertical"),
            },
            {"vertical": MEASURED},
        )

    def test_a_dangling_name_is_refused(self) -> None:
        with pytest.raises(RenderProfileInvalid):
            check_target_profiles(
                {"tiktok": _target("tiktok", "vertcal")}, {"vertical": MEASURED}
            )

    def test_the_refusal_names_the_target_the_profile_and_what_exists(self) -> None:
        """Three facts, because an operator reading a boot failure has none of
        them: which row is wrong, what it asked for, and what it could have
        asked for."""
        with pytest.raises(RenderProfileInvalid) as refusal:
            check_target_profiles(
                {"tiktok": _target("tiktok", "vertcal")}, {"vertical": MEASURED}
            )

        message = str(refusal.value)
        assert "tiktok" in message
        assert "vertcal" in message
        assert "vertical" in message

    def test_every_dangling_row_is_named_not_only_the_first(self) -> None:
        """Fixing one and rebooting to be told about the next is a boot loop an
        operator walks through by hand."""
        with pytest.raises(RenderProfileInvalid) as refusal:
            check_target_profiles(
                {
                    "tiktok": _target("tiktok", "vertcal"),
                    "youtube": _target("youtube", "square"),
                },
                {"vertical": MEASURED},
            )

        assert "tiktok" in str(refusal.value)
        assert "youtube" in str(refusal.value)

    def test_an_unmeasured_profile_is_not_a_dangling_one(self) -> None:
        """The trap this check is built around. `resolve_render_profiles`
        refuses an unmeasured profile, and an unmeasured profile stays a legal
        registry state (the shipped one is measured now; the next addition need
        not be) -- so a boot check that asked for renderability would refuse to
        start a server whose transcription half renders nothing.

        The name resolves to a row. Whether that row is ready to render is a
        question asked where a frame is needed, by the code that owns the answer.
        """
        check_target_profiles(
            {"tiktok": _target("tiktok", "vertical")}, {"vertical": UNMEASURED}
        )


class TestTheShippedRegistries:
    def test_they_agree(self) -> None:
        """The assertion that fails the day a row is added with a typo, whether
        or not the operator has selected that destination."""
        check_target_profiles(SCRIPT_TARGETS, RENDER_PROFILES)

    def test_a_row_nobody_selected_is_checked_too(self) -> None:
        """The check reads the registry, not `settings.script_targets`. A
        destination sitting unselected in the configuration is one an operator
        will select eventually, and the typo would surface then -- after the
        transcription hours are already spent."""
        assert set(SCRIPT_TARGETS) - set(
            settings_module.Settings.model_fields["script_targets"].default.split(",")
        ) == set()


class TestItRunsAtComposition:
    def test_settings_construct_with_the_shipped_registries(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        assert _settings(monkeypatch, tmp_path).script_targets

    def test_a_dangling_row_stops_the_boot(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Before a job runs, not at render time. A profile name that only
        failed once ffmpeg was reached would fail after the transcription hours
        were already spent."""
        monkeypatch.setattr(
            settings_module, "SCRIPT_TARGETS", {"tiktok": _target("tiktok", "vertcal")}
        )

        with pytest.raises(RenderProfileInvalid):
            _settings(monkeypatch, tmp_path)

    def test_an_unmeasured_registry_still_boots(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """An unmeasured registry is still a legal one, so this is the test that
        would fail if the check were ever rewritten to call the resolver."""
        monkeypatch.setattr(
            settings_module, "RENDER_PROFILES", {"vertical": UNMEASURED}
        )

        assert _settings(monkeypatch, tmp_path).script_targets
