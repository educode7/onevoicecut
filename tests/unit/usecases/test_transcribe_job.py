"""The core loop, driven end to end against fakes.

Everything this orchestrates already exists and is already proven in isolation:
planning (2a), stitching (2b), extraction (3a/3b), persistence (4a). What is
unproven — and what these tests are for — is the *order*, and the fact that each
chunk is committed as it completes rather than at the end. A loop that transcribed
all 87 chunks correctly and persisted them in one batch at the end would pass every
existing test and lose three hours of work to a crash at chunk 86.
"""

from pathlib import Path

import pytest

from transcribe.domain.chunking import ChunkState
from transcribe.domain.ids import JobId, make_job_id, make_media_id
from transcribe.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from transcribe.domain.media import SourceMedia
from transcribe.domain.transcript import SegmentKind
from transcribe.usecases.transcribe_job import transcribe_job
from tests.fakes.audio_extractor import FAKE_DURATION_S, FakeAudioExtractorPort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.fakes.transcription import FakeTranscriptionPort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")

# Comfortably longer than the fake track, so the default path is a single chunk
# unless a test asks for more.
FIXED_NOW = 1723501234.5


def a_media() -> SourceMedia:
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicacion.mp4",
        stored_path=Path("source.mp4"),
        size_bytes=4096,
        container="mp4",
        checksum="deadbeef",
    )


def a_job(job_id: JobId = JOB_ID) -> JobRecord:
    return JobRecord(
        job_id=job_id,
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
    store.calls.clear()  # admission is the web process's work, not the loop's
    return store


def run(
    storage: FakeTranscriptStoragePort,
    *,
    transcriber: FakeTranscriptionPort | None = None,
    target_chunk_s: float = 600.0,
) -> None:
    transcribe_job(
        JOB_ID,
        a_media(),
        extractor=FakeAudioExtractorPort(JOB_ID),
        transcriber=transcriber or FakeTranscriptionPort(),
        storage=storage,
        now=lambda: FIXED_NOW,
        target_chunk_s=target_chunk_s,
    )


def test_a_job_runs_to_completion(storage: FakeTranscriptStoragePort) -> None:
    run(storage)

    assert storage.load_job(JOB_ID).state is JobState.COMPLETED


def test_the_job_walks_its_states_in_order(storage: FakeTranscriptStoragePort) -> None:
    """The states are not decoration: the web process polls them, and a job that
    jumps straight from PENDING to COMPLETED tells the operator nothing for hours."""
    run(storage)

    assert storage.state_history() == [
        JobState.EXTRACTING,
        JobState.PLANNED,
        JobState.TRANSCRIBING,
        JobState.STITCHING,
        JobState.COMPLETED,
    ]


def test_the_chunk_plan_is_persisted_before_transcription_starts(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Resume reads the plan. A plan held only in memory makes resume impossible,
    because nothing on disk says how many chunks the job was supposed to have."""
    run(storage)

    plan = storage.load_chunk_plan(JOB_ID)
    assert plan is not None
    assert len(plan.chunks) == 1


def test_every_planned_chunk_produces_a_persisted_result(
    storage: FakeTranscriptStoragePort,
) -> None:
    run(storage, target_chunk_s=2.0)

    plan = storage.load_chunk_plan(JOB_ID)
    results = storage.load_chunk_results(JOB_ID)
    assert plan is not None
    assert [r.index for r in results] == [c.index for c in plan.chunks]
    assert all(r.state is ChunkState.DONE for r in results)


def test_each_chunk_is_committed_as_it_completes(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The property the whole storage slice exists for. Asserted by watching the
    order of calls: a result must be saved before the next chunk is transcribed,
    otherwise a crash at chunk 86 discards 85 completed chunks."""
    run(storage, target_chunk_s=2.0)

    saves = [call for call in storage.calls if call.startswith("save_chunk_result")]
    assert len(saves) > 1
    assert storage.calls.index("save_chunk_result:0") < storage.calls.index(
        "save_chunk_result:1"
    )


def test_the_stitched_transcript_is_persisted(
    storage: FakeTranscriptStoragePort,
) -> None:
    run(storage)

    transcript = storage.load_transcript(JOB_ID)
    assert transcript is not None
    assert transcript.job_id == JOB_ID
    assert [s.text for s in transcript.segments] == ["hola mundo"]


def test_the_transcript_records_which_engine_produced_it(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Two adapters are interchangeable behind the port, so the artifact has to say
    which one ran — otherwise a quality problem cannot be attributed to an engine."""
    run(storage)

    transcript = storage.load_transcript(JOB_ID)
    assert transcript is not None
    assert transcript.engine_id == "fake-asr"
    assert transcript.diarized is False


def test_transcript_times_are_track_relative_not_chunk_local(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The port returns chunk-local times and the stitcher is the only place they
    become absolute. If the loop skipped the stitcher, every chunk's segments would
    start at zero and all clip timestamps would point at the first seconds."""
    run(storage, target_chunk_s=2.0)

    transcript = storage.load_transcript(JOB_ID)
    assert transcript is not None
    assert transcript.segments[-1].end_s == pytest.approx(FAKE_DURATION_S)
    assert transcript.segments[-1].start_s > 0.0


def test_chunk_results_carry_the_engine_and_a_finish_time(
    storage: FakeTranscriptStoragePort,
) -> None:
    run(storage)

    result = storage.load_chunk_results(JOB_ID)[0]
    assert result.engine_id == "fake-asr"
    assert result.finished_at == FIXED_NOW
    assert result.attempts == 1
    assert result.error is None


def test_segment_classification_survives_the_whole_loop(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Music is marked, never dropped, and the loop is one more place it could be
    lost between the adapter and the stored transcript."""
    transcriber = FakeTranscriptionPort(
        script=(("hola", SegmentKind.SPEECH), ("lalala", SegmentKind.MUSIC))
    )

    run(storage, transcriber=transcriber)

    transcript = storage.load_transcript(JOB_ID)
    assert transcript is not None
    assert [s.kind for s in transcript.segments] == [
        SegmentKind.SPEECH,
        SegmentKind.MUSIC,
    ]


def test_the_job_record_is_updated_never_recreated(
    storage: FakeTranscriptStoragePort,
) -> None:
    """The worker is the sole writer of the job record, and it owns a job that
    already exists. A loop that called create_job would destroy the web process's
    record of why the job was admitted."""
    run(storage)

    assert "create_job" not in storage.calls
