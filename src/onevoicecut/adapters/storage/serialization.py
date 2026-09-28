"""JSON codec for the persisted job aggregate.

Kept apart from the adapter that touches the disk, and kept pure, so that what the
on-disk format *means* is proven without a filesystem: reading a file and trusting
its contents are two different claims, and only the second one lives here.

Decoding is explicit field by field rather than reflective. A generic
`Entity(**payload)` would accept whatever a previous version happened to write and
then fail somewhere far away; here a payload that no longer matches the entity is
rejected at the boundary as `CorruptedRecord`. That matters because resume reads
files written by an older process that may have died mid-write.

Encoding is `asdict` because the entities are the schema, and `StrEnum` members
serialize as their own values.

**`ClipExport` is the first record carrying a `Path`, and it is converted by
hand.** `asdict` leaves a `Path` intact and `json.dumps` then refuses it, so the
one field is spelled out rather than left to a default encoder: a codec that
silently stringified any unknown object would accept the next unserialisable type
too, and this module exists to reject a payload at the boundary instead of far
away. It decodes back through `Path`, so a record written on one platform reads
as that platform's flavour rather than as text.
"""

from dataclasses import asdict
from pathlib import Path

from onevoicecut.shared.domain.errors import CorruptedRecord
from onevoicecut.systems.pipeline.clips.domain.framing import TrackingConfidence
from onevoicecut.systems.pipeline.clips.domain.generation import ClipCandidate, GenerationResult, ScriptVariant
from onevoicecut.shared.domain.ids import (
    ClipId,
    InvalidIdError,
    JobId,
    make_clip_id,
    make_job_id,
)
from onevoicecut.shared.infrastructure.storage.core import (
    Record,
    _dumps,
    _field,
    _id_field,
    _loads,
    _member,
    _number,
    _objects,
    _optional_text,
    _text,
)
# The six codecs that touch `job.json`, `media.json` and `control.json` live
# beside the jobs facade (OQ3), re-exported here (`X as X` for mypy's
# `no_implicit_reexport`) so every historical importer keeps resolving until
# slice 4f retires this module's monolith. This file never re-implements them.
from onevoicecut.systems.pipeline.jobs.infrastructure.storage.job_store import (
    decode_control as decode_control,
    decode_job as decode_job,
    decode_media as decode_media,
    encode_control as encode_control,
    encode_job as encode_job,
    encode_media as encode_media,
)
# The transcripts half moved the same way with slice 3b: plan, chunk result and
# transcript codecs live beside `FilesystemTranscriptStore`, because a codec
# taking `ChunkResult` belongs to the module whose domain type it names.
from onevoicecut.systems.pipeline.transcripts.infrastructure.storage.transcript_store import (
    decode_chunk_plan as decode_chunk_plan,
    decode_chunk_result as decode_chunk_result,
    decode_transcript as decode_transcript,
    encode_chunk_plan as encode_chunk_plan,
    encode_chunk_result as encode_chunk_result,
    encode_transcript as encode_transcript,
)
from onevoicecut.systems.pipeline.clips.domain.rendering import (
    CaptionCoverage,
    ClipExport,
    ClipState,
    DurationCompliance,
    DurationComplianceKind,
    OutputQuality,
    OutputQualityKind,
    RenderedClip,
    SubtitleTimingSource,
)

def _job_id(record: Record) -> JobId:
    """Validated here because the value is about to become a path component."""
    return _id_field(record, "job_id", make_job_id)


def encode_artifacts(artifacts: GenerationResult) -> str:
    return _dumps(asdict(artifacts))


def decode_artifacts(payload: str) -> GenerationResult:
    record = _loads(payload)
    return GenerationResult(
        job_id=_job_id(record),
        summary=_text(record, "summary"),
        clip_candidates=tuple(
            ClipCandidate(
                start_s=_number(item, "start_s"),
                end_s=_number(item, "end_s"),
                hook=_text(item, "hook"),
                quote=_text(item, "quote"),
                rationale=_text(item, "rationale"),
                score=_number(item, "score"),
                variants=tuple(
                    ScriptVariant(
                        target=_text(variant, "target"),
                        format=_text(variant, "format"),
                        body=_text(variant, "body"),
                        duration_target_s=_number(variant, "duration_target_s"),
                    )
                    for variant in _objects(item, "variants")
                ),
            )
            for item in _objects(record, "clip_candidates")
        ),
    )


def _clip_id(record: Record) -> ClipId:
    """Validated here for the reason `_job_id` is: it is about to become a path
    component, and a stored record is not a trusted one."""
    return _id_field(record, "clip_id", make_clip_id)


def encode_clip_export(export: ClipExport) -> str:
    record = asdict(export)
    if export.clip is not None:
        record["clip"]["path"] = str(export.clip.path)
    return _dumps(record)


def decode_clip_export(payload: str) -> ClipExport:
    record = _loads(payload)
    return ClipExport(
        job_id=_job_id(record),
        clip_id=_clip_id(record),
        clip=_rendered_clip(record),
        failure=_optional_text(record, "failure"),
        profile=_text(record, "profile"),
        source_start_s=_number(record, "source_start_s"),
        source_end_s=_number(record, "source_end_s"),
        title=_text(record, "title"),
        description=_text(record, "description"),
        variants=tuple(
            ScriptVariant(
                target=_text(variant, "target"),
                format=_text(variant, "format"),
                body=_text(variant, "body"),
                duration_target_s=_number(variant, "duration_target_s"),
            )
            for variant in _objects(record, "variants")
        ),
        state=_member(record, "state", ClipState),
    )


def _rendered_clip(record: Record) -> RenderedClip | None:
    """`None` is a render that was refused, not a corrupted record.

    A refusal has no file and no honest value for any of the four declarations,
    so the absence is the fact being stored rather than a field that went
    missing -- which is why this is the codec's second key-tolerant read, and
    why it still validates every field once a clip is actually present.
    """
    clip = record.get("clip")
    if clip is None:
        return None
    if not isinstance(clip, dict):
        raise CorruptedRecord("field 'clip' is not an object")
    quality = _field(clip, "quality")
    if not isinstance(quality, dict):
        raise CorruptedRecord("field 'quality' is not an object")
    duration = _field(clip, "duration")
    if not isinstance(duration, dict):
        raise CorruptedRecord("field 'duration' is not an object")
    return RenderedClip(
        clip_id=_clip_id(clip),
        job_id=_job_id(clip),
        path=Path(_text(clip, "path")),
        source_start_s=_number(clip, "source_start_s"),
        source_end_s=_number(clip, "source_end_s"),
        quality=OutputQuality(
            kind=_member(quality, "kind", OutputQualityKind),
            factor=_number(quality, "factor"),
        ),
        subtitle_timing=_member(clip, "subtitle_timing", SubtitleTimingSource),
        captions=_member(clip, "captions", CaptionCoverage),
        tracking=_member(clip, "tracking", TrackingConfidence),
        duration=DurationCompliance(
            kind=_member(duration, "kind", DurationComplianceKind),
            overrun_s=_number(duration, "overrun_s"),
        ),
    )
