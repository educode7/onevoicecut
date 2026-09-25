"""`JobStore` over the shared filesystem core: the jobs half of the persistence
boundary (OQ3), with a real filesystem behind it.

The interface lives in `jobs/domain/interfaces/job_store.py` and is imported by
nobody here — satisfaction is structural, so this class binds to it without the
domain ever learning that a filesystem exists. What this module does import is
the jobs domain and the shared `core`, and nothing else: the layout, the atomic
rename and the JSON field reads all live in `core`, while the jobs records' own
codec lives beside the methods that write it, because a codec taking `JobRecord`
can live neither in the domain-agnostic core (AB-08: no `systems.*` vocabulary
crosses into `shared`) nor in `adapters/storage`, which this facade is the start
of replacing.

The codec functions are therefore defined here and re-exported from
`adapters.storage.serialization` (`X as X`, the same seam slice 2a used) so the
monolith keeps resolving them until slice 4f retires it — a facade reaching back
into the adapter it exists to replace would break the day that adapter went.

`core` is injected rather than built from `data_dir`: a composition root
constructs it once and hands the same object to every facade, so one process has
exactly one owner of where things go. `runtime/` keeps constructing the monolith,
which satisfies `JobStore` structurally, until slice 4f.
"""

from dataclasses import asdict
from pathlib import Path

from onevoicecut.shared.domain.errors import (
    CorruptedRecord,
    JobAlreadyExists,
    JobNotFound,
)
from onevoicecut.shared.domain.ids import (
    InvalidIdError,
    JobId,
    MediaId,
    OperatorId,
    make_job_id,
    make_media_id,
    make_operator_id,
)
from onevoicecut.shared.infrastructure.storage.core import (
    CONTROL,
    HEARTBEAT,
    JOB_RECORD,
    MEDIA,
    Record,
    StorageCore,
    _dumps,
    _flag,
    _id_field,
    _loads,
    _member,
    _number,
    _optional_text,
    _optional_whole,
    _text,
    _whole,
)
from onevoicecut.systems.pipeline.jobs.domain.jobs import (
    EngineChoice,
    JobRecord,
    JobState,
    SpeakerMode,
)
from onevoicecut.systems.pipeline.jobs.domain.media import SourceMedia


