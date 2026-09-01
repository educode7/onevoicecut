"""Retry and per-chunk timeouts — the two rules about how many times a chunk runs.

A cloud engine dropping one request out of eighty-seven is ordinary, and failing
the chunk on the first refusal would waste an hour of a job over a network blip.
So a failure is retried. A *timeout* is not: retrying it mostly spends the timeout
again, and three attempts at a thirty-minute budget is ninety minutes spent to
learn the same thing.

The other rule is a negative one, and it is the reason the job model exists: there
is no job-level deadline anywhere. A sermon that takes four hours to transcribe is
working, not hung.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.chunking import ChunkState
from onevoicecut.domain.errors import ChunkTimeout
from onevoicecut.domain.ids import make_job_id, make_media_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.media import SourceMedia
from onevoicecut.usecases.transcribe_job import DEFAULT_MAX_ATTEMPTS, transcribe_job
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
    transcriber: FlakyFakeTranscriptionPort,
    *,
    target_chunk_s: float = MULTI_CHUNK_STRIDE_S,
    chunk_timeout_s: float | None = 1800.0,
) -> JobRecord:
    return transcribe_job(
        JOB_ID,
        a_media(),
        extractor=FakeAudioExtractorPort(JOB_ID),
        transcriber=transcriber,
        storage=storage,
        now=lambda: FIXED_NOW,
        target_chunk_s=target_chunk_s,
        chunk_timeout_s=chunk_timeout_s,
    )


def test_a_transient_failure_is_retried_and_succeeds(
    storage: FakeTranscriptStoragePort,
) -> None:
    job = run(storage, FlakyFakeTranscriptionPort({1: 1}))

    assert job.state is JobState.COMPLETED
    assert all(r.state is ChunkState.DONE for r in storage.load_chunk_results(JOB_ID))


def test_only_the_failed_chunk_is_retried(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Re-running the whole job to recover one chunk is what chunking exists to
    avoid. Chunk 1 is attempted twice; every other chunk exactly once."""
    transcriber = FlakyFakeTranscriptionPort({1: 1})

    run(storage, transcriber)

    assert transcriber.attempts.count(1) == 2
    assert transcriber.attempts.count(0) == 1
    assert transcriber.attempts.count(2) == 1


def test_the_attempt_count_is_recorded_on_the_result(
    storage: FakeTranscriptStoragePort,
) -> None:
    """A chunk that needed three tries is evidence about the engine, not noise."""
    run(storage, FlakyFakeTranscriptionPort({1: 1}))

    by_index = {r.index: r for r in storage.load_chunk_results(JOB_ID)}
    assert by_index[1].attempts == 2
    assert by_index[0].attempts == 1


def test_retrying_is_bounded(storage: FakeTranscriptStoragePort) -> None:
    """A chunk the engine will never accept must not retry forever on a job that
    already runs for hours."""
    transcriber = FlakyFakeTranscriptionPort({1: 99})

    job = run(storage, transcriber)

    assert transcriber.attempts.count(1) == DEFAULT_MAX_ATTEMPTS
    assert job.state is JobState.FAILED


def test_an_exhausted_chunk_records_every_attempt_it_made(
    storage: FakeTranscriptStoragePort,
) -> None:
    run(storage, FlakyFakeTranscriptionPort({1: 99}))

    failed = next(
        r for r in storage.load_chunk_results(JOB_ID) if r.state is ChunkState.FAILED
    )
    assert failed.attempts == DEFAULT_MAX_ATTEMPTS


def test_a_timeout_is_not_retried(storage: FakeTranscriptStoragePort) -> None:
    """The one failure worth giving up on immediately. Three attempts at a
    thirty-minute budget is ninety minutes spent to learn the same thing."""
    transcriber = FlakyFakeTranscriptionPort({1: 99}, error=ChunkTimeout)

    run(storage, transcriber)

    assert transcriber.attempts.count(1) == 1


def test_a_timed_out_chunk_is_recorded_as_failed_with_its_reason(
    storage: FakeTranscriptStoragePort,
) -> None:
    run(storage, FlakyFakeTranscriptionPort({1: 99}, error=ChunkTimeout))

    by_index = {r.index: r for r in storage.load_chunk_results(JOB_ID)}
    assert by_index[1].state is ChunkState.FAILED
    assert by_index[1].attempts == 1
    assert by_index[1].error is not None


def test_a_timed_out_chunk_does_not_stop_the_job(
    storage: FakeTranscriptStoragePort,
) -> None:
    transcriber = FlakyFakeTranscriptionPort({1: 99}, error=ChunkTimeout)

    run(storage, transcriber)

    assert [r.index for r in storage.load_chunk_results(JOB_ID)] == [0, 1, 2, 3]
    assert 3 in transcriber.attempts


def test_the_per_chunk_budget_reaches_the_engine(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The adapter honours the timeout in-call where it can. It cannot honour a
    budget it was never given."""
    transcriber = FlakyFakeTranscriptionPort()

    run(storage, transcriber, chunk_timeout_s=42.0)

    assert {request.timeout_s for request in transcriber.requests} == {42.0}


def test_a_long_job_is_never_terminated_on_elapsed_time_alone(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The negative property the whole job model rests on. The clock jumps an hour
    between every chunk and the job still completes: there is no job-level
    deadline, because a sermon that takes four hours is working, not hung."""
    ticking = iter(FIXED_NOW + hour * 3600.0 for hour in range(100))
    transcriber = FlakyFakeTranscriptionPort()

    job = transcribe_job(
        JOB_ID,
        a_media(),
        extractor=FakeAudioExtractorPort(JOB_ID),
        transcriber=transcriber,
        storage=storage,
        now=lambda: next(ticking),
        target_chunk_s=MULTI_CHUNK_STRIDE_S,
    )

    assert job.state is JobState.COMPLETED


def test_a_job_may_run_with_no_timeout_at_all(
    storage: FakeTranscriptStoragePort,
) -> None:
    """`None` is a legitimate budget: a local engine on a trusted machine has no
    provider to time out against."""
    transcriber = FlakyFakeTranscriptionPort()

    job = run(storage, transcriber, chunk_timeout_s=None)

    assert job.state is JobState.COMPLETED
    assert {request.timeout_s for request in transcriber.requests} == {None}
