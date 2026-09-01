"""What the loop does when a chunk does not come back, and when the operator stops it.

Chunk 84 of 87 failing is a designed-for case on multi-hour input, not an
exception. Two things must both be true afterwards, and they pull in opposite
directions: the 83 completed chunks are still on disk and still valid, *and* the
job does not present itself as finished. A loop that satisfied only the first
would stitch a transcript with a hole in it — and a hole stitches into text that
reads perfectly, because the words either side of the gap simply run together.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.chunking import ChunkState
from onevoicecut.domain.errors import DiarizationUnsupported
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.media import SourceMedia
from onevoicecut.usecases.transcribe_job import transcribe_job
from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.fakes.transcription import FakeTranscriptionPort, FlakyFakeTranscriptionPort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
FIXED_NOW = 1723501234.5

# The fake track is 10s; a 2s stride plans four chunks (0-3) after tail absorption.
MULTI_CHUNK_STRIDE_S = 2.0
LAST_CHUNK = 3


def a_media() -> SourceMedia:
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicacion.mp4",
        stored_path=Path("source.mp4"),
        size_bytes=4096,
        container="mp4",
        checksum="deadbeef",
    )


def a_job(speaker_mode: SpeakerMode = SpeakerMode.SINGLE) -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=JobState.PENDING,
        speaker_mode=speaker_mode,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=1.0,
        worker_pid=None,
        error=None,
        owner=None,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    store = FakeTranscriptStoragePort(tmp_path)
    store.create_job(a_job())
    store.calls.clear()
    return store


def run(
    storage: FakeTranscriptStoragePort,
    transcriber: FakeTranscriptionPort | FlakyFakeTranscriptionPort,
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


def test_a_failing_chunk_does_not_discard_the_completed_ones(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The whole point of committing per chunk. Three hours of work survives one
    bad chunk."""
    run(storage, FlakyFakeTranscriptionPort({LAST_CHUNK: 99}))

    done = [r for r in storage.load_chunk_results(JOB_ID) if r.state is ChunkState.DONE]
    assert [r.index for r in done] == [0, 1, 2]
    assert all(r.segments for r in done)


def test_the_failure_is_recorded_against_its_own_chunk(
    storage: FakeTranscriptStoragePort,
) -> None:
    run(storage, FlakyFakeTranscriptionPort({LAST_CHUNK: 99}))

    failed = next(
        r for r in storage.load_chunk_results(JOB_ID) if r.state is ChunkState.FAILED
    )
    assert failed.index == LAST_CHUNK
    assert failed.error is not None
    assert failed.segments == ()


def test_the_loop_continues_past_a_failed_chunk(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Failing chunk 1 must not abandon chunks 2 and 3. On an 87-chunk sermon,
    stopping at the first failure would waste the rest of a multi-hour run."""
    transcriber = FlakyFakeTranscriptionPort({1: 99})

    run(storage, transcriber)

    assert [r.index for r in storage.load_chunk_results(JOB_ID)] == [0, 1, 2, 3]
    assert 3 in transcriber.attempts


def test_a_job_with_a_failed_chunk_does_not_report_success(
    storage: FakeTranscriptStoragePort,
) -> None:
    job = run(storage, FlakyFakeTranscriptionPort({LAST_CHUNK: 99}))

    assert job.state is JobState.FAILED
    assert storage.load_job(JOB_ID).state is JobState.FAILED


def test_no_transcript_is_stitched_over_a_hole(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The dangerous half. A missing chunk stitches into text that reads complete,
    because the words either side of the gap simply run together — nothing in the
    artifact announces that a quarter of the sermon is missing."""
    run(storage, FlakyFakeTranscriptionPort({LAST_CHUNK: 99}))

    assert storage.load_transcript(JOB_ID) is None
    assert "save_transcript" not in storage.calls


def test_the_job_record_names_what_failed(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The operator has to know which part of a three-hour sermon to look at."""
    job = run(storage, FlakyFakeTranscriptionPort({1: 99, LAST_CHUNK: 99}))

    assert job.error is not None
    assert "1" in job.error and str(LAST_CHUNK) in job.error


def test_an_engine_that_cannot_diarize_fails_the_job_at_the_first_chunk(
    storage: FakeTranscriptStoragePort,
) -> None:
    """A capability gap is not a chunk defect: every chunk would raise it. Grinding
    through 87 chunks to discover that 87 times is pure waste, and the eventual
    error would blame the last chunk instead of the job's configuration."""
    storage.update_job(a_job(SpeakerMode.MULTI))
    storage.calls.clear()
    transcriber = FakeTranscriptionPort()

    with pytest.raises(DiarizationUnsupported):
        run(storage, transcriber)

    assert storage.load_chunk_results(JOB_ID) == ()


def test_cancellation_is_honoured_at_a_chunk_boundary(
    storage: FakeTranscriptStoragePort,
) -> None:
    storage.request_cancellation(JOB_ID)

    job = run(storage, FakeTranscriptionPort())

    assert job.state is JobState.CANCELLED
    assert storage.load_chunk_results(JOB_ID) == ()


def test_cancellation_keeps_the_chunks_already_committed(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Cancelling is not discarding. A resumed job should not redo finished work,
    and an operator who cancels to free the machine has not asked to lose it."""
    transcriber = FakeTranscriptionPort()
    cancel_after = 2

    def cancel_once_two_chunks_are_done(index: int) -> None:
        if index == cancel_after - 1:
            storage.request_cancellation(JOB_ID)

    storage.on_chunk_saved = cancel_once_two_chunks_are_done

    job = run(storage, transcriber)

    assert job.state is JobState.CANCELLED
    assert [r.index for r in storage.load_chunk_results(JOB_ID)] == [0, 1]


def test_a_cancelled_job_produces_no_transcript(
    storage: FakeTranscriptStoragePort,
) -> None:
    storage.request_cancellation(JOB_ID)

    run(storage, FakeTranscriptionPort())

    assert storage.load_transcript(JOB_ID) is None


def test_cancellation_is_checked_before_each_chunk_not_only_at_the_start(
    storage: FakeTranscriptStoragePort,
) -> None:
    """A three-hour job polled once would ignore the stop button for three hours."""
    run(storage, FakeTranscriptionPort())

    polls = [call for call in storage.calls if call == "cancellation_requested"]
    assert len(polls) == 4
