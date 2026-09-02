"""The loop that keeps sweeping, and refuses to die quietly.

`drain_once` decides what to start. This decides *when* — every five seconds,
for as long as the web process lives. The interval is what makes the queue drain
without anybody sending a request: slots free when workers finish, and a job
finishing at 2 a.m. is the ordinary case for multi-hour work, not an edge one.
Sweeping only on request would leave that slot cold until morning.

The error handling is the part worth arguing about. A sweep that raises must not
end the loop, because a dead supervisor strands every queued job on the machine
while the web process keeps answering 204 to new uploads — the failure that looks
like success. The queue on disk is the truth, and the next sweep retries against
it.
"""

import asyncio
from pathlib import Path

import pytest

from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import (
    EngineChoice,
    JobRecord,
    JobState,
    SpeakerMode,
)
from onevoicecut.runtime.app import DRAIN_SWEEP_INTERVAL_S, drain_supervisor
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")
OLDEST = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA1")
MIDDLE = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA2")
NEWEST = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA3")


def always_alive(pid: int) -> bool:
    return True


class StopAfter:
    """A clock that ends the loop the way a real shutdown does.

    Injected rather than sleeping for real: a test that waited on wall-clock
    intervals would be slow when it passed and flaky when it did not.
    """

    def __init__(self, sweeps: int) -> None:
        self._limit = sweeps
        self.slept: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.slept.append(delay)
        if len(self.slept) >= self._limit:
            raise asyncio.CancelledError


def a_queued_job(job_id: JobId) -> JobRecord:
    return JobRecord(
        job_id=job_id,
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
    return FakeTranscriptStoragePort(tmp_path)


async def run_supervisor(
    storage: FakeTranscriptStoragePort,
    launched: list[JobId],
    *,
    sweeps: int,
    cap: int = 1,
    interval_s: float = DRAIN_SWEEP_INTERVAL_S,
) -> StopAfter:
    clock = StopAfter(sweeps)
    with pytest.raises(asyncio.CancelledError):
        await drain_supervisor(
            storage,
            max_concurrent_jobs=cap,
            launch=launched.append,
            is_alive=always_alive,
            interval_s=interval_s,
            sleep=clock,
        )
    return clock


async def test_the_default_interval_is_five_seconds(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Up to five seconds between an upload finishing and its worker starting.

    Against a three-hour job that is noise, and QUEUED is an honest thing to
    show on the shared board in the meantime.
    """
    clock = await run_supervisor(storage, [], sweeps=2)

    assert DRAIN_SWEEP_INTERVAL_S == 5.0
    assert clock.slept == [5.0, 5.0]


async def test_it_keeps_sweeping_rather_than_draining_once(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Three jobs, one slot, three sweeps — and the queue moves each time.

    Nothing claims a pid in this test, so each sweep sees zero derived active
    workers and starts the next queued job. That the second sweep does not
    re-start the first is the spawned set surviving across sweeps within one
    supervisor, which is exactly what it is for.
    """
    for job_id in (OLDEST, MIDDLE, NEWEST):
        storage.create_job(a_queued_job(job_id))
    launched: list[JobId] = []

    await run_supervisor(storage, launched, sweeps=3, cap=1)

    assert launched == [OLDEST, MIDDLE, NEWEST]


async def test_a_failing_sweep_does_not_end_the_loop(
    storage: FakeTranscriptStoragePort, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole reason this is wrapped.

    A supervisor that died on one bad record would strand every queued job on
    the machine, while uploads kept answering 204 — and nothing in the system
    would say so.
    """
    storage.create_job(a_queued_job(OLDEST))
    calls: list[int] = []
    real_list = storage.list_jobs

    def fails_once() -> tuple[JobRecord, ...]:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("the data directory went away for a moment")
        return real_list()

    storage.list_jobs = fails_once  # type: ignore[method-assign]
    launched: list[JobId] = []

    clock = await run_supervisor(storage, launched, sweeps=2)

    assert len(calls) == 2, "the loop swept again after the failure"
    assert launched == [OLDEST]
    assert clock.slept == [5.0, 5.0]


async def test_a_failing_sweep_is_reported_rather_than_swallowed(
    storage: FakeTranscriptStoragePort, capsys: pytest.CaptureFixture[str]
) -> None:
    """Continuing quietly would be its own silent failure. It goes to stderr,
    where the process's output already goes."""

    def always_fails() -> tuple[JobRecord, ...]:
        raise OSError("the data directory went away")

    storage.list_jobs = always_fails  # type: ignore[method-assign]

    await run_supervisor(storage, [], sweeps=1)

    assert "the data directory went away" in capsys.readouterr().err


async def test_shutdown_stops_it(storage: FakeTranscriptStoragePort) -> None:
    """Cancellation propagates instead of being caught by the error handling.

    `CancelledError` is not an error in a sweep, it is the shutdown signal —
    swallowing it with everything else would leave a task the event loop cannot
    stop, and the process would hang on exit.
    """
    storage.create_job(a_queued_job(OLDEST))
    task = asyncio.create_task(
        drain_supervisor(
            storage,
            max_concurrent_jobs=1,
            launch=lambda job_id: None,
            is_alive=always_alive,
            interval_s=0.01,
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert task.cancelled()
