"""The persisted form must survive a round trip without losing or inventing meaning.

Resume reads back what a previous process wrote, so a codec that quietly changes a
value is indistinguishable from a corrupted disk. Two properties carry the weight
here: every field returns identical, and a payload that cannot be trusted raises a
domain error rather than leaking `json`'s.
"""

import json
from pathlib import Path

import pytest

from transcribe.adapters.storage.serialization import (
    decode_artifacts,
    decode_chunk_plan,
    decode_chunk_result,
    decode_job,
    decode_media,
    decode_transcript,
    encode_artifacts,
    encode_chunk_plan,
    encode_chunk_result,
    encode_job,
    encode_media,
    encode_transcript,
)
from transcribe.domain.media import SourceMedia
from transcribe.domain.chunking import ChunkPlan, ChunkResult, ChunkState, PlannedChunk
from transcribe.domain.errors import CorruptedRecord
from transcribe.domain.generation import ClipCandidate, GenerationResult, ScriptVariant
from transcribe.domain.ids import make_job_id, make_media_id
from transcribe.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from transcribe.domain.transcript import SegmentKind, Transcript, TranscriptSegment

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")


def a_job() -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=JobState.TRANSCRIBING,
        speaker_mode=SpeakerMode.MULTI,
        engine=EngineChoice.CLOUD,
        created_at=1723501234.5,
        updated_at=1723501999.25,
        worker_pid=4812,
        error=None,
    )


def a_plan() -> ChunkPlan:
    return ChunkPlan(
        job_id=JOB_ID,
        stride_s=600.0,
        overlap_s=5.0,
        chunks=(
            PlannedChunk(index=0, start_s=0.0, end_s=605.0),
            PlannedChunk(index=1, start_s=600.0, end_s=1205.0),
        ),
    )


def a_segment(kind: SegmentKind = SegmentKind.SPEECH) -> TranscriptSegment:
    return TranscriptSegment(
        start_s=12.25,
        end_s=18.5,
        text="hola a todos, gracias por acompañarme",
        speaker="SPEAKER_01",
        confidence=0.93,
        kind=kind,
    )


def a_chunk_result() -> ChunkResult:
    return ChunkResult(
        job_id=JOB_ID,
        index=42,
        state=ChunkState.DONE,
        segments=(a_segment(), a_segment(SegmentKind.MUSIC)),
        engine_id="faster-whisper/large-v3",
        attempts=2,
        error=None,
        finished_at=1723501999.25,
    )


def a_transcript() -> Transcript:
    return Transcript(
        job_id=JOB_ID,
        segments=(a_segment(), a_segment(SegmentKind.UNCERTAIN)),
        engine_id="faster-whisper/large-v3",
        diarized=True,
        language="es",
    )


def an_artifact_set() -> GenerationResult:
    return GenerationResult(
        job_id=JOB_ID,
        summary="Resumen del mensaje.",
        clip_candidates=(
            ClipCandidate(
                start_s=100.0,
                end_s=145.5,
                hook="El momento clave",
                quote="una cita textual",
                rationale="cierra una idea completa",
                score=0.82,
                variants=(
                    ScriptVariant(
                        target="tiktok",
                        format="vertical",
                        body="guion corto",
                        duration_target_s=45.0,
                    ),
                ),
            ),
        ),
    )


def test_source_media_round_trips_with_its_path_intact() -> None:
    """The only persisted entity carrying a `Path`. JSON has no path type, so it
    is stored as text and rebuilt — guessing at load time would be worse."""
    media = SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicación del domingo.mp4",
        stored_path=Path("jobs") / JOB_ID / "source.mp4",
        size_bytes=4096,
        container="mp4",
        checksum="deadbeef",
    )

    restored = decode_media(encode_media(media))

    assert restored == media
    assert isinstance(restored.stored_path, Path)


def test_a_media_record_with_a_broken_id_raises_a_domain_error() -> None:
    payload = json.loads(
        encode_media(
            SourceMedia(
                media_id=MEDIA_ID,
                original_filename="a.mp4",
                stored_path=Path("a.mp4"),
                size_bytes=1,
                container="mp4",
                checksum="x",
            )
        )
    )
    payload["media_id"] = "not-a-ulid"

    with pytest.raises(CorruptedRecord):
        decode_media(json.dumps(payload))


def test_a_job_record_round_trips_unchanged() -> None:
    assert decode_job(encode_job(a_job())) == a_job()


def test_a_job_record_keeps_its_nullable_fields_distinct_from_defaults() -> None:
    failed = JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=JobState.FAILED,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=2.0,
        worker_pid=None,
        error="ffmpeg exited 1",
    )

    restored = decode_job(encode_job(failed))

    assert restored.worker_pid is None
    assert restored.error == "ffmpeg exited 1"


def test_a_chunk_plan_round_trips_with_its_chunks_as_a_tuple() -> None:
    restored = decode_chunk_plan(encode_chunk_plan(a_plan()))

    assert restored == a_plan()
    # A list would already compare unequal above, but assert the type outright:
    # the domain contract is a tuple, and a list would be silently mutable.
    assert isinstance(restored.chunks, tuple)


def test_chunk_boundaries_survive_as_exact_floats() -> None:
    """A boundary is an address into the source; a rounded one points elsewhere."""
    plan = ChunkPlan(
        job_id=JOB_ID,
        stride_s=600.0,
        overlap_s=5.0,
        chunks=(PlannedChunk(index=0, start_s=3599.999, end_s=3600.001),),
    )

    restored = decode_chunk_plan(encode_chunk_plan(plan))

    assert restored.chunks[0].start_s == 3599.999
    assert restored.chunks[0].end_s == 3600.001


