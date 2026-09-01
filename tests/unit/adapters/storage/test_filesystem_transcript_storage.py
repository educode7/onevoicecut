"""The job record lifecycle against real files, not a dict.

The fake has always round-tripped because a dict cannot lose anything. Disk can: a
directory that does not exist, a file that was never written, a job id that is also
a path. These tests exist for the gap between the two, so they use `tmp_path` rather
than a stubbed `open` — a mock would prove nothing about the thing that breaks.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.errors import CorruptedRecord, JobAlreadyExists, JobNotFound
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
OTHER_JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFF")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemTranscriptStorage:
    return FilesystemTranscriptStorage(tmp_path)


def a_job(job_id: JobId = JOB_ID, state: JobState = JobState.PENDING) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        media_id=MEDIA_ID,
        state=state,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1723501234.5,
        updated_at=1723501234.5,
        worker_pid=None,
        error=None,
        owner=None,
    )


def test_a_created_job_loads_back_identical(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.create_job(a_job())

    assert storage.load_job(JOB_ID) == a_job()


def test_a_created_job_lands_in_its_own_directory(
    storage: FilesystemTranscriptStorage, tmp_path: Path
) -> None:
    storage.create_job(a_job())

    assert (tmp_path / "jobs" / JOB_ID / "job.json").is_file()


def test_creating_a_job_that_already_exists_is_refused(
    storage: FilesystemTranscriptStorage,
) -> None:
    """`create` and `update` are separate methods on the port. If create also
    overwrote, a reused id would silently discard a running job's state."""
    storage.create_job(a_job())

    with pytest.raises(JobAlreadyExists):
        storage.create_job(a_job(state=JobState.FAILED))

    assert storage.load_job(JOB_ID).state is JobState.PENDING


def test_loading_an_unknown_job_raises_a_domain_error(
    storage: FilesystemTranscriptStorage,
) -> None:
    with pytest.raises(JobNotFound):
        storage.load_job(JOB_ID)


def test_an_updated_job_replaces_the_stored_state(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.create_job(a_job())

    storage.update_job(a_job(state=JobState.TRANSCRIBING))

    assert storage.load_job(JOB_ID).state is JobState.TRANSCRIBING


def test_updating_an_unknown_job_raises_rather_than_creating_it(
    storage: FilesystemTranscriptStorage,
) -> None:
    with pytest.raises(JobNotFound):
        storage.update_job(a_job(state=JobState.TRANSCRIBING))


def test_updating_one_job_leaves_the_other_untouched(
    storage: FilesystemTranscriptStorage,
) -> None:
    """Per-job storage isolation: two jobs, no cross-interference."""
    storage.create_job(a_job(JOB_ID))
    storage.create_job(a_job(OTHER_JOB_ID))

    storage.update_job(a_job(JOB_ID, state=JobState.FAILED))

    assert storage.load_job(OTHER_JOB_ID).state is JobState.PENDING


def test_listing_an_empty_store_returns_no_jobs(
    storage: FilesystemTranscriptStorage,
) -> None:
    assert storage.list_jobs() == ()


def test_jobs_are_listed_in_creation_order(
    storage: FilesystemTranscriptStorage,
) -> None:
    """ULIDs sort lexicographically by creation time, so directory order is already
    the order the operator wants. No timestamp comparison needed."""
    storage.create_job(a_job(OTHER_JOB_ID))
    storage.create_job(a_job(JOB_ID))

    assert [job.job_id for job in storage.list_jobs()] == [JOB_ID, OTHER_JOB_ID]


def test_listing_ignores_directories_that_are_not_jobs(
    storage: FilesystemTranscriptStorage, tmp_path: Path
) -> None:
    storage.create_job(a_job())
    (tmp_path / "jobs" / "scratch").mkdir()

    assert len(storage.list_jobs()) == 1


def test_listing_ignores_a_job_directory_with_no_record_yet(
    storage: FilesystemTranscriptStorage, tmp_path: Path
) -> None:
    storage.create_job(a_job())
    (tmp_path / "jobs" / OTHER_JOB_ID).mkdir()

    assert [job.job_id for job in storage.list_jobs()] == [JOB_ID]


def test_listing_fails_loudly_on_a_corrupted_job_record(
    storage: FilesystemTranscriptStorage, tmp_path: Path
) -> None:
    """A job missing from the list invites re-running a three-hour transcription.
    An error that names the bad file does not."""
    storage.create_job(a_job())
    (tmp_path / "jobs" / JOB_ID / "job.json").write_text("{}", encoding="utf-8")

    with pytest.raises(CorruptedRecord):
        storage.list_jobs()


@pytest.mark.parametrize(
    "hostile",
    ["../../etc/passwd", "..", "", "job.json", "01HQ3M8XKJ7VNPQR2ZYWB4TCF"],
)
def test_a_job_id_that_is_not_a_ulid_never_reaches_the_filesystem(
    storage: FilesystemTranscriptStorage, tmp_path: Path, hostile: str
) -> None:
    """The id is a path component, so it is validated before the path is built —
    not resolved and then checked for containment, which would already have created
    a directory somewhere by the time the check ran."""
    with pytest.raises(JobNotFound):
        storage.load_job(JobId(hostile))

    assert not (tmp_path / "jobs").exists()
