"""`TranscriptStoragePort` over one directory per job. The only module that owns
the on-disk layout.

    {data_dir}/jobs/{job_id}/
      job.json  control.json  source.<ext>  audio.flac
      chunks/NNNN.flac  results/NNNN.json
      transcript.json  transcript.txt  artifacts.json

`data_dir` is injected rather than read from the environment here: resolving
`TRANSCRIBE_DATA_DIR` is the composition root's job, and an adapter that reads its
own configuration cannot be pointed at a `tmp_path`.

The id is validated as a ULID *before* it is joined onto a path, not resolved and
then checked for containment. A containment check answers "did we escape?" once the
path exists; this answers "is this even an id?" before anything is created, which is
the only order that holds when the value arrives from an HTTP route.
"""

import os
from pathlib import Path

from transcribe.adapters.storage.serialization import (
    decode_chunk_plan,
    decode_chunk_result,
    decode_control,
    decode_job,
    decode_transcript,
    encode_artifacts,
    encode_chunk_plan,
    encode_chunk_result,
    encode_control,
    encode_job,
    encode_transcript,
)
from transcribe.domain.chunking import ChunkPlan, ChunkResult
from transcribe.domain.errors import JobAlreadyExists, JobNotFound
from transcribe.domain.generation import GenerationResult
from transcribe.domain.ids import InvalidIdError, JobId, make_job_id
from transcribe.domain.jobs import JobRecord
from transcribe.domain.transcript import Transcript

JOBS_DIRNAME = "jobs"
JOB_RECORD = "job.json"
CONTROL = "control.json"
CHUNK_PLAN = "plan.json"
AUDIO_TRACK = "audio.flac"
CHUNKS_DIRNAME = "chunks"
RESULTS_DIRNAME = "results"
PENDING_SUFFIX = ".tmp"
TRANSCRIPT = "transcript.json"
TRANSCRIPT_TEXT = "transcript.txt"
ARTIFACTS = "artifacts.json"


