"""The watchdog only enforces anything if something calls it.

Slice 7b-i built the sweep and stopped at its rollback boundary, so it shipped as
a seam nothing ran — a per-chunk timeout that could not fire is the same as no
per-chunk timeout at all, except that the design document claims otherwise. This
file pins the wiring that makes it real.

It runs as a second supervised task beside the drain rather than inside it. They
answer different questions on different clocks: the drain asks "is there a free
slot" every five seconds, and the watchdog asks "has this chunk stopped moving"
on the scale of the per-chunk timeout. Folding the watchdog into the drain sweep
would tie a thirty-minute judgement to a five-second cadence, and a drain sweep
that raised would take the timeout down with it.
"""

import asyncio
import time
from pathlib import Path

import pytest

from onevoicecut.adapters.web.app import WebDependencies
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.runtime import app as app_module
from onevoicecut.runtime.app import WatchdogConfig, build_app
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import fake_authenticate

MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")
JOB = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA1")


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    """A worker that is alive and stuck — the only case the watchdog owns.

    The heartbeat is written at the current time on purpose. Startup reconcile
    runs first in the lifespan and claims every worker-bound record with no live
    worker behind it, so a job with no heartbeat is INTERRUPTED before the first
    sweep and the watchdog correctly ignores it. The two divide the work by the
    question they ask: reconcile asks whether a worker exists, the watchdog asks
    whether an existing one is still moving. Only a heartbeat that is fresh
    against the two-hour liveness bound and stale against the per-chunk timeout
    reaches this sweep at all.
    """
    store = FakeTranscriptStoragePort(tmp_path)
    store.write_heartbeat(JOB, at_s=time.time())
    store.create_job(
        JobRecord(
            job_id=JOB,
            media_id=MEDIA_ID,
            state=JobState.TRANSCRIBING,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=1.0,
            updated_at=1.0,
            worker_pid=4812,
            error=None,
            owner=OWNER,
        )
    )
    return store


@pytest.fixture
def no_real_binaries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_module, "require_binaries", lambda: None)


