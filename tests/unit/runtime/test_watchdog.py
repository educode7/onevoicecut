"""The timeout that can actually fire, for the inference that cannot be interrupted.

`TranscriptionRequest.timeout_s` is honoured in-call by adapters that can honour
it. The local one deliberately cannot: once CTranslate2 enters its C++ decode
loop there is no budget Python can enforce from inside, so a chunk that never
returns holds the worker forever and the job sits at 41 of 87 for the rest of the
week. Killing the process from outside is the only enforcement that exists, and
that is what this sweep does.

The signal it reads is the heartbeat, not a timer of its own. The worker writes
one at the top of every chunk iteration, and `TRANSCRIBING` begins only after
extraction and planning have finished — so for a job in that state the age of the
heartbeat *is* the time the current chunk has been running. That is the quantity
the per-chunk timeout is defined over, and no second clock is needed to know it.

Two conditions have to hold together before anything is killed, and the second
one is easy to miss:

- The heartbeat is older than the per-chunk timeout.
- The job has *been* in `TRANSCRIBING` for longer than the per-chunk timeout.

The second exists because the heartbeat the worker wrote at claim time is not
refreshed during extraction. Extracting a three-hour recording outlasts a
thirty-minute chunk timeout comfortably, so without it the first sweep after a
long extraction would kill a job that had just started working.
"""

import time
from pathlib import Path

import pytest

from onevoicecut.domain.chunking import ChunkPlan, ChunkResult, ChunkState, PlannedChunk
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.runtime.supervisor import watchdog_once
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")

CHUNK_TIMEOUT_S = 1800.0
STARTED_AT = 1_700_000_000.0
LIVE_PID = 4812

# Past both conditions: the heartbeat is stale and the job has been transcribing
# longer than one chunk's budget.
STALLED_AT = STARTED_AT + CHUNK_TIMEOUT_S + 1.0


class Killer:
    """Records what it was asked to kill instead of killing it."""

    def __init__(self) -> None:
        self.killed: list[int] = []

    def __call__(self, pid: int) -> None:
        self.killed.append(pid)


def alive(pid: int) -> bool:
    return True


def dead(pid: int) -> bool:
    return False


def a_job(
    *,
    state: JobState = JobState.TRANSCRIBING,
    pid: int | None = LIVE_PID,
    entered_at: float = STARTED_AT,
) -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=state,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=entered_at,
        worker_pid=pid,
        error=None,
        owner=OWNER,
    )


def a_plan(chunks: int = 4) -> ChunkPlan:
    return ChunkPlan(
        job_id=JOB_ID,
        stride_s=600.0,
        overlap_s=5.0,
        chunks=tuple(
            PlannedChunk(index=i, start_s=i * 600.0, end_s=(i + 1) * 600.0 + 5.0)
            for i in range(chunks)
        ),
    )


