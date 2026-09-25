"""The source media record: written at admission, read by a worker hours later.

The job record carries only a media id, so this is the only place the container,
the stored path and the checksum live. Without it a worker in a separate process
would have to invent a `SourceMedia`, and an invented checksum is worse than none.
"""

from pathlib import Path

import pytest

from onevoicecut.shared.domain.errors import JobNotFound
from onevoicecut.shared.domain.ids import JobId, make_job_id, make_media_id
from onevoicecut.shared.infrastructure.storage.core import StorageCore
from onevoicecut.shared.domain.speaker import SpeakerMode
from onevoicecut.systems.pipeline.jobs.domain.jobs import (
    EngineChoice,
    JobRecord,
    JobState,
)
from onevoicecut.shared.domain.media import SourceMedia
from onevoicecut.systems.pipeline.jobs.infrastructure.storage.job_store import (
    FilesystemJobStore,
)

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")


def a_job(job_id: JobId) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        media_id=MEDIA_ID,
        state=JobState.TRANSCRIBING,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1723501234.5,
        updated_at=1723501234.5,
        worker_pid=None,
        error=None,
        owner=None,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemJobStore:
    store = FilesystemJobStore(StorageCore(tmp_path))
    store.create_job(a_job(JOB_ID))
    return store


def test_the_source_media_record_round_trips(
    storage: FilesystemJobStore,
) -> None:
    """Written at admission, read by a worker in another process hours later.

    The job record carries only a media id; without this the worker would have to
    invent a `SourceMedia`, and an invented checksum is worse than none.
    """
    media = SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicación del domingo.mp4",
        stored_path=storage.job_dir(JOB_ID) / "source.mp4",
        size_bytes=4096,
        container="mp4",
        checksum="deadbeef",
    )

    storage.save_media(JOB_ID, media)

    assert storage.load_media(JOB_ID) == media


def test_a_job_with_no_media_recorded_is_reported_not_guessed(
    storage: FilesystemJobStore,
) -> None:
    with pytest.raises(JobNotFound):
        storage.load_media(JOB_ID)
