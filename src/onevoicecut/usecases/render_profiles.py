"""The named render profiles, and the three ways naming one can go wrong.

A render profile carries what a destination implies about the file: the output
spec, the caption safe area, the duration ceiling. Resolution is where a
configuration mistake becomes visible, and it has to become visible **before** a
job runs — a dangling profile name that only failed at render time would fail
after the transcription hours were already spent.

**There is no fallback profile, and that is the whole point.** The caption safe
area is the least visible of this system's no-silent-degradation axes: a caption
under a destination's interface overlay is correct in the file, correct in a
local player, and wrong only in the app it was made for. A shared default margin
is exactly how that failure gets introduced — right for the profile it was
measured against, silently wrong for every profile that inherited it.

**The registry ships profile *shapes*, not measured values.** The safe-area
fractions and duration ceilings per destination are measurements against each
app's current interface, and they go stale when that interface changes. The spec
fixes that they must be declared and per profile; it deliberately does not fix
what they are. So a profile nobody has measured records `safe_area=None` and is
refused here by name — populating it is an operator task with a measurement
step, not a coding task.
"""

from collections.abc import Mapping

from onevoicecut.domain.errors import RenderProfileInvalid
from onevoicecut.domain.rendering import OutputSpec, RenderProfile

# Every destination this change delivers to is vertical 9:16, so they share one
# profile and therefore one rendered file. `safe_area=None` is not an oversight:
# nobody has measured where TikTok's interface sits over the frame, and the
# registry has to be able to say that. `max_duration_s` is unmeasured too, and
# unreachable while the safe area gates the profile.
RENDER_PROFILES: Mapping[str, RenderProfile] = {
    "vertical": RenderProfile(
        name="vertical",
        output=OutputSpec(width=1080, height=1920),
        safe_area=None,
        max_duration_s=90.0,
    ),
}


def resolve_render_profiles(
    names: str, *, registry: Mapping[str, RenderProfile] = RENDER_PROFILES
) -> tuple[RenderProfile, ...]:
    """Comma-separated names, the shape `resolve_script_targets` established.

    Order is the caller's, not the registry's: the operator wrote the list. A
    repeated name resolves once, because distinctness is the render dedup made
    structural — two networks naming one profile must share a file, and
    returning it twice would put two byte-identical renders in the job directory
    with nothing to tell them apart.

    One unmeasured profile refuses the **whole** selection. Resolving the two
    that were measured and quietly dropping the third is the silent degradation
    stated backwards: the operator asked for three destinations and would get
    two files with nothing saying why.

    The registry is a parameter so a caller can resolve against a set other than
    the shipped one — a test proving the unmeasured refusal would otherwise have
    to pollute the shipped registry to reach it.
    """
    wanted: list[str] = []
    for name in names.split(","):
        stripped = name.strip()
        if stripped and stripped not in wanted:
            wanted.append(stripped)

    available = ", ".join(sorted(registry))
    if not wanted:
        raise RenderProfileInvalid(
            "no render profiles are configured, so no clip would be rendered; "
            f"available: {available}"
        )

    unknown = sorted({name for name in wanted if name not in registry})
    if unknown:
        raise RenderProfileInvalid(
            f"unknown render profile(s) {unknown}; available: {available}"
        )

    unmeasured = sorted(
        name for name in wanted if registry[name].safe_area is None
    )
    if unmeasured:
        raise RenderProfileInvalid(
            f"render profile(s) {unmeasured} declare no caption safe area, so "
            f"caption placement for them is unknown; a margin is never inherited "
            f"from another profile, so the whole selection is refused until each "
            f"is measured against its destination"
        )

    return tuple(registry[name] for name in wanted)
