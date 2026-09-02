"""Startup order, and the supervisor's lifetime being the app's.

Three things happen before the first request, and the order is load-bearing:
binaries are checked, dead workers are reconciled, and only then does the drain
start sweeping. Reconcile-before-drain is what lets a restart after a crash hand
reclaimed slots to queued work on the very first sweep — the other order would
leave the queue waiting a full interval behind workers that no longer exist.

The supervisor is also the last thing standing between an accepted upload and a
transcript, so this file pins the wiring that starts it. The old guard against
that failure was `no_job_starter`, a refusing default on the upload path; the
upload path no longer starts anything, so the guarantee has to be asserted here
instead.
"""

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI

from onevoicecut.adapters.web.app import WebDependencies
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.runtime import app as app_module
from onevoicecut.runtime.app import DrainConfig, build_app, get_app
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import fake_authenticate

MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")
QUEUED_JOB = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA1")


def a_queued_job() -> JobRecord:
    return JobRecord(
        job_id=QUEUED_JOB,
        media_id=MEDIA_ID,
        state=JobState.QUEUED,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=1.0,
        worker_pid=None,
        error=None,
        owner=OWNER,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    store = FakeTranscriptStoragePort(tmp_path)
    store.create_job(a_queued_job())
    return store


@pytest.fixture
def no_real_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    """ffmpeg's presence is proven elsewhere; this file is about ordering."""
    monkeypatch.setattr(app_module, "require_binaries", lambda: None)


async def test_binaries_then_reconcile_then_the_first_sweep(
    storage: FakeTranscriptStoragePort, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A crashed worker's slot must be reclaimed before the queue is served.

    Sweeping first would derive an active count that still included processes
    which no longer exist, and queued work would wait a whole interval — or
    forever, if the record never got reconciled.
    """
    order: list[str] = []
    swept = asyncio.Event()
    monkeypatch.setattr(
        app_module, "require_binaries", lambda: order.append("binaries")
    )
    real_reconcile = app_module.reconcile_interrupted_jobs

    def spy_reconcile(
        store: FakeTranscriptStoragePort, **kwargs: object
    ) -> tuple[JobId, ...]:
        order.append("reconcile")
        return real_reconcile(store, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(app_module, "reconcile_interrupted_jobs", spy_reconcile)

    def launch(job_id: JobId) -> None:
        order.append("sweep")
        swept.set()

    app = build_app(
        WebDependencies(storage=storage, authenticate=fake_authenticate),
        drain=DrainConfig(launch=launch, max_concurrent_jobs=1, interval_s=0.01),
    )
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(swept.wait(), timeout=5.0)

    assert order[:3] == ["binaries", "reconcile", "sweep"]


async def test_the_supervisor_stops_when_the_app_does(
    storage: FakeTranscriptStoragePort, no_real_binaries: None
) -> None:
    """A task outliving its app would keep spawning workers against a data
    directory the process is done with, and hold the event loop open on exit."""
    sweeps: list[JobId] = []
    app = build_app(
        WebDependencies(storage=storage, authenticate=fake_authenticate),
        drain=DrainConfig(
            launch=sweeps.append, max_concurrent_jobs=99, interval_s=0.001
        ),
    )

    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.02)
    after_shutdown = len(sweeps)
    await asyncio.sleep(0.02)

    assert sweeps, "the supervisor really was running"
    assert len(sweeps) == after_shutdown


async def test_an_app_built_without_a_drain_starts_no_supervisor(
    storage: FakeTranscriptStoragePort, no_real_binaries: None
) -> None:
    """Route tests build apps that must not spawn anything in the background.

    Opting out is explicit — `drain=None` — rather than the shape a forgotten
    argument produces at the composition root, which the test below covers.
    """
    app = build_app(WebDependencies(storage=storage, authenticate=fake_authenticate))

    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.02)

    assert storage.load_job(QUEUED_JOB).state is JobState.QUEUED


class TestTheUploadPathCannotStartAnything:
    def test_web_dependencies_carries_no_launcher(self) -> None:
        """Structural, because an absence cannot be proven by a request.

        Upload used to hold a `start_job` callable, and that field was the
        second spawn decision point. Removing it is what makes "the supervisor
        is the only code that starts work" a property of the type rather than a
        convention someone could re-break in one line.
        """
        fields = set(WebDependencies.__dataclass_fields__)

        assert "start_job" not in fields
        assert not [name for name in fields if "start" in name or "launch" in name]


class TestTheCompositionRootWiresARealDrain:
    def test_get_app_supplies_the_configured_cap_and_a_real_launcher(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, no_real_binaries: None
    ) -> None:
        """The failure this replaces `no_job_starter` to prevent: a server that
        accepts uploads, queues them honestly, and never drains — every response
        a success, every job stuck."""
        monkeypatch.setenv("ONEVOICECUT_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("ONEVOICECUT_OPERATOR_TOKENS", "maria:t-secret")
        monkeypatch.setenv("ONEVOICECUT_MAX_CONCURRENT_JOBS", "4")
        captured: list[DrainConfig | None] = []
        real_build_app = app_module.build_app

        def spy_build_app(
            deps: WebDependencies,
            *,
            drain: DrainConfig | None = None,
            **rest: object,
        ) -> FastAPI:
            # `**rest` so a second supervised task added later — the watchdog
            # was — does not fail this test for a reason unrelated to the drain.
            captured.append(drain)
            return real_build_app(deps, drain=drain, **rest)  # type: ignore[arg-type]

        monkeypatch.setattr(app_module, "build_app", spy_build_app)

        get_app()

        assert len(captured) == 1
        assert captured[0] is not None
        assert captured[0].max_concurrent_jobs == 4
