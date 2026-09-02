"""A worker's exit code is evidence nobody was reading.

`_popen` launched and walked away, so the one fact only the parent can observe —
that the child is gone, and how — was thrown on the floor. The visible cost, hit
while wiring the local engine: an unusable engine makes the worker print its
reason to the server's stderr and exit 3, and the operator sees a job sitting in
QUEUED forever with no explanation anywhere they can reach.

The second cost is worse and was hit in the same session. A worker that dies
after claiming its job leaves the record in a worker-bound state with a dead pid,
and `reconcile_interrupted_jobs` only runs at startup — so that job is stranded
until somebody restarts the server. The watchdog does not cover it either: it
requires a *live* pid, because its question is whether a running worker is still
moving.

So reaping classifies by what the record says, not by the exit code:

- Still QUEUED — the worker never claimed it, so nothing else will ever write
  this record. FAILED, naming the exit code, because the operator needs a reason.
- Worker-bound — it claimed the job and died mid-flight. INTERRUPTED, which is
  the resumable off-ramp: every committed chunk is still on disk. Identical to
  what reconcile decides at boot, reached continuously instead of once.
- Terminal — the worker already wrote its own outcome. Nothing to add.
"""

import asyncio
from pathlib import Path

import pytest

from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.runtime import app as app_module
from onevoicecut.runtime.app import DrainConfig, drain_supervisor, spawn_worker
from onevoicecut.runtime.supervisor import reap_exited_workers
from onevoicecut.runtime.worker import EXIT_UNUSABLE
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

JOB = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA1")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")
REAPED_AT = 1_700_000_000.0


