"""Which chunks a restarted job still owes.

Resume is not a mode. Running the job again *is* the resume: the loop asks this
what is left and works only on that, so the first run and the fifth take the same
route. A separate resume path would execute rarely, be tested rarely, and be
exactly where a bug survives a year.

`DONE` is the only state that discharges a chunk. Everything else is owed again,
including `RUNNING` — which is precisely what a process killed mid-chunk leaves
behind, and trusting it would drop that chunk silently. A dropped chunk is not a
visible gap: once stitched, the words either side of it run together and the
transcript reads continuous.
"""

from transcribe.domain.chunking import ChunkPlan, ChunkResult, ChunkState, PlannedChunk


def pending_chunks(
    plan: ChunkPlan, results: tuple[ChunkResult, ...]
) -> tuple[PlannedChunk, ...]:
    """The planned chunks with no completed result, in plan order.

    Order comes from the plan rather than from the results, because results are
    written in whatever order chunks finished — a retry can commit chunk 7 after
    chunk 11 — while the work must still proceed forward through the sermon.

    A result whose index is not in the plan is ignored rather than trusted. It is
    a leftover from an earlier plan, and letting it discharge current work would
    mark a chunk done that this plan never ran.
    """
    completed = {
        result.index for result in results if result.state is ChunkState.DONE
    }
    return tuple(chunk for chunk in plan.chunks if chunk.index not in completed)
