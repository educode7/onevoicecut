"""Cancellation crosses a process boundary, which is why it is a second file.

The web process and the worker both have a reason to write job state, and that is a
guaranteed race. The resolution is not a lock: while a worker is alive it is the
sole writer of `job.json`, and the web process asks for cancellation by writing
`control.json`, which the worker polls at chunk boundaries.

So the property under test is not "cancellation works". It is that asking for it
never writes the file the worker owns.
"""

from pathlib import Path

import pytest

from transcribe.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from transcribe.domain.errors import CorruptedRecord, JobNotFound
from transcribe.domain.ids import JobId, make_job_id, make_media_id
from transcribe.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from transcribe.ports.transcript_storage import TranscriptStoragePort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
OTHER_JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFF")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")


def a_job(job_id: JobId) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        media_id=MEDIA_ID,
        state=JobState.TRANSCRIBING,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=1.0,
        worker_pid=9001,
        error=None,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemTranscriptStorage:
    store = FilesystemTranscriptStorage(tmp_path)
    store.create_job(a_job(JOB_ID))
    return store


def test_the_adapter_satisfies_the_port(
    storage: FilesystemTranscriptStorage,
) -> None:
    """Structural conformance, proven by mypy on this assignment rather than at
    runtime — the point of `Protocol` ports is that no adapter imports the core to
    declare it implements one. All twelve methods now exist, so this binds."""
    port: TranscriptStoragePort = storage

    assert len(port.list_jobs()) == 1


def test_a_job_nobody_cancelled_is_not_cancelled(
    storage: FilesystemTranscriptStorage,
) -> None:
    assert storage.cancellation_requested(JOB_ID) is False


def test_a_requested_cancellation_is_visible_to_the_worker(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.request_cancellation(JOB_ID)

    assert storage.cancellation_requested(JOB_ID) is True


def test_requesting_cancellation_never_writes_the_job_record(
    storage: FilesystemTranscriptStorage,
) -> None:
    """The single-writer rule. While the worker is alive it is the sole writer of
    `job.json`; the web process gets a different file or it gets a race."""
    record = storage.job_dir(JOB_ID) / "job.json"
    before = record.read_bytes()

    storage.request_cancellation(JOB_ID)

    assert record.read_bytes() == before
    assert storage.load_job(JOB_ID) == a_job(JOB_ID)


def test_the_request_lands_in_its_own_file(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.request_cancellation(JOB_ID)

    assert (storage.job_dir(JOB_ID) / "control.json").is_file()


def test_the_request_is_committed_atomically_like_every_other_write(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.request_cancellation(JOB_ID)

    assert list(storage.job_dir(JOB_ID).glob("*.tmp")) == []


def test_polling_for_cancellation_writes_nothing(
    storage: FilesystemTranscriptStorage,
) -> None:
    """The worker polls this at every chunk boundary of a multi-hour job. A poll
    that touched the directory would make the read half of a race."""
    before = sorted(path.name for path in storage.job_dir(JOB_ID).iterdir())

    storage.cancellation_requested(JOB_ID)

    assert sorted(path.name for path in storage.job_dir(JOB_ID).iterdir()) == before


def test_cancellation_is_scoped_to_one_job(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.create_job(a_job(OTHER_JOB_ID))

    storage.request_cancellation(JOB_ID)

    assert storage.cancellation_requested(JOB_ID) is True
    assert storage.cancellation_requested(OTHER_JOB_ID) is False


def test_a_withdrawn_request_stops_being_visible(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.request_cancellation(JOB_ID)

    storage.request_cancellation(JOB_ID, requested=False)

    assert storage.cancellation_requested(JOB_ID) is False


def test_cancelling_an_uncreated_job_is_refused(
    storage: FilesystemTranscriptStorage,
) -> None:
    with pytest.raises(JobNotFound):
        storage.request_cancellation(OTHER_JOB_ID)


def test_polling_an_uncreated_job_reports_no_cancellation(
    storage: FilesystemTranscriptStorage,
) -> None:
    """Reads stay tolerant, as everywhere else in this adapter."""
    assert storage.cancellation_requested(OTHER_JOB_ID) is False


def test_an_unreadable_control_file_is_reported_rather_than_ignored(
    storage: FilesystemTranscriptStorage,
) -> None:
    """A silently ignored control file is a stop button that does nothing, on a job
    that runs for hours. Better to name the file the operator has to delete."""
    (storage.job_dir(JOB_ID) / "control.json").write_text("garbage", encoding="utf-8")

    with pytest.raises(CorruptedRecord):
        storage.cancellation_requested(JOB_ID)
