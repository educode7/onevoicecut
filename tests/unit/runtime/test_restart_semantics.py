"""What survives a web restart, and why nothing needs to be rebuilt.

Workers are separate processes, so restarting the web process does not stop the
transcription running beside it. The gate has to come back and reach the same
conclusions it had before — and it does, because it never had any state to lose:
the active count is listed off disk and checked against the operating system,
and the queue is a set of records in a directory.

These tests use real filesystem storage and a genuinely live pid. A fake would
prove the arithmetic; only a second `FilesystemTranscriptStorage` over the same
directory proves the conclusions came from disk rather than from an object that
happened to still be in memory.
"""

import os
from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.runtime.app import drain_once, process_is_alive

MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")
RUNNING_JOB = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA1")
QUEUED_FIRST = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA2")
QUEUED_SECOND = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA3")

DEAD_PID = 9999


def a_job(job_id: JobId, state: JobState, *, pid: int | None = None) -> JobRecord:
    return JobRecord(
        job_id=job_id,
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
def before_restart(tmp_path: Path) -> FilesystemTranscriptStorage:
    """One job being transcribed by a process that really exists, two queued."""
    storage = FilesystemTranscriptStorage(tmp_path)
    storage.create_job(a_job(RUNNING_JOB, JobState.TRANSCRIBING, pid=os.getpid()))
    storage.create_job(a_job(QUEUED_FIRST, JobState.QUEUED))
    storage.create_job(a_job(QUEUED_SECOND, JobState.QUEUED))
    return storage


def after_restart(tmp_path: Path) -> FilesystemTranscriptStorage:
    """A second instance over the same directory — the new web process."""
    return FilesystemTranscriptStorage(tmp_path)


def test_a_restarted_gate_still_sees_the_running_worker(
    before_restart: FilesystemTranscriptStorage, tmp_path: Path
) -> None:
    """CAP-05: the slot is occupied, and nothing in memory said so.

    A counter would have died with the old process, and the restarted gate would
    start a second job on a machine already running one — the exact overload the
    cap exists to prevent, appearing only after a restart.
    """
    launched: list[JobId] = []

    drain_once(
        after_restart(tmp_path),
        max_concurrent_jobs=1,
        launch=launched.append,
        spawned=set(),
        is_alive=process_is_alive,
    )

    assert launched == []


def test_queued_jobs_survive_the_restart_and_drain_in_order(
    before_restart: FilesystemTranscriptStorage, tmp_path: Path
) -> None:
    """CAP-08: none lost, none re-admitted, still oldest-first.

    The queue is a directory, so it needs no rebuilding — which is the whole
    reason it is not an in-memory list.
    """
    launched: list[JobId] = []

    drain_once(
        after_restart(tmp_path),
        max_concurrent_jobs=3,
        launch=launched.append,
        spawned=set(),
        is_alive=process_is_alive,
    )

    assert launched == [QUEUED_FIRST, QUEUED_SECOND]


def test_a_slot_held_by_a_worker_that_died_in_the_gap_is_reclaimed(
    tmp_path: Path
) -> None:
    """CAP-06 across a restart: the crash that took the web process took the
    worker too, and the queue must not wait on it."""
    storage = FilesystemTranscriptStorage(tmp_path)
    storage.create_job(a_job(RUNNING_JOB, JobState.TRANSCRIBING, pid=DEAD_PID))
    storage.create_job(a_job(QUEUED_FIRST, JobState.QUEUED))
    launched: list[JobId] = []

    drain_once(
        after_restart(tmp_path),
        max_concurrent_jobs=1,
        launch=launched.append,
        spawned=set(),
        is_alive=lambda pid: False,
    )

    assert launched == [QUEUED_FIRST]


def test_the_spawned_set_starts_empty_and_the_records_are_the_truth(
    tmp_path: Path
) -> None:
    """A worker issued but never claimed is correctly started again.

    Within one web lifetime the spawned set suppresses a re-launch, which is
    right — the worker is probably still starting. Across a restart that memory
    is gone, and the record still reading QUEUED is the honest signal that
    nothing ever claimed it.
    """
    storage = FilesystemTranscriptStorage(tmp_path)
    storage.create_job(a_job(QUEUED_FIRST, JobState.QUEUED))
    launched: list[JobId] = []

    drain_once(
        after_restart(tmp_path),
        max_concurrent_jobs=1,
        launch=launched.append,
        spawned=set(),
        is_alive=process_is_alive,
    )

    assert launched == [QUEUED_FIRST]
