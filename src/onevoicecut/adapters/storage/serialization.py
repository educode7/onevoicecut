"""JSON codecs for the persisted records, re-exported from the facades that own them.

Kept apart from the adapter that touches the disk, and kept pure, so that what the
on-disk format *means* is proven without a filesystem: reading a file and trusting
its contents are two different claims, and only the second one lives in a codec.

Decoding is explicit field by field rather than reflective. A generic
`Entity(**payload)` would accept whatever a previous version happened to write and
then fail somewhere far away; here a payload that no longer matches the entity is
rejected at the boundary as `CorruptedRecord`. That matters because resume reads
files written by an older process that may have died mid-write.

Encoding is `asdict` because the entities are the schema, and `StrEnum` members
serialize as their own values.

**No codec is defined here any more.** Each half moved out with the facade that
writes it — the job record with its media and control files to `JobStore`
(slice 2b), the chunk plan, chunk result and transcript to `TranscriptStore`
(slice 3b), and the artifacts and per-profile exports to `ClipStore` (slice 4b) —
because a codec taking a domain type can live neither in the domain-agnostic core
(AB-08: no `systems.*` vocabulary crosses into `shared`) nor in a module that does
not own that type. What remains is the re-export seam (`X as X`, for mypy's
`no_implicit_reexport`) that every historical importer still resolves through:
`filesystem_transcript_storage`, and the tests that pin the on-disk format. Slice
4f retires this module along with the monolith it serves. It never re-implements
a codec, and a facade reaching back into the adapter it exists to replace would
break the day that adapter went.
"""

from onevoicecut.systems.pipeline.clips.infrastructure.storage.clip_store import (
    decode_artifacts as decode_artifacts,
    decode_clip_export as decode_clip_export,
    encode_artifacts as encode_artifacts,
    encode_clip_export as encode_clip_export,
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