class FilesystemJobStore:
    def __init__(self, core: StorageCore) -> None:
        self._core = core

    def job_dir(self, job_id: JobId) -> Path:
        """The directory that holds everything belonging to one job.

        Public because the job directory is not private to persistence: the ffmpeg
        adapter is constructed against it, and it is `core` that decides where it is.
        """
        return self._core.job_dir(job_id)

    def source_path(self, job_id: JobId) -> Path:
        """Extensionless by design.

        The layout in the design sketch said `source.<ext>`, but the extension was
        never load-bearing: content type is validated by `ffprobe`, never by a
        suffix. Keeping it out removes the last place a client-supplied filename
        could reach a path at all.
        """
        return self._core.source_path(job_id)

    def create_job(self, job: JobRecord) -> None:
        directory = self.job_dir(job.job_id)
        if (directory / JOB_RECORD).exists():
            raise JobAlreadyExists(f"job {job.job_id} already exists")
        directory.mkdir(parents=True, exist_ok=True)
        self._core.write_atomic(directory / JOB_RECORD, encode_job(job))

    def load_job(self, job_id: JobId) -> JobRecord:
        path = self.job_dir(job_id) / JOB_RECORD
        if not path.is_file():
            raise JobNotFound(f"no job stored under {job_id!r}")
        return decode_job(path.read_text(encoding="utf-8"))

    def update_job(self, job: JobRecord) -> None:
        path = self.job_dir(job.job_id) / JOB_RECORD
        if not path.is_file():
            raise JobNotFound(f"no job stored under {job.job_id!r}")
        self._core.write_atomic(path, encode_job(job))

    def list_jobs(self) -> tuple[JobRecord, ...]:
        """Sorted by id, which for ULIDs is already creation order.

        A directory that is not a job is skipped — a half-created job directory or
        an operator's scratch folder is not a listing failure. A job record that
        *is* there but does not decode is NOT skipped: a job silently missing from
        the list invites re-running a three-hour transcription, while a loud
        `CorruptedRecord` names the file to fix.
        """
        if not self._core.jobs_root.is_dir():
            return ()
        records = sorted(
            directory / JOB_RECORD
            for directory in self._core.jobs_root.iterdir()
            if directory.is_dir() and self._core.is_job_id(directory.name)
        )
        return tuple(
            decode_job(record.read_text(encoding="utf-8"))
            for record in records
            if record.is_file()
        )

    def save_media(self, job_id: JobId, media: SourceMedia) -> None:
        self._core.write_atomic(self._core.writable(job_id) / MEDIA, encode_media(media))

    def load_media(self, job_id: JobId) -> SourceMedia:
        path = self.job_dir(job_id) / MEDIA
        if not path.is_file():
            raise JobNotFound(f"no source media recorded for {job_id!r}")
        return decode_media(path.read_text(encoding="utf-8"))

    def write_heartbeat(self, job_id: JobId, *, at_s: float) -> None:
        """The worker saying it is still working, not merely still running.

        Written through the same atomic path as everything else: a torn
        heartbeat that read as fresh would vouch for a worker using bytes that
        were never fully written.

        Nobody ever removes this file. After a job finishes it is inert —
        liveness is only ever asked about worker-bound states — and removal
        would buy a writer-and-cleaner pair for no correctness gain.
        """
        self._core.write_atomic(self._core.writable(job_id) / HEARTBEAT, repr(float(at_s)))

    def heartbeat_is_fresh(
        self, job_id: JobId, *, now_s: float, stale_after_s: float
    ) -> bool:
        """Fails closed on absent, torn, or unreadable content.

        The asymmetry is deliberate. Wrongly believing a worker is alive orphans
        the job forever — nothing reconciles a record it thinks is healthy.
        Wrongly believing it is dead costs a re-run that resumes from the chunks
        already committed. So anything short of a readable number is "not
        fresh".

        A timestamp from the future reads as fresh: the difference goes negative,
        which is under any positive bound. Clock skew should not orphan a job
        that is plainly working, and the pid check is what establishes the
        process exists at all.
        """
        raw = self._core.read_optional(self.job_dir(job_id) / HEARTBEAT)
        if raw is None:
            return False
        try:
            written_at = float(raw)
        except ValueError:
            return False
        return now_s - written_at <= stale_after_s

    def request_cancellation(self, job_id: JobId, *, requested: bool = True) -> None:
        """The web process's only way to influence a running job.

        It is a separate file on purpose. Both processes have a reason to write job
        state, which is a guaranteed race; the resolution is not a lock but an
        ownership split — while a worker is alive it is the sole writer of
        `job.json`, so a cancellation must never be expressed by editing it.
        """
        self._core.write_atomic(self._core.writable(job_id) / CONTROL, encode_control(requested))

    def cancellation_requested(self, job_id: JobId) -> bool:
        """Polled by the worker at every chunk boundary, so it writes nothing.

        An unreadable control file is reported rather than shrugged off: silently
        ignoring it turns the operator's stop button into a no-op on a job that
        runs for hours, and naming the file to delete is the more useful failure.
        """
        payload = self._core.read_optional(self.job_dir(job_id) / CONTROL)
        return False if payload is None else decode_control(payload)


def _job_id(record: Record) -> JobId:
    """Validated here because the value is about to become a path component."""
    return _id_field(record, "job_id", make_job_id)


def _media_id(record: Record) -> MediaId:
    return _id_field(record, "media_id", make_media_id)


def _optional_operator(record: Record) -> OperatorId | None:
    """The codec's one key-tolerant read.

    Every other field is required at decode because an older build could not
    legitimately omit it; `owner` is the exception, because records written
    before this change genuinely lack the key. Absent or null → no owner.
    A present value is validated like any identity: anything else fails
    closed as corruption, never coerced to `None` and never invented —
    a silent owner is a security decision the codec has no business making.
    """
    value = record.get("owner")
    if value is None:
        return None
    if not isinstance(value, str):
        raise CorruptedRecord("field 'owner' is not a string")
    try:
        return make_operator_id(value)
    except InvalidIdError as error:
        raise CorruptedRecord(str(error)) from error


def encode_control(cancel_requested: bool) -> str:
    """The control file is not a domain entity — it is a message from the web
    process to the worker — but it is still persistence, so its shape lives here
    rather than as raw `json` inside the adapter."""
    return _dumps({"cancel_requested": cancel_requested})


def decode_control(payload: str) -> bool:
    return _flag(_loads(payload), "cancel_requested")


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
        owner=_optional_operator(record),
    )


def encode_media(media: SourceMedia) -> str:
    payload = asdict(media)
    # The one persisted entity carrying a `Path`. Stored as text and read back as
    # a `Path`, because JSON has no path type and guessing at load time is worse.
    payload["stored_path"] = str(media.stored_path)
    return _dumps(payload)


def decode_media(payload: str) -> SourceMedia:
    record = _loads(payload)
    return SourceMedia(
        media_id=_media_id(record),
        original_filename=_text(record, "original_filename"),
        stored_path=Path(_text(record, "stored_path")),
        size_bytes=_whole(record, "size_bytes"),
        container=_text(record, "container"),
        checksum=_text(record, "checksum"),
    )
