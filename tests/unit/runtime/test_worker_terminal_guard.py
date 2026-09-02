"""A worker that starts on a job already finished must do nothing at all.

This is the losing half of the spawn-versus-cancel race. The gate re-reads before
spawning, so the ordinary outcome is that the cancel wins and no worker starts.
But the two happen in different processes, and a cancel landing in the microsecond
after the re-read still leaves a worker being born for a job the operator stopped.

Containment is two independent mechanisms, and this file covers the first: before
claiming anything, the worker checks whether the record is already terminal and
leaves if it is. The second — the chunk-boundary poll — is characterized in
`tests/unit/usecases/test_cancel_boundary.py`, and the last test here shows the
two composing.

The claim is what makes this matter. Writing `worker_pid` onto a cancelled record
would make it look worker-bound to the next drain sweep, so the job would occupy
a slot until something noticed the pid was gone — a cancelled job holding the
machine's only slot is precisely the failure the capacity gate exists to prevent.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import (
    TERMINAL_STATES,
    EngineChoice,
    JobRecord,
    JobState,
    SpeakerMode,
)
from onevoicecut.domain.media import SourceMedia
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.runtime.engine_resolver import EngineResolver
from onevoicecut.runtime.worker import run_job
from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.transcription import FakeTranscriptionPort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")


class RecordingExtractorFactory:
    """Counts, because "did no work" is only provable as an absence of calls."""

    def __init__(self) -> None:
        self.built: list[JobId] = []

    def __call__(self, job_dir: Path, job_id: JobId) -> AudioExtractorPort:
        self.built.append(job_id)
        return FakeAudioExtractorPort(job_id)


def recording_resolver(resolved: list[EngineChoice]) -> EngineResolver:
    def build() -> FakeTranscriptionPort:
        resolved.append(EngineChoice.LOCAL)
        return FakeTranscriptionPort()

    return EngineResolver({EngineChoice.LOCAL: build})


def a_job_in(data_dir: Path, state: JobState) -> FilesystemTranscriptStorage:
    """A job on disk in `state`, with its media described, exactly as the gate
    would have left it."""
    storage = FilesystemTranscriptStorage(data_dir)
    storage.create_job(
        JobRecord(
            job_id=JOB_ID,
            media_id=MEDIA_ID,
            state=state,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=1.0,
            updated_at=1.0,
            worker_pid=None,
            error=None,
            owner=OWNER,
        )
    )
    storage.save_media(
        JOB_ID,
        SourceMedia(
            media_id=MEDIA_ID,
            original_filename="predicacion.mp4",
            stored_path=storage.job_dir(JOB_ID) / "source",
            size_bytes=4096,
            container="mp4",
            checksum="deadbeef",
        ),
    )
    return storage


class TestATerminalRecordStopsTheWorkerDead:
    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
    def test_no_claim_is_written(self, tmp_path: Path, state: JobState) -> None:
        """CAP-10: the record must not start looking worker-bound.

        A `worker_pid` on a finished job reads as an occupied slot to the next
        drain sweep, and the queue waits on a process that is not coming back.
        """
        storage = a_job_in(tmp_path, state)

        run_job(
            JOB_ID,
            tmp_path,
            resolver=recording_resolver([]),
            extractor_factory=RecordingExtractorFactory(),
        )

        assert storage.load_job(JOB_ID).worker_pid is None

    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
    def test_the_record_comes_back_untouched(
        self, tmp_path: Path, state: JobState
    ) -> None:
        storage = a_job_in(tmp_path, state)
        before = storage.load_job(JOB_ID)

        returned = run_job(
            JOB_ID,
            tmp_path,
            resolver=recording_resolver([]),
            extractor_factory=RecordingExtractorFactory(),
        )

        assert returned == before
        assert storage.load_job(JOB_ID) == before

    @pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
    def test_nothing_is_extracted_and_no_engine_is_resolved(
        self, tmp_path: Path, state: JobState
    ) -> None:
        """Resolving the engine is not free — for a cloud engine it is where a
        secret is read, and for a local one it is where model weights load."""
        a_job_in(tmp_path, state)
        extractor_factory = RecordingExtractorFactory()
        resolved: list[EngineChoice] = []

        run_job(
            JOB_ID,
            tmp_path,
            resolver=recording_resolver(resolved),
            extractor_factory=extractor_factory,
        )

        assert extractor_factory.built == []
        assert resolved == []


class TestANonTerminalRecordStillRuns:
    def test_a_queued_job_is_claimed_and_transcribed(self, tmp_path: Path) -> None:
        """The guard must stop the finished cases without stopping the only case
        the product needs. A guard that refused everything would satisfy every
        assertion above."""
        storage = a_job_in(tmp_path, JobState.QUEUED)

        returned = run_job(
            JOB_ID,
            tmp_path,
            resolver=recording_resolver([]),
            extractor_factory=RecordingExtractorFactory(),
        )

        assert returned.state is JobState.COMPLETED
        assert storage.load_transcript(JOB_ID) is not None


class TestTheSpawnWinsRace:
    def test_a_worker_that_wins_the_race_still_transcribes_nothing(
        self, tmp_path: Path
    ) -> None:
        """The two containment mechanisms composing.

        The record still reads QUEUED — the cancel wrote the control file but the
        drain had already issued the spawn — so the terminal guard lets this
        worker through. The chunk-boundary poll is what catches it, before the
        first chunk, which is why cancellation writes both the record and the
        control file rather than trusting either alone.
        """
        storage = a_job_in(tmp_path, JobState.QUEUED)
        storage.request_cancellation(JOB_ID)

        returned = run_job(
            JOB_ID,
            tmp_path,
            resolver=recording_resolver([]),
            extractor_factory=RecordingExtractorFactory(),
        )

        assert returned.state is JobState.CANCELLED
        assert storage.load_chunk_results(JOB_ID) == ()
        assert storage.load_transcript(JOB_ID) is None