class FilesystemTranscriptStorage:
    def __init__(self, data_dir: Path) -> None:
        self._jobs_root = data_dir / JOBS_DIRNAME

    def job_dir(self, job_id: JobId) -> Path:
        """The directory that holds everything belonging to one job.

        Public because the job directory is not private to persistence: the ffmpeg
        adapter is constructed against it, and it is this module that decides where
        it is.
        """
        return self._jobs_root / self._validated(job_id)

    def audio_path(self, job_id: JobId) -> Path:
        """Where the extractor writes the normalized track.

        Storage answers this rather than the caller composing it, so the layout
        stays in one module. Like `job_dir`, it computes a path and creates
        nothing — the extractor owns making the file.
        """
        return self.job_dir(job_id) / AUDIO_TRACK

    def chunk_path(self, job_id: JobId, index: int) -> Path:
        """Zero-padded so the directory sorts the way the chunks are numbered."""
        return self.job_dir(job_id) / CHUNKS_DIRNAME / f"{index:04d}.flac"

    def create_job(self, job: JobRecord) -> None:
        directory = self.job_dir(job.job_id)
        if (directory / JOB_RECORD).exists():
            raise JobAlreadyExists(f"job {job.job_id} already exists")
        directory.mkdir(parents=True, exist_ok=True)
        self._write(directory / JOB_RECORD, encode_job(job))

    def load_job(self, job_id: JobId) -> JobRecord:
        path = self.job_dir(job_id) / JOB_RECORD
        if not path.is_file():
            raise JobNotFound(f"no job stored under {job_id!r}")
        return decode_job(path.read_text(encoding="utf-8"))

    def update_job(self, job: JobRecord) -> None:
        path = self.job_dir(job.job_id) / JOB_RECORD
        if not path.is_file():
            raise JobNotFound(f"no job stored under {job.job_id!r}")
        self._write(path, encode_job(job))

    def list_jobs(self) -> tuple[JobRecord, ...]:
        """Sorted by id, which for ULIDs is already creation order.

        A directory that is not a job is skipped — a half-created job directory or
        an operator's scratch folder is not a listing failure. A job record that
        *is* there but does not decode is NOT skipped: a job silently missing from
        the list invites re-running a three-hour transcription, while a loud
        `CorruptedRecord` names the file to fix.
        """
        if not self._jobs_root.is_dir():
            return ()
        records = sorted(
            directory / JOB_RECORD
            for directory in self._jobs_root.iterdir()
            if directory.is_dir() and self._is_job_id(directory.name)
        )
        return tuple(
            decode_job(record.read_text(encoding="utf-8"))
            for record in records
            if record.is_file()
        )

    def save_chunk_plan(self, job_id: JobId, plan: ChunkPlan) -> None:
        self._write(self._writable(job_id) / CHUNK_PLAN, encode_chunk_plan(plan))

    def load_chunk_plan(self, job_id: JobId) -> ChunkPlan | None:
        payload = self._read_optional(self.job_dir(job_id) / CHUNK_PLAN)
        return None if payload is None else decode_chunk_plan(payload)

    def save_chunk_result(self, result: ChunkResult) -> None:
        """Committed by rename, because this is what resume reads.

        A chunk lands while the job is still running and the process holding it can
        die at any instruction. The next process distinguishes a committed result
        from a half-written one by the directory alone — there is no journal and no
        recovery pass — which is only true if the last step is atomic.
        """
        directory = self._writable(result.job_id) / RESULTS_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        self._write(directory / f"{result.index:04d}.json", encode_chunk_result(result))

    def load_chunk_results(self, job_id: JobId) -> tuple[ChunkResult, ...]:
        """Sorted by chunk index: a retry can commit chunk 7 after chunk 11, but the
        transcript may not be assembled in that order. A stale `.tmp` is skipped by
        the glob rather than by a check, so there is no path that forgets to."""
        directory = self.job_dir(job_id) / RESULTS_DIRNAME
        if not directory.is_dir():
            return ()
        results = [
            decode_chunk_result(path.read_text(encoding="utf-8"))
            for path in directory.glob("*.json")
        ]
        return tuple(sorted(results, key=lambda result: result.index))

    def save_transcript(self, transcript: Transcript) -> None:
        directory = self._writable(transcript.job_id)
        self._write(directory / TRANSCRIPT, encode_transcript(transcript))

    def load_transcript(self, job_id: JobId) -> Transcript | None:
        payload = self._read_optional(self.job_dir(job_id) / TRANSCRIPT)
        return None if payload is None else decode_transcript(payload)

    def save_artifacts(self, job_id: JobId, artifacts: GenerationResult) -> None:
        self._write(self._writable(job_id) / ARTIFACTS, encode_artifacts(artifacts))

    def export_text(self, job_id: JobId, text: str) -> Path:
        """Writes the derived `.txt`. `transcript.json` is untouched: the export is
        one rendering of the transcript, never a replacement for it."""
        path = self._writable(job_id) / TRANSCRIPT_TEXT
        self._write(path, text)
        return path

    def request_cancellation(self, job_id: JobId, *, requested: bool = True) -> None:
        """The web process's only way to influence a running job.

        It is a separate file on purpose. Both processes have a reason to write job
        state, which is a guaranteed race; the resolution is not a lock but an
        ownership split — while a worker is alive it is the sole writer of
        `job.json`, so a cancellation must never be expressed by editing it.
        """
        self._write(self._writable(job_id) / CONTROL, encode_control(requested))

    def cancellation_requested(self, job_id: JobId) -> bool:
        """Polled by the worker at every chunk boundary, so it writes nothing.

        An unreadable control file is reported rather than shrugged off: silently
        ignoring it turns the operator's stop button into a no-op on a job that
        runs for hours, and naming the file to delete is the more useful failure.
        """
        payload = self._read_optional(self.job_dir(job_id) / CONTROL)
        return False if payload is None else decode_control(payload)

    def _writable(self, job_id: JobId) -> Path:
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

    def _validated(self, job_id: JobId) -> str:
        try:
            return make_job_id(job_id)
        except InvalidIdError as error:
            raise JobNotFound(f"{job_id!r} is not a job id") from error

    @staticmethod
    def _is_job_id(name: str) -> bool:
        try:
            make_job_id(name)
        except InvalidIdError:
            return False
        return True

    @staticmethod
    def _write(path: Path, payload: str) -> None:
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
    def _read_optional(path: Path) -> str | None:
        """Absent means "not produced yet", a normal mid-run state for a plan or a
        transcript. The port returns `None` for both rather than raising."""
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
