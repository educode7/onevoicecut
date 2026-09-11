"""Turning `{candidate_index, targets}` into `PENDING` exports, one per profile.

The first production caller of `load_artifacts` and `generate_clip_id`
(`ports/transcript_storage.py`'s `load_artifacts` docstring names this). The
candidate resolution and the networks-to-profiles fan-out both already exist
— `load_artifacts` and `usecases.render_profiles.group_variants_by_profile`
— so what this module owns is the seam between them: which operator mistake
gets which refusal, and writing the resulting `PENDING` exports before it
answers.

**A network naming no variant on this candidate is refused here, not left to
`group_variants_by_profile`.** That function validates the variants it is
handed against the render-profile registry, which is a different question
from "did the operator ask for something this candidate was never scripted
for". Narrowing to the requested networks happens first — a mistyped or
ungenerated network would otherwise just vanish from the narrowed set and
never reach that function's own check, the exact silent-drop shape this
system refuses everywhere else.
"""

from collections.abc import Callable, Mapping

from onevoicecut.domain.errors import (
    ArtifactsNotAvailable,
    ClipCandidateNotFound,
    ClipTargetsInvalid,
)
from onevoicecut.domain.ids import ClipId, JobId
from onevoicecut.domain.rendering import ClipExport, ClipState, RenderProfile
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.usecases.generate_artifacts import SCRIPT_TARGETS, ScriptTarget
from onevoicecut.usecases.render_profiles import RENDER_PROFILES, group_variants_by_profile


def request_clip_export(
    job_id: JobId,
    candidate_index: int,
    targets: tuple[str, ...],
    *,
    storage: TranscriptStoragePort,
    new_clip_id: Callable[[], ClipId],
    script_targets: Mapping[str, ScriptTarget] = SCRIPT_TARGETS,
    render_profiles: Mapping[str, RenderProfile] = RENDER_PROFILES,
) -> tuple[ClipId, tuple[RenderProfile, ...]]:
    """Resolve one candidate, fan it out to its distinct profiles, and persist.

    Callable's own state check (job must be `COMPLETED`) is the HTTP route's
    job, not this one -- this function only ever sees a job whose artifacts
    might exist, and answers `ArtifactsNotAvailable` when they do not.

    A repeated network in `targets` is deduplicated, order preserved, the
    same tolerance every other comma-list resolver in this system extends to
    an operator's input. An empty `targets` reaches
    `group_variants_by_profile` with no variants at all and is refused by the
    registry resolver it already delegates to -- "no render profiles are
    configured" is the correct message for "asked for nothing" too, so there
    is no second refusal to invent here.

    Returns the freshly minted clip id and the **profiles** the request
    resolved to -- not the networks asked for -- because two networks
    sharing one profile must be reported as the one file they become.
    """
    artifacts = storage.load_artifacts(job_id)
    if artifacts is None:
        raise ArtifactsNotAvailable(
            f"job {job_id} has generated no clip candidates yet; script "
            f"generation has not produced artifacts to export a clip from"
        )

    candidates = artifacts.clip_candidates
    if not 0 <= candidate_index < len(candidates):
        raise ClipCandidateNotFound(
            f"candidate index {candidate_index} names no clip candidate; "
            f"job {job_id} generated {len(candidates)}"
        )
    candidate = candidates[candidate_index]

    # Dedup, order preserved -- the same tolerance `resolve_render_profiles`
    # and `resolve_script_targets` already extend to a comma-separated list.
    wanted = tuple(dict.fromkeys(targets))
    available = {variant.target for variant in candidate.variants}
    unmatched = sorted(name for name in wanted if name not in available)
    if unmatched:
        raise ClipTargetsInvalid(
            f"target(s) {unmatched} name no script variant on candidate "
            f"{candidate_index}; available: {', '.join(sorted(available))}"
        )

    selected = tuple(variant for variant in candidate.variants if variant.target in wanted)
    grouped = group_variants_by_profile(
        selected, script_targets=script_targets, render_profiles=render_profiles
    )

    clip_id = new_clip_id()
    for profile, variants in grouped:
        storage.save_clip_export(
            ClipExport(
                job_id=job_id,
                clip_id=clip_id,
                profile=profile.name,
                # Present from PENDING onward: a worker resolving this clip id
                # later has a range to render against without ever touching
                # the candidate that produced it, which may have been
                # re-ranked or dropped by then.
                source_start_s=candidate.start_s,
                source_end_s=candidate.end_s,
                title=candidate.hook,
                description=candidate.rationale,
                variants=variants,
                state=ClipState.PENDING,
                clip=None,
                failure=None,
            )
        )

    return clip_id, tuple(profile for profile, _ in grouped)
