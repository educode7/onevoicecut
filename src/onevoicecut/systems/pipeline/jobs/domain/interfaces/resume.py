"""The resume derivation's shape, declared where the jobs side may name it.

`pending_chunks` is pure over `ChunkPlan` and `ChunkResult` — transcripts
domain — and lives there, published through the transcripts module API. What
may not follow it across is the dependency itself: the jobs *application*
importing `transcripts.domain` would put one system's application on the other
system's domain types, and AB-07 forbids that with a test rather than a
convention.

So the jobs side declares its own seam: a Protocol with the derivation's
signature. `jobs/domain` may speak the entity names (it already does, for
`JobRecord`), so the types are imported here with explicit re-exports and the
application layer reads only `jobs.*`.
"""

from typing import Protocol

from onevoicecut.systems.pipeline.transcripts.domain.chunking import (
    ChunkPlan as ChunkPlan,
)
from onevoicecut.systems.pipeline.transcripts.domain.chunking import (
    ChunkResult as ChunkResult,
)
from onevoicecut.systems.pipeline.transcripts.domain.chunking import (
    PlannedChunk as PlannedChunk,
)


class PendingChunks(Protocol):
    """Plan plus results in, the chunks still owed out — in plan order.

    Callable rather than a class hierarchy on purpose: the real derivation is a
    pure function, a test's double is a lambda, and neither should have to
    inherit to satisfy the jobs command that consumes them.
    """

    def __call__(
        self, plan: ChunkPlan, results: tuple[ChunkResult, ...]
    ) -> tuple[PlannedChunk, ...]: ...