@pytest.fixture
def no_startup_reconcile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reconcile probes the real OS for pid 4812, whose existence is not ours to
    decide. Its own behaviour is pinned elsewhere; this file is about the sweep."""
    monkeypatch.setattr(
        app_module, "reconcile_interrupted_jobs", lambda storage, **kwargs: ()
    )


def _app(
    storage: FakeTranscriptStoragePort, *, watchdog: WatchdogConfig | None
) -> object:
    return build_app(
        WebDependencies(storage=storage, authenticate=fake_authenticate),
        watchdog=watchdog,
    )


def _config(killed: list[int], *, interval_s: float = 0.001) -> WatchdogConfig:
    """A live pid and a heartbeat old enough to be stale by any clock."""
    return WatchdogConfig(
        chunk_timeout_s=0.0001,
        interval_s=interval_s,
        kill=killed.append,
        is_alive=lambda pid: True,
    )


async def test_it_sweeps_for_the_life_of_the_app(
    storage: FakeTranscriptStoragePort,
    no_real_binaries: None,
    no_startup_reconcile: None,
) -> None:
    """On a timer, not on a request. A stalled chunk is discovered at three in
    the morning, when nobody is holding the page open."""
    killed: list[int] = []
    swept = asyncio.Event()

    def kill(pid: int) -> None:
        killed.append(pid)
        swept.set()

    app = _app(
        storage,
        watchdog=WatchdogConfig(
            chunk_timeout_s=0.0001,
            interval_s=0.001,
            kill=kill,
            is_alive=lambda pid: True,
        ),
    )

    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        await asyncio.wait_for(swept.wait(), timeout=5.0)

    assert killed == [4812]
    assert storage.load_job(JOB).state is JobState.INTERRUPTED


async def test_it_stops_when_the_app_does(
    storage: FakeTranscriptStoragePort,
    no_real_binaries: None,
    no_startup_reconcile: None,
) -> None:
    """A task outliving its app would go on killing workers on behalf of a
    process that is finished with that data directory."""
    killed: list[int] = []
    app = _app(storage, watchdog=_config(killed))

    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        await asyncio.sleep(0.05)
    after_shutdown = len(killed)
    await asyncio.sleep(0.05)

    assert killed, "the watchdog really was running"
    assert len(killed) == after_shutdown


async def test_an_app_built_without_one_starts_nothing(
    storage: FakeTranscriptStoragePort,
    no_real_binaries: None,
    no_startup_reconcile: None,
) -> None:
    """Route tests build apps that must not kill anything in the background, and
    opting out is explicit rather than the shape a forgotten argument takes."""
    app = _app(storage, watchdog=None)

    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        await asyncio.sleep(0.02)

    assert storage.load_job(JOB).state is JobState.TRANSCRIBING


async def test_a_failing_sweep_does_not_end_the_loop(
    storage: FakeTranscriptStoragePort,
    no_real_binaries: None,
    no_startup_reconcile: None,
) -> None:
    """One unreadable record must not retire the per-chunk timeout for the whole
    machine. The drain already survives a bad sweep for the same reason, and a
    watchdog that dies quietly is worse than one that never existed — the design
    document still promises the timeout."""
    attempts: list[int] = []
    twice = asyncio.Event()

    def explode(pid: int) -> None:
        attempts.append(pid)
        if len(attempts) >= 2:
            twice.set()
        raise RuntimeError("unreadable record")

    app = _app(
        storage,
        watchdog=WatchdogConfig(
            chunk_timeout_s=0.0001,
            interval_s=0.001,
            kill=explode,
            is_alive=lambda pid: True,
        ),
    )

    async with app.router.lifespan_context(app):  # type: ignore[attr-defined]
        # Waited on rather than polled: a wall-clock budget makes this test fail
        # under a loaded suite for reasons that have nothing to do with the loop.
        await asyncio.wait_for(twice.wait(), timeout=5.0)

    assert len(attempts) >= 2, "the loop stopped at the first failing sweep"


class TestTheCompositionRoot:
    def test_it_supplies_a_watchdog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The seam is only closed if the real entrypoint uses it. A default of
        `None` is right for tests and wrong for the server."""
        captured: dict[str, object] = {}

        def spy(deps: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return object()

        monkeypatch.setenv("ONEVOICECUT_DATA_DIR", ".")
        monkeypatch.setenv("ONEVOICECUT_OPERATOR_TOKENS", "maria:t0ken")
        monkeypatch.setattr(app_module, "build_app", spy)

        app_module.get_app()

        assert isinstance(captured["watchdog"], WatchdogConfig)

    def test_the_timeout_comes_from_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`ONEVOICECUT_CHUNK_TIMEOUT_SECONDS` reaching the sweep is the whole
        point of it being a setting rather than a constant."""
        captured: dict[str, object] = {}

        def spy(deps: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return object()

        monkeypatch.setenv("ONEVOICECUT_DATA_DIR", ".")
        monkeypatch.setenv("ONEVOICECUT_OPERATOR_TOKENS", "maria:t0ken")
        monkeypatch.setenv("ONEVOICECUT_CHUNK_TIMEOUT_SECONDS", "742")
        monkeypatch.setattr(app_module, "build_app", spy)

        app_module.get_app()

        watchdog = captured["watchdog"]
        assert isinstance(watchdog, WatchdogConfig)
        assert watchdog.chunk_timeout_s == 742.0


class TestTheMovedLiveness:
    def test_liveness_lives_with_the_supervision_it_serves(self) -> None:
        """Moved out of `app.py` because the wiring forced the question.

        `supervisor.py` needed the probe and `app.py` needed the sweep, which is
        an import cycle; the direction that resolves it is the honest one, since
        `app.py` is a FastAPI factory that happened to hold process-supervision
        helpers. It is re-exported, so every existing caller is undisturbed.
        """
        from onevoicecut.runtime import supervisor

        assert supervisor.process_is_alive is app_module.process_is_alive
        assert supervisor.worker_is_alive is app_module.worker_is_alive
        assert (
            supervisor.HEARTBEAT_STALE_AFTER_S == app_module.HEARTBEAT_STALE_AFTER_S
        )
