"""The jobs half of the persistence boundary (design decision OQ3).

Narrow by construction: every method here takes or returns only types `jobs`
owns. Chunk plans, chunk results, transcripts, artifacts, exports and render
claims are declared on the interfaces of the modules that own them, so no
single Protocol has to import all three domains' types — which is what made
the old cross-module `TranscriptStoragePort` unsatisfiable inside the layout.
The on-disk layout stays with the shared filesystem core behind a facade bound
at a composition root; nothing here knows how a record is written.
"""

from pathlib import Path
from typing import Protocol

from onevoicecut.shared.domain.ids import JobId
from onevoicecut.systems.pipeline.jobs.domain.jobs import JobRecord
from onevoicecut.shared.domain.media import SourceMedia


class JobStore(Protocol):
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

    def write_heartbeat(self, job_id: JobId, *, at_s: float) -> None:
        """Written by the worker, and by nothing else.

        A pid says a process exists; this says it is still doing the work. The
        two failures it covers are ordinary rather than exotic on a machine that
        runs multi-hour jobs: a worker that hangs keeps its pid, and a recycled
        pid makes a dead worker's number belong to somebody else.
        """
        ...

    def heartbeat_is_fresh(
        self, job_id: JobId, *, now_s: float, stale_after_s: float
    ) -> bool:
        """MUST fail closed: absent or unreadable is not fresh.

        Believing a dead worker is alive orphans its job permanently, because
        nothing reconciles a record that looks healthy. Believing a live worker
        is dead costs a re-run that resumes from the committed chunks.
        """
        ...

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