def a_done_result(index: int) -> ChunkResult:
    return ChunkResult(
        job_id=JOB_ID,
        index=index,
        state=ChunkState.DONE,
        segments=(),
        engine_id="faster-whisper:small",
        attempts=1,
        error=None,
        finished_at=STARTED_AT,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


def a_stalled_job(
    storage: FakeTranscriptStoragePort, *, done: int = 2, chunks: int = 4
) -> None:
    """A worker that claimed chunk `done`, wrote its heartbeat, and never returned."""
    storage.create_job(a_job())
    storage.save_chunk_plan(JOB_ID, a_plan(chunks))
    for index in range(done):
        storage.save_chunk_result(a_done_result(index))
    storage.write_heartbeat(JOB_ID, at_s=STARTED_AT)


def _sweep(
    storage: FakeTranscriptStoragePort,
    *,
    at: float = STALLED_AT,
    kill: Killer | None = None,
    is_alive: object = alive,
) -> tuple[JobId, ...]:
    return watchdog_once(
        storage,
        chunk_timeout_s=CHUNK_TIMEOUT_S,
        now=lambda: at,
        kill=kill or Killer(),
        is_alive=is_alive,  # type: ignore[arg-type]
    )


def test_a_chunk_that_never_returns_gets_its_worker_killed(
    storage: FakeTranscriptStoragePort,
) -> None:
    """7.5: no progress past the per-chunk timeout kills the worker process."""
    a_stalled_job(storage)
    killer = Killer()

    assert _sweep(storage, kill=killer) == (JOB_ID,)
    assert killer.killed == [LIVE_PID]


def test_the_stalled_chunk_is_recorded_failed(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The killed worker cannot write its own epitaph, so the sweep writes it.

    Without this the job resumes and re-attempts the chunk with no record that it
    ever timed out — and a chunk that hangs the engine every time would loop
    forever, each run looking like a fresh start.
    """
    a_stalled_job(storage, done=2, chunks=4)

    _sweep(storage)

    results = {r.index: r for r in storage.load_chunk_results(JOB_ID)}
    # Chunk 2 is the one in flight: 0 and 1 committed, the plan has four.
    assert results[2].state is ChunkState.FAILED
    assert results[2].error is not None
    assert "timeout" in results[2].error.lower()
    # The committed work is untouched — that is what resume is built on.
    assert results[0].state is ChunkState.DONE
    assert results[1].state is ChunkState.DONE


def test_the_job_is_not_terminated_as_a_whole(
    storage: FakeTranscriptStoragePort,
) -> None:
    """`transcription-jobs`: "One chunk times out" — only the chunk fails.

    INTERRUPTED, not FAILED: nothing is wrong with the job. Every committed chunk
    is on disk and a resume picks up from the first one that is not. FAILED would
    say the work is over, which it is not.
    """
    a_stalled_job(storage)

    _sweep(storage)

    assert storage.load_job(JOB_ID).state is JobState.INTERRUPTED


def test_a_job_making_progress_is_left_alone(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The heartbeat moved within the budget, so the chunk is simply slow."""
    a_stalled_job(storage)
    storage.write_heartbeat(JOB_ID, at_s=STALLED_AT - 1.0)
    killer = Killer()

    assert _sweep(storage, kill=killer) == ()
    assert killer.killed == []


def test_a_long_job_within_its_chunk_timeouts_is_never_killed(
    storage: FakeTranscriptStoragePort,
) -> None:
    """`transcription-jobs`: "Long job within chunk timeouts".

    Three hours of elapsed time is not a symptom. The sweep must read the age of
    the current chunk and nothing else, or the multi-hour input this whole
    project is built for becomes the failure case.
    """
    a_stalled_job(storage)
    three_hours_in = STARTED_AT + 3 * 3600.0
    storage.write_heartbeat(JOB_ID, at_s=three_hours_in - 60.0)
    killer = Killer()

    assert _sweep(storage, at=three_hours_in, kill=killer) == ()
    assert killer.killed == []


def test_a_long_extraction_does_not_look_like_a_stall(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The condition that is easy to leave out, and expensive to leave out.

    The worker writes a heartbeat at claim time and then not again until the
    first chunk boundary — extraction sits in between, and on a three-hour
    recording it outlasts the chunk timeout. A job that has only just reached
    TRANSCRIBING therefore carries an already-stale heartbeat through no fault of
    its own, and killing it would make long input unprocessable.
    """
    storage.create_job(a_job(entered_at=STALLED_AT - 1.0))
    storage.save_chunk_plan(JOB_ID, a_plan())
    storage.write_heartbeat(JOB_ID, at_s=STARTED_AT - 3600.0)
    killer = Killer()

    assert _sweep(storage, kill=killer) == ()
    assert killer.killed == []


@pytest.mark.parametrize(
    "state",
    [
        JobState.PENDING,
        JobState.QUEUED,
        JobState.EXTRACTING,
        JobState.PLANNED,
        JobState.STITCHING,
        JobState.GENERATING,
        JobState.COMPLETED,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.INTERRUPTED,
    ],
)
def test_only_a_transcribing_job_is_watched(
    storage: FakeTranscriptStoragePort, state: JobState
) -> None:
    """The per-chunk timeout is defined over chunks, so it applies where there are
    chunks. Extraction and stitching have their own duration and no boundaries to
    measure against; the two-hour heartbeat bound covers a hang in those."""
    a_stalled_job(storage)
    storage.update_job(a_job(state=state))
    killer = Killer()

    assert _sweep(storage, kill=killer) == ()
    assert killer.killed == []


def test_a_worker_that_already_died_is_left_to_reconcile(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Killing a dead pid is how a recycled number gets somebody else's process.

    Startup reconciliation already owns the crashed-worker case and reaches the
    same INTERRUPTED record without signalling anything.
    """
    a_stalled_job(storage)
    killer = Killer()

    assert _sweep(storage, kill=killer, is_alive=dead) == ()
    assert killer.killed == []


def test_a_record_with_no_pid_is_never_signalled(
    storage: FakeTranscriptStoragePort,
) -> None:
    """There is nothing to kill, and `None` is not a pid to guess at."""
    a_stalled_job(storage)
    storage.update_job(a_job(pid=None))
    killer = Killer()

    assert _sweep(storage, kill=killer) == ()
    assert killer.killed == []


def test_a_stalled_job_with_no_plan_is_killed_but_records_no_chunk(
    storage: FakeTranscriptStoragePort,
) -> None:
    """TRANSCRIBING with no plan on disk should be impossible — the worker writes
    the plan before it transitions. If it happens anyway the worker is still
    hung and still has to go; inventing a chunk index to blame would put a
    fabricated result where resume reads its work set."""
    storage.create_job(a_job())
    storage.write_heartbeat(JOB_ID, at_s=STARTED_AT)
    killer = Killer()

    assert _sweep(storage, kill=killer) == (JOB_ID,)
    assert killer.killed == [LIVE_PID]
    assert storage.load_chunk_results(JOB_ID) == ()


def test_the_timeout_comes_from_settings() -> None:
    """Unlike the two-hour liveness bound, this one is an operator's to set: it
    depends on the machine, the model size and the chunk length, and design.md
    lists `ONEVOICECUT_CHUNK_TIMEOUT_SECONDS` for exactly that reason."""
    from onevoicecut.runtime.settings import Settings

    assert "chunk_timeout_s" in Settings.model_fields


def test_the_sweep_survives_one_unkillable_worker(
    storage: FakeTranscriptStoragePort,
) -> None:
    """A sweep runs on a timer for the life of the process. One job whose pid
    cannot be signalled — already reaped, or owned by another user — must not
    stop the sweep from reaching the jobs behind it."""
    a_stalled_job(storage)
    other = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFF")
    storage.create_job(
        JobRecord(
            job_id=other,
            media_id=MEDIA_ID,
            state=JobState.TRANSCRIBING,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=2.0,
            updated_at=STARTED_AT,
            worker_pid=LIVE_PID + 1,
            error=None,
            owner=OWNER,
        )
    )
    storage.write_heartbeat(other, at_s=STARTED_AT)

    def refuses_the_first(pid: int) -> None:
        if pid == LIVE_PID:
            raise ProcessLookupError(pid)

    swept = watchdog_once(
        storage,
        chunk_timeout_s=CHUNK_TIMEOUT_S,
        now=lambda: STALLED_AT,
        kill=refuses_the_first,
        is_alive=alive,
    )

    assert swept == (other,)
    assert storage.load_job(other).state is JobState.INTERRUPTED


def test_the_default_clock_is_real_time() -> None:
    """The sweep is called by a timer in production and by tests with a frozen
    clock. A default that was not the real one would make the production path
    the untested path."""
    import inspect

    assert inspect.signature(watchdog_once).parameters["now"].default is time.time
