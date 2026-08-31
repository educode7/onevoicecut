"""The persisted form must survive a round trip without losing or inventing meaning.

Resume reads back what a previous process wrote, so a codec that quietly changes a
value is indistinguishable from a corrupted disk. Two properties carry the weight
here: every field returns identical, and a payload that cannot be trusted raises a
domain error rather than leaking `json`'s.
"""

import json

import pytest

from transcribe.adapters.storage.serialization import (
    decode_chunk_plan,
    decode_job,
    encode_chunk_plan,
    encode_job,
)
from transcribe.domain.chunking import ChunkPlan, PlannedChunk
from transcribe.domain.errors import CorruptedRecord
from transcribe.domain.ids import make_job_id, make_media_id
from transcribe.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode

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
