"""Who writes the heartbeat, and when. The answer to "who" is: only the worker.

Two writes matter. The first happens immediately after the claim, so a worker
that dies during extraction — before any chunk boundary exists — still left
evidence it was ever alive. The rest happen at chunk boundaries, next to the
cancellation poll, because that is the only point in a multi-hour run where the
loop reliably comes up for air.

There is no timer thread. A background thread ticking every few seconds would
report a hung worker as healthy, which is precisely the failure the heartbeat
exists to catch: liveness has to be a side effect of doing work, not of being
loaded into memory.

The single-writer rule is the other half of this file. Every other process has a
reason to touch a job — the web adapter uploads, the gate spawns, reconcile
rewrites — and if any of them wrote a heartbeat, the file would vouch for a
worker that does not exist.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    HEARTBEAT,
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.media import SourceMedia
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.runtime.app import drain_once, reconcile_interrupted_jobs
from onevoicecut.runtime.engine_resolver import EngineResolver
from onevoicecut.runtime.worker import run_job
from onevoicecut.usecases.cancel_job import cancel_job
from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.fakes.transcription import FakeTranscriptionPort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")

CLAIMED_AT = 1_700_000_000.0


def fake_extractor(job_dir: Path, job_id: JobId) -> AudioExtractorPort:
    return FakeAudioExtractorPort(job_id)


def a_job(state: JobState = JobState.QUEUED, *, pid: int | None = None) -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
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


def a_media(storage: FilesystemTranscriptStorage) -> SourceMedia:
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicacion.mp4",
        stored_path=storage.job_dir(JOB_ID) / "source",
        size_bytes=4096,
        container="mp4",
        checksum="deadbeef",
    )


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    storage = FilesystemTranscriptStorage(tmp_path)
    storage.create_job(a_job())
    storage.save_media(JOB_ID, a_media(storage))
    return tmp_path


def heartbeat_of(data_dir: Path) -> float:
    storage = FilesystemTranscriptStorage(data_dir)
    return float((storage.job_dir(JOB_ID) / HEARTBEAT).read_text(encoding="utf-8"))


class TestTheWorkerWritesOne:
    def test_a_heartbeat_exists_as_soon_as_the_job_is_claimed(
        self, data_dir: Path
    ) -> None:
        """HARD-01. Written next to the claim, not after the first chunk.

        Extraction on a three-hour file happens before any boundary exists. A
        worker that died there with no heartbeat at all would be indistinguishable
        from one that never started, and its slot would be held by a pid check
        alone — the thing the heartbeat is here to stop trusting.
        """
        run_job(
            JOB_ID,
            data_dir,
            resolver=EngineResolver({EngineChoice.LOCAL: FakeTranscriptionPort}),
            extractor_factory=fake_extractor,
            now=lambda: CLAIMED_AT,
        )

        assert heartbeat_of(data_dir) == CLAIMED_AT

    def test_the_clock_is_injected_rather_than_read_from_the_wall(
        self, data_dir: Path
    ) -> None:
        """Freshness is arithmetic on this number, so a test that could not
        control it would have to sleep — and a default suite that sleeps for two
        hours is a suite nobody runs."""
        run_job(
            JOB_ID,
            data_dir,
            resolver=EngineResolver({EngineChoice.LOCAL: FakeTranscriptionPort}),
            extractor_factory=fake_extractor,
            now=lambda: 42.0,
        )

        assert heartbeat_of(data_dir) == 42.0


class TestOnlyTheWorkerWritesOne:
    """HARD-03. Every other actor has a reason to touch a job record; none of
    them may vouch for a worker."""

    @pytest.fixture
    def storage(self, tmp_path: Path) -> FakeTranscriptStoragePort:
        store = FakeTranscriptStoragePort(tmp_path)
        store.create_job(a_job())
        store.calls.clear()
        return store

    def test_cancelling_writes_no_heartbeat(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        cancel_job(JOB_ID, operator=OWNER, storage=storage, now=lambda: CLAIMED_AT)

        assert "write_heartbeat" not in storage.calls

    def test_a_drain_sweep_writes_no_heartbeat(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """The gate spawning a worker must not pre-emptively vouch for it: the
        window between issuing a spawn and the worker's own first write is
        exactly when a launch failure has to remain visible."""
        drain_once(
            storage,
            max_concurrent_jobs=1,
            launch=lambda job_id: None,
            spawned=set(),
            is_alive=lambda pid: True,
        )

        assert "write_heartbeat" not in storage.calls

    def test_reconcile_writes_no_heartbeat(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """Reconcile rewrites records precisely because no worker is behind
        them. Writing a heartbeat there would resurrect what it just buried."""
        storage.update_job(a_job(JobState.TRANSCRIBING, pid=9999))
        storage.calls.clear()

        reconcile_interrupted_jobs(
            storage, now=lambda: CLAIMED_AT, is_alive=lambda pid: False
        )

        assert "write_heartbeat" not in storage.calls

    def test_the_owner_survives_every_heartbeat_era_transition(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """OWN-02's final lock, across the transitions this slice added."""
        storage.update_job(a_job(JobState.TRANSCRIBING, pid=9999))

        reconcile_interrupted_jobs(
            storage, now=lambda: CLAIMED_AT, is_alive=lambda pid: False
        )

        assert storage.load_job(JOB_ID).owner == OWNER
