"""Job record entity, its enums, and the progress derived from them."""

from dataclasses import dataclass
from enum import StrEnum

from onevoicecut.domain.chunking import ChunkPlan, ChunkResult, ChunkState
from onevoicecut.domain.ids import JobId, MediaId, OperatorId


class SpeakerMode(StrEnum):
    SINGLE = "single"
    MULTI = "multi"


class EngineChoice(StrEnum):
    """No default: required on every create-job request."""

    LOCAL = "local"
    CLOUD = "cloud"


class JobState(StrEnum):
    PENDING = "pending"
    # Admitted and uploaded, waiting for a worker slot. Nothing writes it until
    # the capacity gate exists; cancellation already classifies over it, because
    # a classification table with a hole gets patched later under pressure.
    QUEUED = "queued"
    EXTRACTING = "extracting"
    PLANNED = "planned"
    TRANSCRIBING = "transcribing"
    STITCHING = "stitching"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


WORKER_BOUND_STATES = frozenset(
    {
        JobState.EXTRACTING,
        JobState.PLANNED,
        JobState.TRANSCRIBING,
        JobState.STITCHING,
        JobState.GENERATING,
    }
)
"""States in which a worker process owns the record.

The single-writer rule is stated here rather than rediscovered at each call
site. Startup reconcile, the capacity gate and cancellation all need the same
answer to "does a worker own this?", and three independent derivations of one
rule is how the three come to disagree.
"""

TERMINAL_STATES = frozenset({JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED})
"""States nothing follows.

Everything outside these two sets is unbound: no worker owns it and no outcome
has settled, so the web process may write it. That the three groups partition
`JobState` is not a comment — `tests/unit/domain/test_jobs.py` asserts it, because
a state outside all three would take cancellation's unbound branch and let the
web process overwrite a live worker's record.
"""


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: JobId
    media_id: MediaId
    state: JobState
    speaker_mode: SpeakerMode
    engine: EngineChoice
    created_at: float
    updated_at: float
    worker_pid: int | None
    error: str | None
    # The operator admitted, or `None` for a record written before owners
    # existed. Required with no default: a construction site must state it
    # explicitly, so no future path can silently mint an ownerless record.
    owner: OperatorId | None


@dataclass(frozen=True, slots=True)
class JobProgress:
    """Never stored. Derived on read, which is why a crash cannot corrupt it."""

    chunks_total: int
    chunks_done: int
    chunks_failed: int
    elapsed_s: float
    eta_s: float | None

    @property
    def chunks_remaining(self) -> int:
        """Neither finished nor failed — the work a resume would pick up."""
        return self.chunks_total - self.chunks_done - self.chunks_failed


def derive_progress(
    plan: ChunkPlan | None,
    results: tuple[ChunkResult, ...],
    *,
    started_at: float,
    now: float,
) -> JobProgress | None:
    """Count what exists against what was planned. Never a counter.

    A counter incremented as work proceeds diverges from the truth the moment the
    process holding it dies — the exact case this system is built for. Reading the
    persisted results against the persisted plan means progress after a crash is
    already correct, with no recovery step to write and none to get wrong.

    `None` when the job has not been planned yet: before there is a denominator,
    "0 of 0" would read as a finished job.
    """
    if plan is None:
        return None

    done = sum(1 for result in results if result.state is ChunkState.DONE)
    failed = sum(1 for result in results if result.state is ChunkState.FAILED)

    return JobProgress(
        chunks_total=len(plan.chunks),
        chunks_done=done,
        chunks_failed=failed,
        elapsed_s=now - started_at,
        eta_s=_eta(len(plan.chunks), done + failed, now - started_at),
    )


def _eta(total: int, attempted: int, elapsed_s: float) -> float | None:
    """`None` until a chunk has finished, and only then a number.

    An estimate from zero samples is not a rough estimate, it is a fabrication —
    and the operator plans their evening around it. A failed chunk counts toward
    the rate: it held the machine as long as a successful one, longer after
    retries, so excluding it would flatter the projection.
    """
    if attempted == 0:
        return None
    return (elapsed_s / attempted) * (total - attempted)
