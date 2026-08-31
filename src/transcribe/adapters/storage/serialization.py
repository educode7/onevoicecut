"""JSON codec for the persisted job aggregate.

Kept apart from the adapter that touches the disk, and kept pure, so that what the
on-disk format *means* is proven without a filesystem: reading a file and trusting
its contents are two different claims, and only the second one lives here.

Decoding is explicit field by field rather than reflective. A generic
`Entity(**payload)` would accept whatever a previous version happened to write and
then fail somewhere far away; here a payload that no longer matches the entity is
rejected at the boundary as `CorruptedRecord`. That matters because resume reads
files written by an older process that may have died mid-write.

Encoding is `asdict` because the entities are the schema. `StrEnum` members
serialize as their own values, and no persisted entity carries a `Path` — the one
type that would not survive.
"""

import json
from dataclasses import asdict
from enum import StrEnum
from typing import Any, TypeVar

from transcribe.domain.chunking import ChunkPlan, PlannedChunk
from transcribe.domain.errors import CorruptedRecord
from transcribe.domain.ids import (
    InvalidIdError,
    JobId,
    MediaId,
    make_job_id,
    make_media_id,
)
from transcribe.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode

Record = dict[str, Any]

_EnumMember = TypeVar("_EnumMember", bound=StrEnum)


def _dumps(payload: Record) -> str:
    # `ensure_ascii=False` because the source language is Spanish: escaping every
    # accented character inflates a multi-hour transcript and makes the file
    # unreadable in exactly the situation you open it — debugging a failed job.
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _loads(payload: str) -> Record:
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as error:
        raise CorruptedRecord(f"not valid JSON: {error}") from error
    if not isinstance(decoded, dict):
        raise CorruptedRecord(f"expected a JSON object, found {type(decoded).__name__}")
    return decoded


def _field(record: Record, key: str) -> Any:
    if key not in record:
        raise CorruptedRecord(f"missing field {key!r}")
    return record[key]


def _text(record: Record, key: str) -> str:
    value = _field(record, key)
    if not isinstance(value, str):
        raise CorruptedRecord(f"field {key!r} is not a string")
    return value


def _number(record: Record, key: str) -> float:
    value = _field(record, key)
    # `bool` is an `int`, so without this a `true` would persist as a timestamp.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CorruptedRecord(f"field {key!r} is not a number")
    return float(value)


def _whole(record: Record, key: str) -> int:
    value = _field(record, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise CorruptedRecord(f"field {key!r} is not an integer")
    return value


def _optional_text(record: Record, key: str) -> str | None:
    return None if _field(record, key) is None else _text(record, key)


def _optional_whole(record: Record, key: str) -> int | None:
    return None if _field(record, key) is None else _whole(record, key)


def _member(record: Record, key: str, enum: type[_EnumMember]) -> _EnumMember:
    value = _text(record, key)
    try:
        return enum(value)
    except ValueError as error:
        raise CorruptedRecord(f"{value!r} is not a known {enum.__name__}") from error


def _objects(record: Record, key: str) -> list[Record]:
    value = _field(record, key)
    if not isinstance(value, list) or not all(isinstance(i, dict) for i in value):
        raise CorruptedRecord(f"field {key!r} is not a list of objects")
    return value


def _job_id(record: Record) -> JobId:
    """Validated here because the value is about to become a path component."""
    try:
        return make_job_id(_text(record, "job_id"))
    except InvalidIdError as error:
        raise CorruptedRecord(str(error)) from error


def _media_id(record: Record) -> MediaId:
    try:
        return make_media_id(_text(record, "media_id"))
    except InvalidIdError as error:
        raise CorruptedRecord(str(error)) from error


def encode_job(job: JobRecord) -> str:
    return _dumps(asdict(job))


def decode_job(payload: str) -> JobRecord:
    record = _loads(payload)
    return JobRecord(
        job_id=_job_id(record),
        media_id=_media_id(record),
        state=_member(record, "state", JobState),
        speaker_mode=_member(record, "speaker_mode", SpeakerMode),
        engine=_member(record, "engine", EngineChoice),
        created_at=_number(record, "created_at"),
        updated_at=_number(record, "updated_at"),
        worker_pid=_optional_whole(record, "worker_pid"),
        error=_optional_text(record, "error"),
    )


def encode_chunk_plan(plan: ChunkPlan) -> str:
    return _dumps(asdict(plan))


def decode_chunk_plan(payload: str) -> ChunkPlan:
    record = _loads(payload)
    return ChunkPlan(
        job_id=_job_id(record),
        stride_s=_number(record, "stride_s"),
        overlap_s=_number(record, "overlap_s"),
        chunks=tuple(
            PlannedChunk(
                index=_whole(item, "index"),
                start_s=_number(item, "start_s"),
                end_s=_number(item, "end_s"),
            )
            for item in _objects(record, "chunks")
        ),
    )
