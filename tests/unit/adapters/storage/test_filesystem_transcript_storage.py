"""The composition root's storage object satisfies the whole surface at once.

Everything else this file proved moved out with its own facade — the job record
lifecycle to `JobStore` (slice 2b), the chunk plan and the transcript to
`TranscriptStore` (slice 3b), and the clips half (`artifacts.json`, the
per-profile exports and the render claims) to `ClipStore` (slice 4b). What stays
is the structural proof that `FilesystemTranscriptStorage` still satisfies all
three: `runtime/` constructs it once and hands that one object to every module's
handler, so a gap in any half is a build error there rather than an AttributeError
three hours into a job.
"""

from pathlib import Path

import pytest

from onevoicecut.runtime.storage import (
    FilesystemTranscriptStorage,
    StorageComposite,
)
from onevoicecut.shared.domain.ids import JobId, make_job_id, make_media_id
from onevoicecut.shared.domain.speaker import SpeakerMode
from onevoicecut.systems.pipeline.jobs.domain.jobs import EngineChoice, JobRecord, JobState

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
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


def test_the_adapter_satisfies_every_narrow_protocol(
    storage: FilesystemTranscriptStorage,
) -> None:
    """Structural conformance, proven by mypy on this assignment rather than at
    runtime — the point of `Protocol` is that an implementation never imports the
    core to declare it implements one. `StorageComposite` is the union of the
    three narrow Protocols, so this one assignment binds the whole surface."""
    storage.create_job(a_job())

    composite: StorageComposite = storage

    assert len(composite.list_jobs()) == 1