def test_a_chunk_result_round_trips_unchanged() -> None:
    restored = decode_chunk_result(encode_chunk_result(a_chunk_result()))

    assert restored == a_chunk_result()
    assert isinstance(restored.segments, tuple)


def test_a_pending_chunk_result_keeps_its_absent_finish_time() -> None:
    """`finished_at is None` is what marks a chunk as not yet done. Resume reads it."""
    unfinished = ChunkResult(
        job_id=JOB_ID,
        index=7,
        state=ChunkState.FAILED,
        segments=(),
        engine_id="faster-whisper/large-v3",
        attempts=3,
        error="timed out",
        finished_at=None,
    )

    restored = decode_chunk_result(encode_chunk_result(unfinished))

    assert restored == unfinished
    assert restored.finished_at is None


@pytest.mark.parametrize("kind", list(SegmentKind))
def test_every_segment_kind_survives_the_round_trip(kind: SegmentKind) -> None:
    """The classification axis must never drift across persistence.

    An `UNCERTAIN` segment that reloads as `SPEECH` is exactly the silent
    degradation the domain forbids at the ASR boundary. A codec is no more
    entitled to assert confirmed speech than a non-classifying adapter is.
    """
    result = ChunkResult(
        job_id=JOB_ID,
        index=0,
        state=ChunkState.DONE,
        segments=(a_segment(kind),),
        engine_id="e",
        attempts=1,
        error=None,
        finished_at=None,
    )

    restored = decode_chunk_result(encode_chunk_result(result))

    assert restored.segments[0].kind is kind


def test_a_segment_without_a_speaker_or_confidence_round_trips() -> None:
    """What a non-diarizing engine produces. `None` must not become `"None"`."""
    bare = TranscriptSegment(
        start_s=0.0, end_s=1.0, text="t", speaker=None, confidence=None
    )
    transcript = Transcript(
        job_id=JOB_ID, segments=(bare,), engine_id="e", diarized=False
    )

    restored = decode_transcript(encode_transcript(transcript))

    assert restored.segments[0].speaker is None
    assert restored.segments[0].confidence is None
    assert restored.diarized is False


def test_a_transcript_round_trips_unchanged() -> None:
    assert decode_transcript(encode_transcript(a_transcript())) == a_transcript()


def test_accented_spanish_text_round_trips_unescaped() -> None:
    """The source language is Spanish; the stored file stays readable."""
    payload = encode_transcript(a_transcript())

    assert "acompañarme" in payload
    assert decode_transcript(payload).segments[0].text.endswith("acompañarme")


def test_artifacts_round_trip_through_their_nested_variants() -> None:
    restored = decode_artifacts(encode_artifacts(an_artifact_set()))

    assert restored == an_artifact_set()
    assert isinstance(restored.clip_candidates[0].variants, tuple)


def test_malformed_json_raises_a_domain_error() -> None:
    with pytest.raises(CorruptedRecord):
        decode_job("{not json")


def test_a_json_document_that_is_not_an_object_raises_a_domain_error() -> None:
    with pytest.raises(CorruptedRecord):
        decode_job("[]")


def test_a_missing_field_raises_a_domain_error() -> None:
    payload = json.loads(encode_job(a_job()))
    del payload["engine"]

    with pytest.raises(CorruptedRecord):
        decode_job(json.dumps(payload))


def test_an_unknown_enum_value_raises_a_domain_error() -> None:
    payload = json.loads(encode_job(a_job()))
    payload["state"] = "levitating"

    with pytest.raises(CorruptedRecord):
        decode_job(json.dumps(payload))


def test_a_job_id_that_is_not_a_ulid_raises_a_domain_error() -> None:
    """The id is about to become a path component. It is validated on the way in."""
    payload = json.loads(encode_job(a_job()))
    payload["job_id"] = "../../etc/passwd"

    with pytest.raises(CorruptedRecord):
        decode_job(json.dumps(payload))


def test_a_boolean_is_not_accepted_where_a_number_belongs() -> None:
    """`bool` is an `int` in Python, so `True` would otherwise persist as 1.0."""
    payload = json.loads(encode_job(a_job()))
    payload["created_at"] = True

    with pytest.raises(CorruptedRecord):
        decode_job(json.dumps(payload))


def test_a_wrongly_typed_collection_raises_a_domain_error() -> None:
    payload = json.loads(encode_chunk_plan(a_plan()))
    payload["chunks"] = "not a list of chunks"

    with pytest.raises(CorruptedRecord):
        decode_chunk_plan(json.dumps(payload))


def test_an_unknown_segment_kind_raises_rather_than_defaulting() -> None:
    """`SegmentKind` defaults to `UNCERTAIN` in the entity, but a stored value that
    is not a known kind means the file is wrong, not that the audio was unclear."""
    payload = json.loads(encode_chunk_result(a_chunk_result()))
    payload["segments"][0]["kind"] = "whistling"

    with pytest.raises(CorruptedRecord):
        decode_chunk_result(json.dumps(payload))


def test_a_missing_segment_field_raises_a_domain_error() -> None:
    payload = json.loads(encode_transcript(a_transcript()))
    del payload["segments"][1]["start_s"]

    with pytest.raises(CorruptedRecord):
        decode_transcript(json.dumps(payload))
