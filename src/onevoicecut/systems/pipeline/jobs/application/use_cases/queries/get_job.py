"""Read one job: the record plus the progress derived from what is on disk.

A status read is derivation, not state. The worker is the sole writer of the
job record, so this handler loads it, counts the plan and results against it
and computes — there is nothing to race against because nothing is written.

Two things stop being the route's business here:

- The id is validated before the store is asked. A malformed id answers with
  `JobNotFound` exactly like an unknown one, so the composition root's table
  gives one 404 for both and neither the status nor the body tells a caller
  which ids exist. Validating after the load would already have handed storage
  a path component built from a caller-supplied string (AUTH-14's handler
  half).
- The refusal is the domain error, not an HTTP answer: the application layer
  does not know what HTTP is, and one table at the composition edge maps it to
  404 for every route that reads a job.

The route this will replace in slice 2e does the same two things today; the
body moved verbatim and only the shape around it is new.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass

from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.shared.domain.errors import JobNotFound
from onevoicecut.shared.domain.ids import InvalidIdError, JobId, make_job_id
from onevoicecut.systems.pipeline.jobs.domain.jobs import (
    JobProgress,
    JobRecord,
    derive_progress,
)


@dataclass(frozen=True, slots=True)
class GetJobQuery:
    """Which job to read. Raw on purpose: the id arrives as it appeared in the
    path, because refusing a malformed one is this handler's own question."""

    job_id: str


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """The record and its derived progress — everything a status read answers.

    Paired rather than returned as a tuple so the two cannot be swapped at a
    call site: progress without its record is not a status, and a record
    carrying somebody else's progress is worse than no answer at all."""

    job: JobRecord
    progress: JobProgress | None


class GetJobHandler:
    """Owns the store and the clock; `handle()` owns the read and nothing else."""

    def __init__(
        self,
        *,
        storage: TranscriptStoragePort,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._storage = storage
        self._now = now

    def handle(self, query: GetJobQuery) -> JobSnapshot:
        """Load, derive, return — in that order, with nothing written.

        The id is checked first, before the store is touched: AUTH-14 requires
        the 404 ahead of any filesystem access, and a handler that validated
        after loading would have already resolved a path built from a
        caller-supplied string.
        """
        storage = self._storage
        now = self._now

        job_id = _validated_job_id(query.job_id)
        job = storage.load_job(job_id)
        progress = derive_progress(
            storage.load_chunk_plan(job.job_id),
            storage.load_chunk_results(job.job_id),
            started_at=job.created_at,
            now=now(),
        )
        return JobSnapshot(job=job, progress=progress)


def _validated_job_id(raw: str) -> JobId:
    """Check the id at the door, against the pattern the domain owns.

    Malformed answers exactly like unknown — `JobNotFound`, same type, same
    message shape — so neither the status nor the body reveals which ids
    exist. The filesystem adapter validates too, and two checks for one rule
    is not duplication: it is the difference between a boundary that holds and
    one that happens to, which is the reasoning the route's own
    `_validated_job_id` states in full.
    """
    try:
        return make_job_id(raw)
    except InvalidIdError as error:
        raise JobNotFound(f"no job stored under {raw!r}") from error
