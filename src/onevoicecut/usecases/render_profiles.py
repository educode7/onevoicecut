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
from onevoicecut.domain.generation import ScriptVariant
from onevoicecut.domain.rendering import OutputSpec, RenderProfile
from onevoicecut.usecases.generate_artifacts import ScriptTarget

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


def group_variants_by_profile(
    variants: tuple[ScriptVariant, ...],
    *,
    script_targets: Mapping[str, ScriptTarget],
    render_profiles: Mapping[str, RenderProfile] = RENDER_PROFILES,
) -> tuple[tuple[RenderProfile, tuple[ScriptVariant, ...]], ...]:
    """A candidate's variants, grouped by the distinct profile they resolve to.

    Order is first-seen among the variants, the same rule
    `resolve_render_profiles` already applies to an operator's comma list --
    reused here rather than re-implemented, so one unmeasured profile still
    refuses the whole candidate rather than rendering the rest and silently
    dropping it.

    **Lives here, not on the render worker, because it is pure logic over two
    registries and no port.** It joins `script_targets` (which network maps to
    which profile) against `render_profiles` (what that profile means) with no
    dependency on ffmpeg, a tracker, or storage -- the render worker's
    composition-root concerns. The join is also not the render worker's alone:
    `13b-iv`'s HTTP route needs the identical grouping to write one `PENDING`
    `ClipExport` per distinct profile and report those profiles in its `202`,
    before any worker process exists to run. An adapter importing from
    `runtime/`, the composition root, would be backwards -- so the function
    belongs where both callers can reach it without either importing the
    other.
    """
    order: list[str] = []
    by_profile_name: dict[str, list[ScriptVariant]] = {}
    for variant in variants:
        target = script_targets.get(variant.target)
        if target is None:
            raise RenderProfileInvalid(
                f"script variant names network {variant.target!r}, which no "
                f"configured script target maps to a render profile"
            )
        if target.profile not in by_profile_name:
            by_profile_name[target.profile] = []
            order.append(target.profile)
        by_profile_name[target.profile].append(variant)

    profiles = resolve_render_profiles(",".join(order), registry=render_profiles)
    return tuple(
        (profile, tuple(by_profile_name[profile.name])) for profile in profiles
    )
