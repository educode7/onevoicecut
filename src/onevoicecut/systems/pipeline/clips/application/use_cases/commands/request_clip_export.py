"""Turning `{candidate_index, targets}` into `PENDING` exports, one per profile.

The first production caller of `load_artifacts` and `generate_clip_id`
(`ports/transcript_storage.py`'s `load_artifacts` docstring names this). The
candidate resolution and the networks-to-profiles fan-out both already exist
— `load_artifacts` and
`application.use_cases.queries.render_profiles.group_variants_by_profile`
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
from dataclasses import dataclass

from onevoicecut.shared.domain.errors import (
    ArtifactsNotAvailable,
    ClipCandidateNotFound,
    ClipTargetsInvalid,
)
from onevoicecut.shared.domain.ids import ClipId, JobId
from onevoicecut.systems.pipeline.clips.domain.rendering import ClipExport, ClipState, RenderProfile
from onevoicecut.systems.pipeline.clips.domain.interfaces.clip_store import ClipStore
from onevoicecut.systems.pipeline.clips.application.use_cases.commands.generate_artifacts import SCRIPT_TARGETS, ScriptTarget
from onevoicecut.systems.pipeline.clips.application.use_cases.queries.render_profiles import RENDER_PROFILES, group_variants_by_profile


@dataclass(frozen=True, slots=True)
class RequestClipExportCommand:
    """Which candidate, asked for where — and nothing about where to store it.

    `job_id`, `candidate_index` and `targets` are what the operator typed. The
    registries they are resolved against are deliberately absent: which script
    targets and render profiles exist is a property of the deployment, not of
    this request, so it lives on the handler where it is chosen once.
    """

    job_id: JobId
    candidate_index: int
    targets: tuple[str, ...]


class RequestClipExportHandler:
    """Resolve one candidate, fan it out to its distinct profiles, and persist.

    The four things held here are the ones a second dispatch of the same
    command would hold identically: the storage it writes through, the clip-id
    factory it mints with, and the two registries it resolves names against.
    Splitting them out is what lets a test inject a storage and a factory
    without restating them per request.
    """

    def __init__(
        self,
        *,
        storage: ClipStore,
        new_clip_id: Callable[[], ClipId],
        script_targets: Mapping[str, ScriptTarget] = SCRIPT_TARGETS,
        render_profiles: Mapping[str, RenderProfile] = RENDER_PROFILES,
    ) -> None:
        self._storage = storage
        self._new_clip_id = new_clip_id
        self._script_targets = script_targets
        self._render_profiles = render_profiles

    def handle(
        self, command: RequestClipExportCommand
    ) -> tuple[ClipId, tuple[RenderProfile, ...]]:
        """Resolve one candidate, fan it out to its distinct profiles, and persist.

        Callable's own state check (job must be `COMPLETED`) is the HTTP route's
        job, not this one -- this handler only ever sees a job whose artifacts
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
        storage = self._storage
        new_clip_id = self._new_clip_id
        script_targets = self._script_targets
        render_profiles = self._render_profiles

        job_id = command.job_id
        candidate_index = command.candidate_index
        targets = command.targets

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

        # Dedup, order preserved -- the same tolerance `RenderProfilesHandler.handle`
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
