"""The primitive half of the filesystem store: layout, durable writes, JSON field reads.

`FilesystemTranscriptStorage` was at once the persistence facade, the owner of
every path in the layout, and the home of the primitives under it — three reasons
to change packed into one file, and no seam for a second facade to stand on
(OQ3). This module is that primitive half, with the boundary enforced by
construction rather than by review (AB-08): nothing here names a domain type, so
signatures take `Path`, `str` and `int` only and the shared kernel stays free of
`systems.*` vocabulary no matter who builds on top.

    {data_dir}/jobs/{job_id}/
      job.json  control.json  source  audio.flac
      chunks/NNNN.flac  results/NNNN.json
      transcript.json  transcript.txt  artifacts.json

Two orders are load-bearing and live beside the code they protect: a job id is
validated as a ULID *before* it is joined onto a path — answering "is this even
an id?" before anything exists, which is the only order that holds for a value
arriving from an HTTP route — and a write is fsynced *before* the rename that
commits it, because a rename is atomic only with respect to data already
durable. Decoding is field-explicit for the same reason: an unknown key is
tolerated (an additive change must not be a one-way door), while an unknown or
missing value refuses as `CorruptedRecord` rather than being guessed.
"""

import json
import os
from enum import StrEnum
from pathlib import Path
from typing import Any, TypeVar

from onevoicecut.shared.domain.errors import CorruptedRecord, JobNotFound
from onevoicecut.shared.domain.ids import InvalidIdError, make_job_id

JOBS_DIRNAME = "jobs"
JOB_RECORD = "job.json"
CONTROL = "control.json"
HEARTBEAT = "heartbeat"
MEDIA = "media.json"
CHUNK_PLAN = "plan.json"
SOURCE = "source"
AUDIO_TRACK = "audio.flac"
CHUNKS_DIRNAME = "chunks"
RESULTS_DIRNAME = "results"
RENDER_DIRNAME = "render"
# A name distinct from `{profile}.json`, `.cmds`, `.ass` and `PENDING_SUFFIX`:
# `load_clip_exports`'s `directory.glob("*.json")` must never pick this up,
# and `_export_from_trajectory` writes its sidecars one directory over, under
# `render/{profile}/`, never under `render/{clip_id}/` where this lives.
RENDER_CLAIM = "claim"
PENDING_SUFFIX = ".tmp"
TRANSCRIPT = "transcript.json"
TRANSCRIPT_TEXT = "transcript.txt"
ARTIFACTS = "artifacts.json"

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


def _optional_number(record: Record, key: str) -> float | None:
    return None if _field(record, key) is None else _number(record, key)


def _flag(record: Record, key: str) -> bool:
    value = _field(record, key)
    if not isinstance(value, bool):
        raise CorruptedRecord(f"field {key!r} is not a boolean")
    return value


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


class StorageCore:
    """Where things go, how a write becomes durable, and whether a name is an id.

    No domain type crosses this boundary: a facade owns the vocabulary, this
    owns the bytes.
    """

    def __init__(self, data_dir: Path) -> None:
        self._jobs_root = data_dir / JOBS_DIRNAME

    @property
    def jobs_root(self) -> Path:
        """Where every job directory lives. A listing facade scopes to it."""
        return self._jobs_root

    def job_dir(self, job_id: str) -> Path:
        """The directory that holds everything belonging to one job.

        Public because the job directory is not private to persistence: the ffmpeg
        adapter is constructed against it, and it is this module that decides where
        it is.
        """
        return self._jobs_root / self.validated_job_id(job_id)

    def source_path(self, job_id: str) -> Path:
        """Extensionless by design.

        The layout in the design sketch said `source.<ext>`, but the extension was
        never load-bearing: content type is validated by `ffprobe`, never by a
        suffix. Keeping it out removes the last place a client-supplied filename
        could reach a path at all.
        """
        return self.job_dir(job_id) / SOURCE

    def audio_path(self, job_id: str) -> Path:
        """Where the extractor writes the normalized track.

        Storage answers this rather than the caller composing it, so the layout
        stays in one module. Like `job_dir`, it computes a path and creates
        nothing — the extractor owns making the file.
        """
        return self.job_dir(job_id) / AUDIO_TRACK

    def chunk_path(self, job_id: str, index: int) -> Path:
        """Zero-padded so the directory sorts the way the chunks are numbered."""
        return self.job_dir(job_id) / CHUNKS_DIRNAME / f"{index:04d}.flac"

    def writable(self, job_id: str) -> Path:
        """The job directory, but only once the job record is really there.

        Writes are strict where reads are tolerant. A save against a job that was
        never created would leave a directory holding a transcript and no
        `job.json`, and `list_jobs` skips exactly that shape — so the orphan would
        be invisible rather than merely wrong.
        """
        directory = self.job_dir(job_id)
        if not (directory / JOB_RECORD).is_file():
            raise JobNotFound(f"no job stored under {job_id!r}")
        return directory

    def validated_job_id(self, job_id: str) -> str:
        try:
            return make_job_id(job_id)
        except InvalidIdError as error:
            raise JobNotFound(f"{job_id!r} is not a job id") from error

    @staticmethod
    def is_job_id(name: str) -> bool:
        try:
            make_job_id(name)
        except InvalidIdError:
            return False
        return True

    @staticmethod
    def write_atomic(path: Path, payload: str) -> None:
        """Write to a sibling `.tmp`, force it to disk, then rename onto the target.

        Every write goes through this, not only `save_chunk_result`: a torn
        `job.json` is no more survivable than a torn chunk result, and the worker
        rewrites it at every state transition.

        The `fsync` is not decoration. A rename is atomic with respect to what is
        already durable, so renaming a file whose bytes are still in the page cache
        commits a name and not the data behind it.

        `os.replace` rather than `os.rename` because on Windows a rename onto an
        existing destination fails — and an existing destination is exactly the
        retry case. A leftover `.tmp` from a crash is simply overwritten here, and
        ignored by every reader, which is what makes resume correct rather than
        hopeful.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        pending = path.with_name(path.name + PENDING_SUFFIX)
        with open(pending, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending, path)

    @staticmethod
    def read_optional(path: Path) -> str | None:
        """Absent means "not produced yet", a normal mid-run state for a plan or a
        transcript. The port returns `None` for both rather than raising."""
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