def a_job(state: JobState, *, pid: int | None = None) -> JobRecord:
    return JobRecord(
        job_id=JOB,
        media_id=MEDIA_ID,
        state=state,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=1.0,
        worker_pid=pid,
        error=None,
        owner=OWNER,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


def _reap(
    storage: FakeTranscriptStoragePort, code: int = EXIT_UNUSABLE
) -> tuple[JobId, ...]:
    return reap_exited_workers(
        storage, exited=((JOB, code),), now=lambda: REAPED_AT
    )


def test_a_worker_that_never_claimed_its_job_fails_it(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The case that motivated this: exit 3, no engine, job stuck in QUEUED.

    Nothing else will ever write this record — the worker is gone and never took
    ownership — so leaving it queued means an operator watching a job that will
    not move and cannot be told why.
    """
    storage.create_job(a_job(JobState.QUEUED))

    assert _reap(storage) == (JOB,)

    job = storage.load_job(JOB)
    assert job.state is JobState.FAILED
    assert job.error is not None
    assert str(EXIT_UNUSABLE) in job.error


def test_the_reason_reaches_the_operator_not_only_the_server_log(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The worker's own message goes to the server's stderr, where an operator
    using a browser will never see it. The record is the surface they have."""
    storage.create_job(a_job(JobState.QUEUED))

    _reap(storage)

    error = storage.load_job(JOB).error or ""
    assert "worker" in error.lower()


@pytest.mark.parametrize(
    "state",
    [
        JobState.EXTRACTING,
        JobState.PLANNED,
        JobState.TRANSCRIBING,
        JobState.STITCHING,
        JobState.GENERATING,
    ],
)
def test_a_worker_that_died_mid_job_leaves_it_resumable(
    storage: FakeTranscriptStoragePort, state: JobState
) -> None:
    """INTERRUPTED, not FAILED: nothing is wrong with the work.

    Every committed chunk is still on disk and a resume continues from the first
    one that is not. This is what reconcile decides at boot; reaping reaches it
    continuously, so the job is not stranded until the next restart.
    """
    storage.create_job(a_job(state, pid=4812))

    assert _reap(storage, code=1) == (JOB,)
    assert storage.load_job(JOB).state is JobState.INTERRUPTED


@pytest.mark.parametrize(
    "state", [JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED]
)
def test_a_worker_that_wrote_its_own_outcome_is_left_alone(
    storage: FakeTranscriptStoragePort, state: JobState
) -> None:
    """A cancelled job exits 2 and a failed one exits 1, both after writing the
    record themselves. Overwriting that would replace the worker's account of
    what happened with the parent's guess from an exit code."""
    storage.create_job(a_job(state, pid=4812))

    assert _reap(storage, code=1) == ()
    assert storage.load_job(JOB).state is state


def test_a_clean_exit_writes_nothing(storage: FakeTranscriptStoragePort) -> None:
    """Exit 0 means the worker finished and recorded it. The record is already
    COMPLETED; there is nothing for the parent to conclude."""
    storage.create_job(a_job(JobState.COMPLETED, pid=4812))
    storage.calls.clear()

    assert reap_exited_workers(storage, exited=((JOB, 0),), now=lambda: REAPED_AT) == ()
    assert not [call for call in storage.calls if "update_job" in str(call)]


def test_a_job_still_awaiting_its_upload_is_left_alone(
    storage: FakeTranscriptStoragePort,
) -> None:
    """PENDING has no worker and no media yet. Nothing spawned for it, so an exit
    attributed to it is a bug in the caller, not a job to fail."""
    storage.create_job(a_job(JobState.PENDING))

    assert _reap(storage) == ()
    assert storage.load_job(JOB).state is JobState.PENDING


def test_an_unknown_job_does_not_stop_the_sweep(
    storage: FakeTranscriptStoragePort,
) -> None:
    """A record deleted under the parent must not take the reaping of every other
    exited worker down with it — the same reason the sweeps above survive a bad
    record."""
    storage.create_job(a_job(JobState.QUEUED))
    missing = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA2")

    reaped = reap_exited_workers(
        storage, exited=((missing, 3), (JOB, 3)), now=lambda: REAPED_AT
    )

    assert reaped == (JOB,)


class StubHandle:
    def __init__(self, status: int | None) -> None:
        self.status = status

    def poll(self) -> int | None:
        return self.status


class TestTheProcessRegistry:
    def test_a_running_worker_is_not_reported(self, tmp_path: Path) -> None:
        handle = StubHandle(None)
        workers = spawn_worker(tmp_path, launch=lambda argv: handle)

        workers(JOB)

        assert workers.finished() == ()

    def test_an_exited_worker_is_reported_with_its_status(
        self, tmp_path: Path
    ) -> None:
        handle = StubHandle(None)
        workers = spawn_worker(tmp_path, launch=lambda argv: handle)
        workers(JOB)

        handle.status = EXIT_UNUSABLE

        assert workers.finished() == ((JOB, EXIT_UNUSABLE),)

    def test_it_is_reported_once_and_then_forgotten(self, tmp_path: Path) -> None:
        """A second report would re-decide a record the first one settled — and
        after a resume that record belongs to a different process entirely."""
        workers = spawn_worker(tmp_path, launch=lambda argv: StubHandle(1))
        workers(JOB)

        assert workers.finished() == ((JOB, 1),)
        assert workers.finished() == ()

    def test_a_launcher_that_returns_no_handle_tracks_nothing(
        self, tmp_path: Path
    ) -> None:
        """Tests that only record argv hand back `None`. Storing that and calling
        `poll()` on it later would break every one of them."""
        launched: list[list[str]] = []
        workers = spawn_worker(tmp_path, launch=launched.append)

        workers(JOB)

        assert launched, "the argv was still built and passed on"
        assert workers.finished() == ()


class TestTheDrainSweep:
    async def test_it_reaps_before_it_drains(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """Serving the queue on top of a stale record would show the operator a
        job that is going nowhere — the same argument that puts reconcile before
        the first sweep."""
        storage.create_job(a_job(JobState.QUEUED))
        order: list[str] = []
        swept = asyncio.Event()

        def reap() -> tuple[tuple[JobId, int], ...]:
            order.append("reap")
            return ((JOB, EXIT_UNUSABLE),)

        def launch(job_id: JobId) -> None:
            order.append("drain")

        async def sleep(_: float) -> None:
            swept.set()
            await asyncio.sleep(3600)

        supervisor = asyncio.create_task(
            drain_supervisor(
                storage,
                max_concurrent_jobs=1,
                launch=launch,
                reap=reap,
                sleep=sleep,
            )
        )
        await asyncio.wait_for(swept.wait(), timeout=5.0)
        supervisor.cancel()

        assert order[0] == "reap"
        # Reaped first, so the drain no longer sees it as queued work.
        assert "drain" not in order
        assert storage.load_job(JOB).state is JobState.FAILED

    def test_the_composition_root_reports_the_workers_it_started(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The registry that spawns and the reaper that reads must be the same
        object, or the parent watches a set of processes it never started."""
        captured: dict[str, object] = {}

        def spy(deps: object, **kwargs: object) -> object:
            captured.update(kwargs)
            return object()

        monkeypatch.setenv("ONEVOICECUT_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("ONEVOICECUT_OPERATOR_TOKENS", "maria:t0ken")
        monkeypatch.setattr(app_module, "require_binaries", lambda: None)
        monkeypatch.setattr(app_module, "build_app", spy)

        app_module.get_app()

        drain = captured["drain"]
        assert isinstance(drain, DrainConfig)
        assert drain.reap.__self__ is drain.launch  # type: ignore[attr-defined]
