"""The persistence boundary of the whole job aggregate. Resume is built on this."""

from pathlib import Path
from typing import Protocol

from onevoicecut.domain.chunking import ChunkPlan, ChunkResult
from onevoicecut.domain.generation import GenerationResult
from onevoicecut.domain.ids import JobId
from onevoicecut.domain.jobs import JobRecord
from onevoicecut.domain.media import SourceMedia
from onevoicecut.domain.transcript import Transcript


class TranscriptStoragePort(Protocol):
    def job_dir(self, job_id: JobId) -> Path:
        """The directory holding everything for one job.

        On the port because the worker must hand an extractor somewhere to write,
        and inventing that path in a use case would put the on-disk layout in two
        places. Storage owns the layout; callers ask it where things go.
        """
        ...

    def source_path(self, job_id: JobId) -> Path:
        """Where the uploaded bytes land.

        Deliberately extensionless. Content type is decided by `ffprobe`, never by
        a suffix, so an extension here would carry no meaning and would be one
        more thing a client could influence.
        """
        ...

    def audio_path(self, job_id: JobId) -> Path: ...

    def chunk_path(self, job_id: JobId, index: int) -> Path: ...

    def create_job(self, job: JobRecord) -> None: ...

    def load_job(self, job_id: JobId) -> JobRecord: ...

    def update_job(self, job: JobRecord) -> None: ...

    def list_jobs(self) -> tuple[JobRecord, ...]:
        """Every job, **sorted by id** — which for ULIDs is creation order.

        The ordering is part of the contract, not an accident of one adapter.
        The drain gate selects queued work oldest-first and does no sorting of
        its own, so an implementation that returned an arbitrary order would
        turn FIFO fairness into luck: a sermon uploaded on Sunday could wait
        behind one uploaded on Wednesday, and nothing would report it.
        """
        ...

    def save_media(self, job_id: JobId, media: SourceMedia) -> None:
        """Recorded at admission and read by the worker hours later.

        The job record carries only a `media_id`; the container, the stored path
        and the checksum live here. Without them a worker in a separate process
        would have to invent a `SourceMedia`, and an invented checksum is worse
        than none.
        """
        ...

    def load_media(self, job_id: JobId) -> SourceMedia: ...

    def save_chunk_plan(self, job_id: JobId, plan: ChunkPlan) -> None: ...

    def load_chunk_plan(self, job_id: JobId) -> ChunkPlan | None: ...

    def save_chunk_result(self, result: ChunkResult) -> None:
        """MUST be atomic."""
        ...

    def load_chunk_results(self, job_id: JobId) -> tuple[ChunkResult, ...]: ...

    def save_transcript(self, transcript: Transcript) -> None: ...

    def load_transcript(self, job_id: JobId) -> Transcript | None: ...

    def save_artifacts(self, job_id: JobId, artifacts: GenerationResult) -> None: ...

    def export_text(self, job_id: JobId, text: str) -> Path: ...

    def request_cancellation(self, job_id: JobId, *, requested: bool = True) -> None:
        """Written by the web process, never by the worker."""
        ...

    def cancellation_requested(self, job_id: JobId) -> bool:
        """Polled by the worker at chunk boundaries.

        Promoted to the port in slice 4b, as 4a left open. The core loop is a use
        case and cannot reach for a concrete adapter, and cancelling a multi-hour
        job is not an adapter detail — it is how the operator stops the work.
        """
        ...
