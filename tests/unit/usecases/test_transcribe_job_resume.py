"""Restarting a crashed job, through the same entry point that started it.

The crash is simulated by leaving storage in the state a dead process would have
left it: a persisted plan, some committed chunk results, no transcript. Then the
job is simply run again. Nothing here calls a special resume function, because
there isn't one.
"""

from pathlib import Path

import pytest

from transcribe.domain.chunking import ChunkResult, ChunkState
from transcribe.domain.ids import make_job_id, make_media_id
from transcribe.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from transcribe.domain.media import SourceMedia
from transcribe.usecases.transcribe_job import transcribe_job
from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.fakes.transcription import FlakyFakeTranscriptionPort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
FIXED_NOW = 1723501234.5
MULTI_CHUNK_STRIDE_S = 2.0


def a_media() -> SourceMedia:
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicacion.mp4",
        stored_path=Path("source.mp4"),
        size_bytes=4096,
        container="mp4",
        checksum="deadbeef",
    )


def a_job() -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=JobState.PENDING,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=1.0,
        worker_pid=None,
        error=None,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    store = FakeTranscriptStoragePort(tmp_path)
    store.create_job(a_job())
    store.calls.clear()
    return store


def run(
    storage: FakeTranscriptStoragePort, transcriber: FlakyFakeTranscriptionPort
) -> JobRecord:
    return transcribe_job(
        JOB_ID,
        a_media(),
        extractor=FakeAudioExtractorPort(JOB_ID),
        transcriber=transcriber,
        storage=storage,
        now=lambda: FIXED_NOW,
        target_chunk_s=MULTI_CHUNK_STRIDE_S,
    )


def crash_after(storage: FakeTranscriptStoragePort, chunks: int) -> None:
    """Leave storage exactly as a process killed mid-job would have left it."""
    transcriber = FlakyFakeTranscriptionPort()
    stop_at = chunks

    def die_once_enough_chunks_landed(index: int) -> None:
        if index == stop_at - 1:
            raise KeyboardInterrupt("process killed")

    storage.on_chunk_saved = die_once_enough_chunks_landed
    with pytest.raises(KeyboardInterrupt):
        run(storage, transcriber)
    storage.on_chunk_saved = None
    storage.calls.clear()


def test_a_restarted_job_finishes_what_was_left(
    storage: FakeTranscriptStoragePort,
) -> None:
    crash_after(storage, 2)

    job = run(storage, FlakyFakeTranscriptionPort())

    assert job.state is JobState.COMPLETED
    assert len(storage.load_chunk_results(JOB_ID)) == 4


def test_a_restart_does_not_transcribe_the_completed_chunks_again(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The whole value of resume. On a real sermon this is hours of ASR not spent
    a second time."""
    crash_after(storage, 2)
    transcriber = FlakyFakeTranscriptionPort()

    run(storage, transcriber)

    assert sorted(transcriber.attempts) == [2, 3]


def test_a_restart_leaves_the_committed_results_untouched(
    storage: FakeTranscriptStoragePort,
) -> None:
    crash_after(storage, 2)
    before = storage.load_chunk_results(JOB_ID)[:2]

    run(storage, FlakyFakeTranscriptionPort())

    assert storage.load_chunk_results(JOB_ID)[:2] == before


def test_a_restart_reuses_the_stored_plan_rather_than_replanning(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Re-planning would usually agree, and "usually" is not enough: the stored
    results are indexed against the stored plan, so a plan differing by one chunk
    would re-map every completed result onto the wrong range of the sermon."""
    crash_after(storage, 2)

    run(storage, FlakyFakeTranscriptionPort())

    assert "save_chunk_plan" not in storage.calls


def test_a_restart_produces_the_transcript_the_first_run_would_have(
    storage: FakeTranscriptStoragePort,
) -> None:
    crash_after(storage, 2)

    run(storage, FlakyFakeTranscriptionPort())

    resumed = storage.load_transcript(JOB_ID)
    assert resumed is not None
    assert resumed.segments[-1].end_s == pytest.approx(10.0)


def test_a_restart_retries_a_chunk_that_had_failed(
    storage: FakeTranscriptStoragePort,
) -> None:
    """A transient provider outage is exactly why an operator restarts a job."""
    run(storage, FlakyFakeTranscriptionPort({2: 99}))
    assert storage.load_job(JOB_ID).state is JobState.FAILED
    transcriber = FlakyFakeTranscriptionPort()

    job = run(storage, transcriber)

    assert job.state is JobState.COMPLETED
    assert transcriber.attempts == [2]


def test_restarting_a_finished_job_redoes_no_transcription(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Idempotent by construction: nothing is owed, so the loop goes straight to
    stitching."""
    run(storage, FlakyFakeTranscriptionPort())
    transcriber = FlakyFakeTranscriptionPort()

    job = run(storage, transcriber)

    assert job.state is JobState.COMPLETED
    assert transcriber.attempts == []


def test_a_chunk_left_running_by_the_crash_is_redone(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Trusting a RUNNING result would drop that chunk, and a dropped chunk
    stitches into text that reads continuous."""
    crash_after(storage, 2)
    storage.save_chunk_result(
        ChunkResult(
            job_id=JOB_ID,
            index=2,
            state=ChunkState.RUNNING,
            segments=(),
            engine_id="fake-asr",
            attempts=1,
            error=None,
            finished_at=None,
        )
    )
    transcriber = FlakyFakeTranscriptionPort()

    run(storage, transcriber)

    assert 2 in transcriber.attempts
