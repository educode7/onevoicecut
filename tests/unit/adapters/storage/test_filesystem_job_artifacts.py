"""Everything a job accumulates after its record exists: plan, transcript, artifacts, export.

Absence is the interesting case here. A job with no chunk plan and a job with no
transcript are both normal mid-run states, not errors, and the port says so by
returning `None`. A save against a job that was never created is the opposite: it
would leave a directory holding a transcript and no `job.json`, which `list_jobs`
skips — an orphan that looks like nothing at all.
"""

from pathlib import Path

import pytest

from transcribe.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from transcribe.adapters.storage.serialization import decode_artifacts
from transcribe.domain.chunking import ChunkPlan, PlannedChunk
from transcribe.domain.errors import JobNotFound
from transcribe.domain.generation import ClipCandidate, GenerationResult, ScriptVariant
from transcribe.domain.ids import JobId, make_job_id, make_media_id
from transcribe.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from transcribe.domain.media import SourceMedia
from transcribe.domain.transcript import SegmentKind, Transcript, TranscriptSegment

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
        created_at=1723501234.5,
        updated_at=1723501234.5,
        worker_pid=None,
        error=None,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemTranscriptStorage:
    store = FilesystemTranscriptStorage(tmp_path)
    store.create_job(a_job(JOB_ID))
    return store


def a_plan(job_id: JobId = JOB_ID) -> ChunkPlan:
    return ChunkPlan(
        job_id=job_id,
        stride_s=600.0,
        overlap_s=5.0,
        chunks=(
            PlannedChunk(index=0, start_s=0.0, end_s=605.0),
            PlannedChunk(index=1, start_s=600.0, end_s=1205.0),
        ),
    )


def a_transcript(job_id: JobId = JOB_ID) -> Transcript:
    return Transcript(
        job_id=job_id,
        segments=(
            TranscriptSegment(
                start_s=0.0,
                end_s=4.5,
                text="hola a todos",
                speaker=None,
                confidence=0.9,
                kind=SegmentKind.SPEECH,
            ),
            TranscriptSegment(
                start_s=4.5,
                end_s=30.0,
                text="lalala",
                speaker=None,
                confidence=0.4,
                kind=SegmentKind.MUSIC,
            ),
        ),
        engine_id="faster-whisper/large-v3",
        diarized=False,
    )


def an_artifact_set() -> GenerationResult:
    return GenerationResult(
        job_id=JOB_ID,
        summary="Resumen del mensaje.",
        clip_candidates=(
            ClipCandidate(
                start_s=10.0,
                end_s=45.0,
                hook="gancho",
                quote="cita",
                rationale="razon",
                score=0.7,
                variants=(
                    ScriptVariant(
                        target="tiktok",
                        format="vertical",
                        body="guion",
                        duration_target_s=45.0,
                    ),
                ),
            ),
        ),
    )


def test_the_working_paths_stay_inside_the_job_directory(
    storage: FilesystemTranscriptStorage,
) -> None:
    """The extractor is handed these and writes to them. Storage answers where
    things go so the layout lives in one module instead of at every call site."""
    assert storage.audio_path(JOB_ID).parent == storage.job_dir(JOB_ID)
    assert storage.chunk_path(JOB_ID, 7).parent == storage.job_dir(JOB_ID) / "chunks"


def test_a_chunk_file_is_named_by_its_zero_padded_index(
    storage: FilesystemTranscriptStorage,
) -> None:
    assert storage.chunk_path(JOB_ID, 7).name == "0007.flac"


def test_asking_where_a_file_goes_creates_nothing(
    storage: FilesystemTranscriptStorage,
) -> None:
    """A path is an answer, not a side effect. The extractor owns making the file,
    and a job that was planned but never ran must not leave an empty chunks/."""
    before = sorted(path.name for path in storage.job_dir(JOB_ID).iterdir())

    storage.audio_path(JOB_ID)
    storage.chunk_path(JOB_ID, 0)

    assert sorted(p.name for p in storage.job_dir(JOB_ID).iterdir()) == before


def test_a_hostile_job_id_is_refused_before_a_path_is_built(
    storage: FilesystemTranscriptStorage,
) -> None:
    with pytest.raises(JobNotFound):
        storage.chunk_path(JobId("../../etc"), 0)


