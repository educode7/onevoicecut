"""`TranscriptStoragePort` over one directory per job: the domain-typed facade
over the layout primitives in `shared/infrastructure/storage/core`.

    {data_dir}/jobs/{job_id}/
      job.json  control.json  source.<ext>  audio.flac
      chunks/NNNN.flac  results/NNNN.json
      transcript.json  transcript.txt  artifacts.json

`data_dir` is injected rather than read from the environment here: resolving
`ONEVOICECUT_DATA_DIR` is the composition root's job, and an adapter that reads its
own configuration cannot be pointed at a `tmp_path`.

The id is validated as a ULID *before* it is joined onto a path, not resolved and
then checked for containment. A containment check answers "did we escape?" once the
path exists; this answers "is this even an id?" before anything is created, which is
the only order that holds when the value arrives from an HTTP route.
"""

from pathlib import Path

from onevoicecut.adapters.storage.serialization import (
    decode_artifacts,
    decode_clip_export,
    encode_clip_export,
    decode_chunk_plan,
    decode_chunk_result,
    decode_control,
    decode_job,
    decode_media,
    decode_transcript,
    encode_artifacts,
    encode_chunk_plan,
    encode_chunk_result,
    encode_control,
    encode_job,
    encode_media,
    encode_transcript,
)
# The layout vocabulary lives in `core` and is re-exported here (`X as X` for
# mypy's `no_implicit_reexport`) so every historical importer of these constants
# from this module keeps resolving — the facade delegates, it does not re-declare.
from onevoicecut.shared.infrastructure.storage.core import (
    StorageCore,
    ARTIFACTS as ARTIFACTS,
    AUDIO_TRACK as AUDIO_TRACK,
    CHUNK_PLAN as CHUNK_PLAN,
    CHUNKS_DIRNAME as CHUNKS_DIRNAME,
    CONTROL as CONTROL,
    HEARTBEAT as HEARTBEAT,
    JOBS_DIRNAME as JOBS_DIRNAME,
    JOB_RECORD as JOB_RECORD,
    MEDIA as MEDIA,
    PENDING_SUFFIX as PENDING_SUFFIX,
    RENDER_CLAIM as RENDER_CLAIM,
    RENDER_DIRNAME as RENDER_DIRNAME,
    RESULTS_DIRNAME as RESULTS_DIRNAME,
    SOURCE as SOURCE,
    TRANSCRIPT as TRANSCRIPT,
    TRANSCRIPT_TEXT as TRANSCRIPT_TEXT,
)
from onevoicecut.systems.pipeline.jobs.domain.media import SourceMedia
from onevoicecut.domain.chunking import ChunkPlan, ChunkResult
from onevoicecut.shared.domain.errors import (
    JobAlreadyExists,
    JobNotFound,
    RenderProfileInvalid,
)
from onevoicecut.domain.generation import GenerationResult
from onevoicecut.shared.domain.ids import ClipId, JobId
from onevoicecut.systems.pipeline.jobs.domain.jobs import JobRecord
from onevoicecut.domain.rendering import ClipExport
from onevoicecut.domain.transcript import Transcript


