"""The web process's composition root.

    PYTHONPATH=src uvicorn onevoicecut.main:get_app --factory

Configuration is read here, the token map is parsed here — the one
`get_secret_value()` call in the system (AUTH-17) — and one `DomainError`
handler maps every domain failure at the edge, so handlers raise domain errors
and the table does the rest. The single error that does not reach the table is
`JobNotOwned`: the jobs controller translates it first, using this module's own
constant, so the generic wording has exactly one home wherever the translation
happens. `runtime.app.get_app` re-exports this factory until the entrypoint
flips in Phase 5.

Every name taken from `runtime.app` is imported *inside* the function that
uses it. `runtime.app` re-exports this module's factory and its drain
configs, so a module-level edge from here to there would close an import
cycle the moment `runtime.app` loaded first — and the wiring tests patch
names on `runtime.app` (notably `build_app`) and expect this code to see the
patch, which call-time import is also what provides.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from onevoicecut.systems.pipeline.transcripts.infrastructure.asr.local.declarations import HF_TOKEN_ENV
from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.adapters.web.app import WebDependencies
from onevoicecut.systems.pipeline.clips.domain.rendering import RenderProfile
from onevoicecut.runtime.engine_resolver import declared_support
from onevoicecut.runtime.supervisor import (
    LivenessProbe,
    kill_worker,
    process_is_alive,
)
from onevoicecut.shared.application.principal import (
    build_authenticator,
    parse_operator_tokens,
)
from onevoicecut.shared.domain.errors import (
    ArtifactsNotAvailable,
    ClipCandidateNotFound,
    DomainError,
    JobAlreadyExists,
    JobNotOwned,
    JobNotFound,
    RenderProfileInvalid,
    UnsupportedContainer,
    UploadTooLarge,
)
from onevoicecut.shared.domain.ids import ClipId, JobId
from onevoicecut.shared.infrastructure.settings import Settings, load_env_file
from onevoicecut.systems.pipeline.jobs.presentation.controllers.v1.job_controller import (
    OWNER_REFUSAL_DETAIL,
)
from onevoicecut.systems.pipeline.clips.application.use_cases.commands.generate_artifacts import SCRIPT_TARGETS, ScriptTarget
from onevoicecut.systems.pipeline.clips.application.use_cases.queries.render_profiles import RENDER_PROFILES

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]] | None

# Five seconds between sweeps. That is the worst-case delay between an upload
# finishing and its worker starting on an idle machine — noise against a
# three-hour job, and QUEUED is an honest status to show meanwhile.
DRAIN_SWEEP_INTERVAL_S = 5.0

# A minute between watchdog sweeps, against a timeout measured in tens of
# minutes. It bounds only how late a kill lands; sweeping at the drain's cadence
# would re-list every job on the machine twelve times a minute to re-ask a
# question whose answer changes on the scale of a chunk.
WATCHDOG_SWEEP_INTERVAL_S = 60.0

# Same cadence as the job drain: a render is minutes rather than hours, so
# there is no argument for sweeping it any less eagerly than the queue it sits
# beside.
RENDER_DRAIN_SWEEP_INTERVAL_S = 5.0

# The design's error-mapping table (design.md, "Error mapping"): status by
# error type, every row matching the route-local translation this replaces.
# A `DomainError` with no row here is validation — 422, the skill default and
# the honest answer for a domain refusal the table has not singled out.
_DOMAIN_ERROR_STATUSES: dict[type[DomainError], int] = {
    JobNotFound: 404,
    ClipCandidateNotFound: 404,
    JobNotOwned: 403,
    JobAlreadyExists: 409,
    ArtifactsNotAvailable: 409,
    UploadTooLarge: 413,
    UnsupportedContainer: 415,
}
_DEFAULT_DOMAIN_ERROR_STATUS = 422

logger = logging.getLogger(__name__)


def _domain_error_status(error: Exception) -> int:
    """Most-derived row first, so a subclass inherits its parent's status
    instead of falling through to 422 — `ChunkTimeout` is a
    `TranscriptionFailed`, and neither should answer by accident of where
    the class hierarchy happened to put them."""
    for cls in type(error).__mro__:
        if cls in _DOMAIN_ERROR_STATUSES:
            return _DOMAIN_ERROR_STATUSES[cls]
    return _DEFAULT_DOMAIN_ERROR_STATUS


def handle_domain_error(request: Request, error: Exception) -> JSONResponse:
    """The one translation from domain error to HTTP, at the composition edge.

    Routes raise `DomainError` and never catch it: the table lives here, so a
    new error gets its status by adding one row rather than by finding every
    route that might produce it. `request` is deliberately unused — the answer
    comes from the error alone, which is what keeps one table true for every
    route at once.
    """
    detail = OWNER_REFUSAL_DETAIL if isinstance(error, JobNotOwned) else str(error)
    return JSONResponse(
        status_code=_domain_error_status(error), content={"detail": detail}
    )


def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    """AUTH-12: the 500 says nothing; the log says where and what — never
    headers (the `Authorization` one especially) and never a body.

    Starlette's server-error middleware re-raises after this response is
    sent, so the server's own log keeps the traceback as well; the explicit
    `logger.error` is what makes the guarantee a property of this handler
    rather than of which server happens to run it.
    """
    logger.error(
        "unhandled exception on %s %s",
        request.method,
        request.url.path,
        exc_info=error,
    )
    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


def create_app(deps: WebDependencies, *, lifespan: Lifespan = None) -> FastAPI:
    """`lifespan` is supplied by the caller, not built here.

    Startup checks — ffmpeg present, stale jobs reconciled — need real
    adapters and a real data directory. A test drives the same routes with
    neither, which is only possible while this stays optional.

    Both handlers are installed on every app this builds, a test's included:
    the mapping is a property of the application, not of whichever factory
    built it, which is what lets routes drop their local translations and
    the existing status-code tests still pass unchanged.

    Neither half of `/api/jobs` is decided here. The five jobs operations arrive
    wired from `jobs_module_api` — the module decides which handlers its
    controller runs against — and the three clip operations that still live in
    the web adapter arrive as their own router; each carries its own `/api/jobs`
    prefix, so the split is invisible on the wire and the route table every
    generated gate reads stays honest about the paths actually served.
    """
    from onevoicecut.adapters.web.routers.jobs import build_clip_router
    from onevoicecut.systems.pipeline.jobs.jobs_module_api import build_jobs_router

    app = FastAPI(
        title="transcribe", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    app.include_router(build_jobs_router(deps))
    app.include_router(build_clip_router(deps))
    app.add_exception_handler(DomainError, handle_domain_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
    return app


@dataclass(frozen=True, slots=True)
class DrainConfig:
    """Everything the supervisor needs, kept off `WebDependencies` on purpose.

    The web adapter no longer knows how to start work, and that is the point of
    the capacity gate — so the launcher must not sit on the object every route
    handler receives. A handler that can reach a launcher is one refactor away
    from calling it.
    """

    launch: Callable[[JobId], None]
    max_concurrent_jobs: int
    is_alive: LivenessProbe = process_is_alive
    interval_s: float = DRAIN_SWEEP_INTERVAL_S
    # Defaults to reporting nothing, so an app built without a process registry
    # sweeps exactly as before rather than reaping workers it never started.
    reap: Callable[[], tuple[tuple[JobId, int], ...]] = field(default=lambda: ())


@dataclass(frozen=True, slots=True)
class RenderDrainConfig:
    """`DrainConfig`'s render-side twin -- no `is_alive`, because a render's
    liveness question is answered by a claim, not a pid probe."""

    launch: Callable[[JobId, ClipId], None]
    max_concurrent_renders: int
    interval_s: float = RENDER_DRAIN_SWEEP_INTERVAL_S
    reap: Callable[[], tuple[tuple[ClipId, int], ...]] = field(default=lambda: ())


@dataclass(frozen=True, slots=True)
class WatchdogConfig:
    """Separate from `DrainConfig` because they are separate decisions.

    One is capacity, the other is enforcement, and their intervals differ by
    three orders of magnitude. Folding them together would tie a thirty-minute
    judgement to a five-second cadence.
    """

    chunk_timeout_s: float
    # A minute between sweeps. The timeout is measured in tens of minutes, so
    # this only bounds how late the kill is, and sweeping harder would re-list
    # every job on the machine for a question whose answer changes slowly.
    interval_s: float = WATCHDOG_SWEEP_INTERVAL_S
    kill: Callable[[int], None] = kill_worker
    is_alive: LivenessProbe = process_is_alive


def check_target_profiles(
    targets: Mapping[str, ScriptTarget], profiles: Mapping[str, RenderProfile]
) -> None:
    """Refuse a script target that names a render profile nobody defined.

    The two registries are edited independently — adding a network is a row in
    one, adding a destination shape is a row in the other — and the only thing
    joining them is a string. `profil="vertcal"` type-checks, imports, and
    transcribes three hours of audio before anybody finds out. So they are read
    against each other while the server boots, which is the last moment before a
    job can start.

    **This asserts membership, not renderability, and the distinction is the
    reason the function exists rather than a call to `RenderProfilesHandler.handle`.**
    That resolver also refuses a profile whose caption safe area nobody has
    measured — on purpose, because the fractions are a measurement against each
    destination's live interface rather than a value this project may invent. The
    shipped profile is now measured, but an unmeasured one remains a legal
    registry state any future profile can be in, and it stays refused at
    resolution rather than at boot. Calling the resolver here would refuse to
    start the server over a destination gap that only rendering needs, and would
    refuse it for transcription, which renders nothing.

    The two failures differ in both directions that matter. A dangling name is a
    typo: fixed by editing a row, identical on every retry, unrecoverable
    downstream — refused here. An unmeasured safe area is a recorded and
    intended state that the render path already refuses **by name**, at the point
    where a frame is genuinely needed. Escalating it to a boot refusal would take
    the transcription pipeline down for a gap in a capability the operator may
    not be using yet, and would make measuring four destinations a precondition
    for starting the server at all.

    Every offending row is named, not the first: fixing one and rebooting to be
    told about the next is a boot loop an operator walks through by hand.
    """
    dangling = sorted(
        f"{target.name} -> {target.profile}"
        for target in targets.values()
        if target.profile not in profiles
    )
    if dangling:
        raise RenderProfileInvalid(
            f"script target(s) {dangling} name a render profile that is not "
            f"defined; available: {', '.join(sorted(profiles))}"
        )


def build_dependencies(settings: Settings) -> WebDependencies:
    # Membership preflight: the inlined default `script_targets` and the
    # registry must agree before anything serves. The model validator that
    # used to enforce this on `Settings` is gone — `shared/infrastructure`
    # must not import the clip use case — so the composition root checks it
    # here, one call later, same exception and message discipline.
    check_target_profiles(SCRIPT_TARGETS, RENDER_PROFILES)
    # Parsing the token map is the composition root's one authentication act.
    # It refuses an empty or malformed map before anything can serve a request —
    # a server must never come up with authentication disabled or ambiguous.
    # AUTH-17: the SecretStr is peeled open here, and only here — no module
    # under shared/ or adapters/ ever holds the plaintext.
    authenticate = build_authenticator(
        parse_operator_tokens(settings.operator_tokens.get_secret_value())
    )
    return WebDependencies(
        storage=FilesystemTranscriptStorage(settings.data_dir),
        authenticate=authenticate,
        max_upload_bytes=settings.max_upload_bytes,
        # Slice 6 built this guard and nothing ever supplied it, so `admit_job`
        # skipped it on the one path an operator uses. An interview-mode job was
        # admitted, queued, given a worker, and refused by the adapter on its
        # first chunk — after ffmpeg had extracted three hours of audio that then
        # went in the bin. Cheap here: neither engine is constructed to answer.
        capabilities=lambda engine: declared_support(
            engine, hf_token=os.environ.get(HF_TOKEN_ENV)
        ),
    )


def build_app(
    deps: WebDependencies,
    *,
    drain: DrainConfig | None = None,
    watchdog: WatchdogConfig | None = None,
    render_drain: RenderDrainConfig | None = None,
) -> FastAPI:
    """`None` for either builds an app that serves routes and starts nothing.

    That is what route tests want, and it is explicit rather than implied by a
    forgotten argument — the composition root always supplies both, and a test
    asserts that it does.
    """
    # Call-time, not module-time: see the module docstring. The wiring tests
    # patch these names on `runtime.app` and expect the lifespan below to see
    # the patch — and their order (binaries, reconcile, sweep) is the very
    # thing those tests pin.
    from onevoicecut.runtime.app import (
        drain_supervisor,
        reconcile_interrupted_jobs,
        render_drain_supervisor,
        require_binaries,
        watchdog_supervisor,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        # These happen before the first request rather than at first use. A
        # missing ffmpeg discovered an hour into a job, or a stale TRANSCRIBING
        # left by yesterday's crash, are both things the operator should learn
        # about at boot.
        require_binaries()
        # Before the drain, always: reconcile turns records left by dead workers
        # into INTERRUPTED, which is what frees their derived slots. Sweeping
        # first would count processes that no longer exist and make queued work
        # wait behind them.
        reconcile_interrupted_jobs(deps.storage, now=deps.now)

        supervisors: list[asyncio.Task[None]] = []
        if drain is not None:
            supervisors.append(
                asyncio.create_task(
                    drain_supervisor(
                        deps.storage,
                        max_concurrent_jobs=drain.max_concurrent_jobs,
                        launch=drain.launch,
                        is_alive=drain.is_alive,
                        interval_s=drain.interval_s,
                        reap=drain.reap,
                    )
                )
            )
        if watchdog is not None:
            supervisors.append(
                asyncio.create_task(
                    watchdog_supervisor(
                        deps.storage,
                        chunk_timeout_s=watchdog.chunk_timeout_s,
                        interval_s=watchdog.interval_s,
                        kill=watchdog.kill,
                        is_alive=watchdog.is_alive,
                    )
                )
            )
        if render_drain is not None:
            supervisors.append(
                asyncio.create_task(
                    render_drain_supervisor(
                        deps.storage,
                        max_concurrent_renders=render_drain.max_concurrent_renders,
                        launch=render_drain.launch,
                        interval_s=render_drain.interval_s,
                        reap=render_drain.reap,
                    )
                )
            )

        try:
            yield
        finally:
            # A task outliving its app would keep spawning workers — or killing
            # them — against a data directory this process is finished with.
            for supervisor in supervisors:
                supervisor.cancel()
            for supervisor in supervisors:
                with suppress(asyncio.CancelledError):
                    await supervisor

    return create_app(deps, lifespan=lifespan)


def get_app() -> FastAPI:
    """Built on call, not at import.

    `uvicorn onevoicecut.runtime.app:get_app --factory` reads the environment when
    it starts the server; a module-level app would read it whenever anything
    imported this module, including a test collecting it.
    """
    # Call-time for every `runtime.app` name: see the module docstring. The
    # wiring tests patch `runtime.app.build_app` and expect this factory to
    # call their spy, so the name has to resolve when the call happens.
    from onevoicecut.runtime.app import build_app, spawn_render_worker, spawn_worker

    # Before `Settings`, and before `build_dependencies` reads the HF token: the
    # operator's gitignored `.env` is one of the places configuration comes from,
    # and loading it here also covers every spawned worker, which inherits this
    # environment.
    load_env_file()
    settings = Settings()  # type: ignore[call-arg]
    workers = spawn_worker(settings.data_dir)
    render_workers = spawn_render_worker(settings.data_dir)
    return build_app(
        build_dependencies(settings),
        drain=DrainConfig(
            launch=workers,
            max_concurrent_jobs=settings.max_concurrent_jobs,
            reap=workers.finished,
        ),
        watchdog=WatchdogConfig(chunk_timeout_s=settings.chunk_timeout_s),
        render_drain=RenderDrainConfig(
            launch=render_workers,
            max_concurrent_renders=settings.max_concurrent_renders,
            reap=render_workers.finished,
        ),
    )