def test_the_source_media_record_round_trips(
    storage: FilesystemTranscriptStorage,
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
    storage: FilesystemTranscriptStorage,
) -> None:
    with pytest.raises(JobNotFound):
        storage.load_media(JOB_ID)


def test_a_chunk_plan_round_trips(storage: FilesystemTranscriptStorage) -> None:
    storage.save_chunk_plan(JOB_ID, a_plan())

    assert storage.load_chunk_plan(JOB_ID) == a_plan()


def test_an_unplanned_job_has_no_chunk_plan(
    storage: FilesystemTranscriptStorage,
) -> None:
    """`None`, not an error: a job legitimately has no plan until it is planned."""
    assert storage.load_chunk_plan(JOB_ID) is None


def test_a_transcript_round_trips(storage: FilesystemTranscriptStorage) -> None:
    storage.save_transcript(a_transcript())

    assert storage.load_transcript(JOB_ID) == a_transcript()


def test_a_stored_transcript_keeps_its_music_segments(
    storage: FilesystemTranscriptStorage,
) -> None:
    """Music is marked, never filtered: a musical range still points into the source
    and stays valid clip material. Persistence must not quietly apply the export's
    policy to the source of truth."""
    storage.save_transcript(a_transcript())

    restored = storage.load_transcript(JOB_ID)

    assert restored is not None
    assert [s.kind for s in restored.segments] == [
        SegmentKind.SPEECH,
        SegmentKind.MUSIC,
    ]


def test_an_unfinished_job_has_no_transcript(
    storage: FilesystemTranscriptStorage,
) -> None:
    assert storage.load_transcript(JOB_ID) is None


def test_artifacts_are_persisted(storage: FilesystemTranscriptStorage) -> None:
    storage.save_artifacts(JOB_ID, an_artifact_set())

    stored = (storage.job_dir(JOB_ID) / "artifacts.json").read_text(encoding="utf-8")
    assert decode_artifacts(stored) == an_artifact_set()


def test_the_text_export_lands_inside_the_job_directory(
    storage: FilesystemTranscriptStorage,
) -> None:
    path = storage.export_text(JOB_ID, "hola a todos")

    assert path.parent == storage.job_dir(JOB_ID)
    assert path.read_text(encoding="utf-8") == "hola a todos"


def test_exporting_text_leaves_the_structured_transcript_intact(
    storage: FilesystemTranscriptStorage,
) -> None:
    """The `.txt` is one rendering of the transcript, never a replacement for it."""
    storage.save_transcript(a_transcript())

    storage.export_text(JOB_ID, "hola a todos")

    assert storage.load_transcript(JOB_ID) == a_transcript()


def test_accented_text_survives_the_export(
    storage: FilesystemTranscriptStorage,
) -> None:
    path = storage.export_text(JOB_ID, "canción de despedida")

    assert path.read_text(encoding="utf-8") == "canción de despedida"


def test_one_jobs_transcript_is_not_visible_from_another(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.create_job(a_job(OTHER_JOB_ID))

    storage.save_transcript(a_transcript(JOB_ID))

    assert storage.load_transcript(JOB_ID) == a_transcript(JOB_ID)
    assert storage.load_transcript(OTHER_JOB_ID) is None


def test_saving_against_an_uncreated_job_is_refused(
    storage: FilesystemTranscriptStorage,
) -> None:
    """Otherwise the save creates a directory holding a transcript and no
    `job.json` — which `list_jobs` skips, so the orphan looks like nothing."""
    with pytest.raises(JobNotFound):
        storage.save_transcript(a_transcript(OTHER_JOB_ID))


def test_planning_an_uncreated_job_is_refused(
    storage: FilesystemTranscriptStorage,
) -> None:
    with pytest.raises(JobNotFound):
        storage.save_chunk_plan(OTHER_JOB_ID, a_plan(OTHER_JOB_ID))


def test_exporting_text_for_an_uncreated_job_is_refused(
    storage: FilesystemTranscriptStorage,
) -> None:
    with pytest.raises(JobNotFound):
        storage.export_text(OTHER_JOB_ID, "hola")


def test_reading_artifacts_of_an_uncreated_job_reports_no_plan(
    storage: FilesystemTranscriptStorage,
) -> None:
    """Reads stay tolerant. Only writes need the job to exist."""
    assert storage.load_chunk_plan(OTHER_JOB_ID) is None
    assert storage.load_transcript(OTHER_JOB_ID) is None
