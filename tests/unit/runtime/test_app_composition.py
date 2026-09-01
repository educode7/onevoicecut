"""The composition root: where configuration is read and the two processes meet.

Everything below this takes what it needs as an argument, which is the only reason
the rest of the system can be tested without an environment. So the things worth
testing here are the ones that exist nowhere else — what the worker is launched
with, and what happens to a job whose worker died.
"""

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.chunking import ChunkPlan, PlannedChunk
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.runtime.app import (
    WORKER_MODULE,
    build_dependencies,
    reconcile_interrupted_jobs,
    spawn_worker,
)
from onevoicecut.runtime.settings import Settings

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
OTHER_JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFF")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
NOW = 2000.0

LIVE_PID = 4812
DEAD_PID = 4813


def a_job(
    job_id: JobId = JOB_ID,
    state: JobState = JobState.TRANSCRIBING,
    worker_pid: int | None = LIVE_PID,
) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        media_id=MEDIA_ID,
        state=state,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=1.0,
        worker_pid=worker_pid,
        error=None,
        owner=None,
    )


def a_plan() -> ChunkPlan:
    return ChunkPlan(
        job_id=JOB_ID,
        stride_s=600.0,
        overlap_s=5.0,
        chunks=(PlannedChunk(index=0, start_s=0.0, end_s=600.0),),
    )


def only_one_pid_is_alive(pid: int) -> bool:
    return pid == LIVE_PID


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemTranscriptStorage:
    return FilesystemTranscriptStorage(tmp_path)


def test_settings_read_the_data_directory_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ONEVOICECUT_DATA_DIR", str(tmp_path))

    assert Settings().data_dir == tmp_path  # type: ignore[call-arg]


def test_a_missing_data_directory_is_refused_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A default would put multi-hour sermons somewhere the operator did not
    choose, and the first they would know of it is a full disk."""
    monkeypatch.delenv("ONEVOICECUT_DATA_DIR", raising=False)

    with pytest.raises(Exception):
        Settings()  # type: ignore[call-arg]


def test_the_app_is_wired_to_the_configured_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ONEVOICECUT_DATA_DIR", str(tmp_path))

    deps = build_dependencies(Settings())  # type: ignore[call-arg]

    assert deps.storage.job_dir(JOB_ID) == tmp_path / "jobs" / JOB_ID


def test_the_worker_is_launched_as_its_own_process(tmp_path: Path) -> None:
    """A process, not a task. It can be killed when a three-hour job goes wrong,
    and while it lives it is the sole writer of that job's record."""
    launched: list[list[str]] = []

    spawn_worker(tmp_path, launch=launched.append)(JOB_ID)

    argv = launched[0]
    assert argv[1:3] == ["-m", WORKER_MODULE]
    assert argv[argv.index("--job-id") + 1] == JOB_ID
    assert argv[argv.index("--data-dir") + 1] == str(tmp_path)


def test_the_worker_module_names_a_module_that_exists() -> None:
    """`WORKER_MODULE` is spawned as `python -m <name>` in a separate process, so a
    stale name fails only at runtime — in the worker, after the upload already
    succeeded, with the web process reporting a job that never starts.

    Asserting argv against the constant proves the two agree; it cannot fail on a
    name that resolves to nothing, because it compares the constant to itself. This
    resolves the name instead.
    """
    assert importlib.util.find_spec(WORKER_MODULE) is not None


def test_the_worker_argv_is_a_list_never_a_command_line(tmp_path: Path) -> None:
    """Same rule as the ffmpeg adapter: nothing parses these tokens as a shell
    command, so nothing in them can become one."""
    launched: list[list[str]] = []

    spawn_worker(tmp_path, launch=launched.append)(JOB_ID)

    assert all(isinstance(token, str) for token in launched[0])
    assert launched[0][0].endswith(("python", "python.exe"))


def test_a_job_whose_worker_died_is_marked_interrupted(
    storage: FilesystemTranscriptStorage,
) -> None:
    """A record saying TRANSCRIBING with nothing behind it is a lie left by a
    crash, and the operator would watch it forever."""
    storage.create_job(a_job(worker_pid=DEAD_PID))

    reconciled = reconcile_interrupted_jobs(
        storage, now=lambda: NOW, is_alive=only_one_pid_is_alive
    )

    assert reconciled == (JOB_ID,)
    assert storage.load_job(JOB_ID).state is JobState.INTERRUPTED


def test_a_job_whose_worker_is_still_running_is_left_alone(
    storage: FilesystemTranscriptStorage,
) -> None:
    """The check that keeps the single-writer rule intact. Workers are separate
    processes and routinely outlive the web app that started them; overwriting
    their record would be the second writer the design exists to prevent."""
    storage.create_job(a_job(worker_pid=LIVE_PID))

    reconciled = reconcile_interrupted_jobs(
        storage, now=lambda: NOW, is_alive=only_one_pid_is_alive
    )

    assert reconciled == ()
    assert storage.load_job(JOB_ID).state is JobState.TRANSCRIBING


def test_a_transcribing_job_with_no_recorded_pid_is_interrupted(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.create_job(a_job(worker_pid=None))

    reconcile_interrupted_jobs(
        storage, now=lambda: NOW, is_alive=only_one_pid_is_alive
    )

    assert storage.load_job(JOB_ID).state is JobState.INTERRUPTED


@pytest.mark.parametrize(
    "state",
    [
        JobState.PENDING,
        JobState.COMPLETED,
        JobState.FAILED,
        JobState.CANCELLED,
        JobState.INTERRUPTED,
    ],
)
def test_jobs_that_are_not_transcribing_are_untouched(
    storage: FilesystemTranscriptStorage, state: JobState
) -> None:
    """Only a running-looking job can be lying. A PENDING one is waiting for an
    upload, and a COMPLETED one is finished."""
    storage.create_job(a_job(state=state, worker_pid=DEAD_PID))

    reconcile_interrupted_jobs(
        storage, now=lambda: NOW, is_alive=only_one_pid_is_alive
    )

    assert storage.load_job(JOB_ID).state is state


def test_reconciliation_touches_only_the_dead_ones(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.create_job(a_job(JOB_ID, worker_pid=DEAD_PID))
    storage.create_job(a_job(OTHER_JOB_ID, worker_pid=LIVE_PID))

    reconcile_interrupted_jobs(
        storage, now=lambda: NOW, is_alive=only_one_pid_is_alive
    )

    assert storage.load_job(JOB_ID).state is JobState.INTERRUPTED
    assert storage.load_job(OTHER_JOB_ID).state is JobState.TRANSCRIBING


def test_an_interrupted_job_keeps_everything_it_finished(
    storage: FilesystemTranscriptStorage,
) -> None:
    """INTERRUPTED rather than FAILED: nothing went wrong with the work. Every
    committed chunk is still there, and re-running resumes from the first one that
    is not."""
    storage.create_job(a_job(worker_pid=DEAD_PID))
    storage.save_chunk_plan(JOB_ID, a_plan())

    reconcile_interrupted_jobs(
        storage, now=lambda: NOW, is_alive=only_one_pid_is_alive
    )

    assert storage.load_chunk_plan(JOB_ID) is not None
    assert storage.load_job(JOB_ID).error is None
