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
same already-filtered answer, and the slice below runs *after* that filter
rather than re-scoping the store underneath it. Slicing first and filtering
after would return the same rows for one page while making the page *union*
depend on the filter — the completeness claim VIS-05 was restated to make
across pages.
"""

from dataclasses import dataclass

from onevoicecut.systems.pipeline.jobs.domain.interfaces.job_store import JobStore
from onevoicecut.shared.application.principal import Principal
from onevoicecut.systems.pipeline.jobs.domain.jobs import JobRecord

# One definition each. The route binds them into `Query(...)`, so a bound that
# drifted between the HTTP layer and the use case would be a limit enforced on
# requests and a different one applied to rows.
DEFAULT_PAGE_LIMIT = 20
MAX_PAGE_LIMIT = 100
MAX_PAGE_OFFSET = 10_000


@dataclass(frozen=True, slots=True)
class ListJobsQuery:
    """Who is asking, whether they asked for their own jobs only, and which
    page of the answer they want.

    There is deliberately no field for an operator identity: no channel carries
    a client-supplied one to this layer, so the only identity the filter can
    compare against is the one that authenticated the request."""

    principal: Principal
    mine: bool = False
    limit: int = DEFAULT_PAGE_LIMIT
    offset: int = 0


class ListJobsHandler:
    """Owns the store; `handle()` owns the narrowing, the slice and nothing else."""

    def __init__(self, *, storage: JobStore) -> None:
        self._storage = storage

    def handle(self, query: ListJobsQuery) -> tuple[JobRecord, ...]:
        """The store's unscoped listing, optionally narrowed, then paged.

        Identity is read off the request before the listing is taken, so an
        unauthenticated-by-construction query cannot be built at all — the
        principal travels with the request rather than being handed to
        `handle()` separately, which is what makes the caller part of the
        request itself.

        The slice is the last operation and is applied to the already-filtered
        tuple. The store's listing stays the unscoped one reconcile uses; the
        page is carved out of the filtered result rather than out of a
        re-scoped query underneath it.
        """
        operator = query.principal.identity
        storage = self._storage

        jobs = storage.list_jobs()
        if query.mine:
            jobs = tuple(job for job in jobs if job.owner == operator)
        return jobs[query.offset : query.offset + query.limit]
