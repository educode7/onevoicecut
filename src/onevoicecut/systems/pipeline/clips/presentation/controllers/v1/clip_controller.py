"""The clips module's controller: HTTP on one side, commands on the other.

The routes own what is genuinely HTTP — the path, the status codes they return.
This owns the translation in between: a request body into a command, a result
back into a response schema, and the three refusals that are presentation's own
question rather than a domain one:

- a malformed job id answers 404, through `validated_job_id` — the one function
  the jobs controller owns. Shared rather than restated, so there is one rule
  about what a job id looks like and one answer for a malformed id and an
  unknown one, on both halves of the `/api/jobs` prefix;
- a malformed clip id answers 404 as well, for the same reason: no route may
  reveal which clip ids exist;
- a job that is not `COMPLETED` answers 409 here, because which HTTP shape a
  state refusal takes is the route's decision — the handler refuses a job whose
  artifacts are missing (`ArtifactsNotAvailable` → 409) and never sees one in
  the wrong state.

`JobNotOwned` is deliberately not caught. `require_owner` raises it inside this
call, and the composition root maps it once (`main.py`) to a 403 whose detail
never names the owner — the same refusal `JobsController.cancel` translates
itself, so a second copy here would be a second spelling to keep in step
(AUTH-10).
"""

from fastapi import HTTPException

from onevoicecut.shared.application.principal import Principal
from onevoicecut.shared.domain.ids import ClipId, InvalidIdError, make_clip_id
from onevoicecut.systems.pipeline.clips.application.use_cases.commands.request_clip_export import (
    RequestClipExportCommand,
    RequestClipExportHandler,
)
from onevoicecut.systems.pipeline.clips.domain.interfaces.clip_store import ClipStore
from onevoicecut.systems.pipeline.clips.domain.rendering import ClipExport
from onevoicecut.systems.pipeline.clips.presentation.schemas.v1.clip_schemas import (
    ClipExportItem,
    ClipExportListResponse,
    ClipExportRequest,
    ClipExportResponse,
)
from onevoicecut.systems.pipeline.jobs.domain.interfaces.job_store import JobStore
from onevoicecut.systems.pipeline.jobs.domain.jobs import JobRecord, JobState
from onevoicecut.systems.pipeline.jobs.domain.ownership import require_owner
from onevoicecut.systems.pipeline.jobs.presentation.controllers.v1.job_controller import (
    validated_job_id,
)


def validated_clip_id(raw: str) -> ClipId:
    """The second id every clip route names, checked the way `job_id` is.

    Malformed and unknown answer identically (404), so no route reveals which
    clip ids exist — the same reasoning `validated_job_id` states in full, and
    for the same reason the answer is this layer's to give rather than the
    store's.
    """
    try:
        return make_clip_id(raw)
    except InvalidIdError as error:
        raise HTTPException(status_code=404, detail="no such clip") from error


class ClipsController:
    """One instance per app: it holds the clip export handler and the two reads.

    Constructed by `clips_module_api`, never by a route — presentation
    constructs nothing that decides behaviour (AB-09), and a controller built
    per request would rebuild the handler graph on every read.

    The two stores are the narrow interfaces each module owns rather than the
    legacy cross-module port: a job record belongs to `jobs` and an export to
    `clips`, and this is the layer that has to name both because the clip
    operations are scoped to a job.
    """

    def __init__(
        self,
        *,
        request_clip_handler: RequestClipExportHandler,
        job_store: JobStore,
        clip_store: ClipStore,
    ) -> None:
        self._request_clip = request_clip_handler
        self._jobs = job_store
        self._clips = clip_store

    def request_clip(
        self, principal: Principal, job_id: str, body: ClipExportRequest
    ) -> ClipExportResponse:
        """Validate then load, then ownership, then state — in that order.

        The order is the answer, not an implementation detail: a caller with no
        claim on a job must be refused 403 before anything about that job's
        contents (its state, its exports) is allowed to shape the reply.
        """
        job = self._load(job_id)
        require_owner(job, principal.identity)

        if job.state is not JobState.COMPLETED:
            raise HTTPException(
                status_code=409,
                detail=f"job is {job.state}, which has no clip candidates to "
                f"export from",
            )

        clip_id, profiles = self._request_clip.handle(
            RequestClipExportCommand(
                job_id=job.job_id,
                candidate_index=body.candidate_index,
                targets=body.targets,
            )
        )
        return ClipExportResponse(
            clip_id=clip_id, profiles=tuple(profile.name for profile in profiles)
        )

    def clip_status(self, job_id: str, clip_id: str) -> ClipExportListResponse:
        """Every profile's export, never just one — a single-object response
        would have to pick a profile to report and be wrong about the rest.

        Read-only, like a job's status: nothing here has a worker to race
        against, so the principal is not consulted — reading is shared (AUTH).
        """
        exports = self._exports(job_id, clip_id)
        return ClipExportListResponse(
            exports=[ClipExportItem.of(export) for export in exports]
        )

    def clip_profile_status(
        self, job_id: str, clip_id: str, profile: str
    ) -> ClipExportItem:
        """Resolves to exactly one export. A clip id alone never identifies a
        single rendered file, so an unknown profile on a known clip is a
        distinct refusal from an unknown clip — both 404, for different
        reasons a caller can tell apart by the message.
        """
        for export in self._exports(job_id, clip_id):
            if export.profile == profile:
                return ClipExportItem.of(export)
        raise HTTPException(
            status_code=404, detail=f"clip has no {profile!r} export"
        )

    def _load(self, job_id: str) -> JobRecord:
        """Validate then load, in that order, on every route that names a job.

        `JobNotFound` from the store rises to the composition root's table
        (404), the same as a malformed id.
        """
        return self._jobs.load_job(validated_job_id(job_id))

    def _exports(self, job_id: str, clip_id: str) -> tuple[ClipExport, ...]:
        """Both ids checked before anything is read, then the set of exports.

        An absent clip is an empty tuple from the store, which is a refusal
        here (404) rather than an empty list: "no such clip" and "this clip has
        no export for that profile" are different answers and the caller can
        tell them apart by the message.
        """
        job = self._load(job_id)
        exports = self._clips.load_clip_exports(job.job_id, validated_clip_id(clip_id))
        if not exports:
            raise HTTPException(status_code=404, detail="no such clip")
        return exports
