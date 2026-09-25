"""List every job on the shared board: attributed, hidden from nobody.

One ministry team cuts one church's sermons, so "is Sunday's sermon done?" is
collaboration, not leakage — read access to every job is the point of a shared
server, while mutation stays owner-gated elsewhere. That is why the source here
is the store's *unscoped* `list_jobs()`, the same listing startup reconcile
uses: the handler cannot scope by caller what the store never scoped, so
"nothing hidden" is structural rather than a promise this file keeps.

Attribution is not something this handler adds either. Each record already
carries the operator who admitted it, and a record written before owners
existed carries `None` — present in the listing, attributed to nobody. The
presentation layer projects those fields; it never decides them.

`mine` is the one branch, and it runs here rather than in the response schema:
narrowing the tuple of records first means every consumer downstream sees the
same already-filtered answer, and pagination (slice 5b) will slice *after* this
filter rather than re-scoping the store underneath it.
"""

from dataclasses import dataclass

from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.shared.application.principal import Principal
from onevoicecut.systems.pipeline.jobs.domain.jobs import JobRecord


@dataclass(frozen=True, slots=True)
class ListJobsQuery:
    """Who is asking, and whether they asked for their own jobs only.

    There is deliberately no field for an operator identity: no channel carries
    a client-supplied one to this layer, so the only identity the filter can
    compare against is the one that authenticated the request."""

    principal: Principal
    mine: bool = False


class ListJobsHandler:
    """Owns the store; `handle()` owns the narrowing and nothing else."""

    def __init__(self, *, storage: TranscriptStoragePort) -> None:
        self._storage = storage

    def handle(self, query: ListJobsQuery) -> tuple[JobRecord, ...]:
        """The store's unscoped listing, optionally narrowed to the caller's.

        Identity is read off the request before the listing is taken, so an
        unauthenticated-by-construction query cannot be built at all — the
        principal travels with the request rather than being handed to
        `handle()` separately, which is what makes the caller part of the
        request itself.
        """
        operator = query.principal.identity
        storage = self._storage

        jobs = storage.list_jobs()
        if query.mine:
            jobs = tuple(job for job in jobs if job.owner == operator)
        return jobs
