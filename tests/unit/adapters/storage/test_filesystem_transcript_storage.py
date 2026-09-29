"""The monolith's remaining claim: it still satisfies `TranscriptStoragePort`.

Everything else this file proved moved out with its own facade — the job record
lifecycle to `JobStore` (slice 2b), the chunk plan and the transcript to
`TranscriptStore` (slice 3b), and the clips half (`artifacts.json`, the
per-profile exports and the render claims) to `ClipStore` (slice 4b). What stays
is the structural proof that `FilesystemTranscriptStorage` still binds the port,
because `runtime/` keeps constructing it until slice 4f retires it.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.shared.domain.ids import JobId, make_job_id, make_media_id
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
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


def test_the_adapter_satisfies_the_port(
    storage: FilesystemTranscriptStorage,
) -> None:
    """Structural conformance, proven by mypy on this assignment rather than at
    runtime — the point of `Protocol` ports is that no adapter imports the core to
    declare it implements one. All twelve methods now exist, so this binds."""
    storage.create_job(a_job())

    port: TranscriptStoragePort = storage

    assert len(port.list_jobs()) == 1
