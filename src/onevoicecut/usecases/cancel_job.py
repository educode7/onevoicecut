"""Stop a job, without ever writing a record a worker still owns.

Cancellation is the one mutation that can arrive while another process is
mid-write, which is what makes it more than a state assignment. The rule the
whole system rests on is that while a worker lives it is the sole writer of
`job.json`; a web process that "helpfully" stamped CANCELLED there would race a
worker committing a chunk result and lose one of the two writes — silently, on a
job that has already cost hours.

So the branch is on who owns the record, not on what looks convenient:

- A **worker-bound** job gets a control-file signal and nothing else. The worker
  reads it at its next chunk boundary and records the terminal state itself,
  through the exit path it already had.
- An **unbound** job (pending, queued, interrupted) has no live writer, so this
  records CANCELLED directly — the same legitimacy startup reconcile relies on
  when it marks abandoned jobs INTERRUPTED. The control file is written too:
  for a queued job it is what contains the spawn-versus-cancel race, and for an
  interrupted one it survives into any later re-run.
- A **terminal** job is left alone entirely. Nothing to stop, so nothing is
  touched.

No liveness probe is taken. A worker-bound record whose worker has already died
takes the same path, and the outcome stays coherent: the control file simply
persists until reconcile marks the record INTERRUPTED, and a re-run sees the
signal before its first chunk. One code path, both endings correct.
"""

import time
from collections.abc import Callable
from dataclasses import replace

from onevoicecut.domain.ids import JobId, OperatorId
from onevoicecut.domain.jobs import (
    TERMINAL_STATES,
    WORKER_BOUND_STATES,
    JobRecord,
    JobState,
)
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.usecases.ownership import require_owner


def cancel_job(
    job_id: JobId,
    *,
    operator: OperatorId,
    storage: TranscriptStoragePort,
    now: Callable[[], float] = time.time,
) -> JobRecord:
    """Returns the record as it stands once the request has been recorded.

    For a worker-bound job that is the *running* state, not CANCELLED — the
    request is in flight and the worker has not stopped yet. Reporting the
    terminal state here would be a lie the shared board contradicts on its very
    next poll, and it would tell the operator the machine is free when it is
    still working.

    Ownership is checked first, before any classification, so the refusal cannot
    depend on which branch a job happens to fall into.
    """
    job = storage.load_job(job_id)
    require_owner(job, operator)

    if job.state in TERMINAL_STATES:
        return job

    storage.request_cancellation(job_id)

    if job.state in WORKER_BOUND_STATES:
        return job

    cancelled = replace(job, state=JobState.CANCELLED, updated_at=now())
    storage.update_job(cancelled)
    return cancelled
