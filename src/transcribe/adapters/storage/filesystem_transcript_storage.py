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

from pathlib import Path

from transcribe.adapters.storage.serialization import decode_job, encode_job
from transcribe.domain.errors import JobAlreadyExists, JobNotFound
from transcribe.domain.ids import InvalidIdError, JobId, make_job_id
from transcribe.domain.jobs import JobRecord

JOBS_DIRNAME = "jobs"
JOB_RECORD = "job.json"


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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