class FilesystemTranscriptStorage:
    def __init__(self, data_dir: Path) -> None:
        self._core = StorageCore(data_dir)

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

    def audio_path(self, job_id: JobId) -> Path:
        """Where the extractor writes the normalized track.

        Storage answers this rather than the caller composing it, so the layout
        stays in one module. Like `job_dir`, it computes a path and creates
        nothing — the extractor owns making the file.
        """
        return self._core.audio_path(job_id)

    def chunk_path(self, job_id: JobId, index: int) -> Path:
        """Zero-padded so the directory sorts the way the chunks are numbered."""
        return self._core.chunk_path(job_id, index)

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

    def save_chunk_plan(self, job_id: JobId, plan: ChunkPlan) -> None:
        self._core.write_atomic(self._core.writable(job_id) / CHUNK_PLAN, encode_chunk_plan(plan))

    def load_chunk_plan(self, job_id: JobId) -> ChunkPlan | None:
        payload = self._core.read_optional(self.job_dir(job_id) / CHUNK_PLAN)
        return None if payload is None else decode_chunk_plan(payload)

    def save_chunk_result(self, result: ChunkResult) -> None:
        """Committed by rename, because this is what resume reads.

        A chunk lands while the job is still running and the process holding it can
        die at any instruction. The next process distinguishes a committed result
        from a half-written one by the directory alone — there is no journal and no
        recovery pass — which is only true if the last step is atomic.
        """
        directory = self._core.writable(result.job_id) / RESULTS_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        self._core.write_atomic(directory / f"{result.index:04d}.json", encode_chunk_result(result))

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
        directory = self._core.writable(transcript.job_id)
        self._core.write_atomic(directory / TRANSCRIPT, encode_transcript(transcript))

    def load_transcript(self, job_id: JobId) -> Transcript | None:
        payload = self._core.read_optional(self.job_dir(job_id) / TRANSCRIPT)
        return None if payload is None else decode_transcript(payload)

    def save_artifacts(self, job_id: JobId, artifacts: GenerationResult) -> None:
        self._core.write_atomic(self._core.writable(job_id) / ARTIFACTS, encode_artifacts(artifacts))

    def load_artifacts(self, job_id: JobId) -> GenerationResult | None:
        payload = self._core.read_optional(self.job_dir(job_id) / ARTIFACTS)
        return None if payload is None else decode_artifacts(payload)

    def export_text(self, job_id: JobId, text: str) -> Path:
        """Writes the derived `.txt`. `transcript.json` is untouched: the export is
        one rendering of the transcript, never a replacement for it."""
        path = self._core.writable(job_id) / TRANSCRIPT_TEXT
        self._core.write_atomic(path, text)
        return path

    def save_clip_export(self, export: ClipExport) -> None:
        """One file per clip *and* profile, committed by rename like every other
        record a worker leaves behind.

        The clip id is a directory rather than a filename because one candidate
        now yields one export per distinct profile, and a flat `{clip_id}.json`
        could hold only the last one written -- silently, since a render that
        finished would leave no trace of the render it overwrote.
        """
        directory = self._core.writable(export.job_id) / RENDER_DIRNAME
        path = self._export_path(directory, export.clip_id, export.profile)
        self._core.write_atomic(path, encode_clip_export(export))

    def load_clip_exports(
        self, job_id: JobId, clip_id: ClipId
    ) -> tuple[ClipExport, ...]:
        """Every profile's export for one clip, sorted by profile so two reads of
        an unchanged directory agree -- `glob` does not promise an order, and a
        caller comparing two listings would otherwise see a difference the disk
        does not have.

        A stale `.tmp` is skipped by the glob rather than by a check, the way
        `load_chunk_results` already does it, so there is no path that forgets to.
        """
        directory = self.job_dir(job_id) / RENDER_DIRNAME / clip_id
        if not directory.is_dir():
            return ()
        exports = [
            decode_clip_export(path.read_text(encoding="utf-8"))
            for path in directory.glob("*.json")
        ]
        return tuple(sorted(exports, key=lambda export: export.profile))

    def list_clip_exports(self) -> tuple[ClipExport, ...]:
        """Every export on the machine, discovered by directory glob -- the
        render drain's `list_jobs`.

        Scoped to directories that are real job ids, the way `list_jobs` scopes
        its own listing, so a scratch folder under the jobs root cannot be
        mistaken for one. Sorted by `(job_id, clip_id, profile)` so two reads
        of an unchanged store agree, the same reason `load_clip_exports` sorts.
        """
        if not self._core.jobs_root.is_dir():
            return ()
        exports = [
            decode_clip_export(path.read_text(encoding="utf-8"))
            for directory in self._core.jobs_root.iterdir()
            if directory.is_dir() and self._core.is_job_id(directory.name)
            for path in (directory / RENDER_DIRNAME).glob("*/*.json")
            if path.is_file()
        ]
        return tuple(
            sorted(exports, key=lambda export: (export.job_id, export.clip_id, export.profile))
        )

    def write_render_claim(self, job_id: JobId, clip_id: ClipId, *, at_s: float) -> None:
        """The render side of `write_heartbeat`: one timestamp per clip, not
        per profile -- a whole clip's pending profiles are claimed by one
        process in one call, so one file records it."""
        directory = self._core.writable(job_id) / RENDER_DIRNAME / clip_id
        self._core.write_atomic(directory / RENDER_CLAIM, repr(float(at_s)))

    def render_claim_is_fresh(
        self, job_id: JobId, clip_id: ClipId, *, now_s: float, stale_after_s: float
    ) -> bool:
        """The render side of `heartbeat_is_fresh`, same fail-closed asymmetry
        and the same reading of a future timestamp as fresh under clock skew."""
        raw = self._core.read_optional(
            self.job_dir(job_id) / RENDER_DIRNAME / clip_id / RENDER_CLAIM
        )
        if raw is None:
            return False
        try:
            written_at = float(raw)
        except ValueError:
            return False
        return now_s - written_at <= stale_after_s

    @staticmethod
    def _export_path(render_dir: Path, clip_id: ClipId, profile: str) -> Path:
        """The profile is a path component, so it is checked like every other
        client-influenced name here.

        It originates in configuration rather than in a request, but
        configuration is not a trust boundary: the same operator file that names
        a profile is edited by hand, and a name carrying `..` would put an
        export outside the job it belongs to. `RenderProfileInvalid` because a
        bad profile name fails identically on every retry -- the distinction that
        type was created for.
        """
        root = render_dir.resolve()
        candidate = (render_dir / clip_id / f"{profile}.json").resolve()
        if not candidate.is_relative_to(root):
            raise RenderProfileInvalid(
                f"render profile {profile!r} does not name a file inside the "
                f"job's render directory; a profile name is a path component "
                f"and cannot escape the job it belongs to"
            )
        return candidate

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
