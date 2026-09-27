"""The jobs-side resume command: which chunks a restarted job still owes.

The derivation itself is transcripts-side (`pending_chunks`, published through
the transcripts module API) because it is pure over transcripts domain entities
— the module map keeps it there deliberately. This command is jobs-side for the
same reason admission is: resume is an operation on a job, expressed in the jobs
command set, and it consumes the derivation through the jobs-owned
`PendingChunks` interface rather than importing the other system's domain
(AB-06/AB-07).

No handler dependency but the derivation: resume needs nothing else resolved,
which is why this is the thinnest command in either command set.
"""

from dataclasses import dataclass

from onevoicecut.systems.pipeline.jobs.domain.interfaces.resume import (
    ChunkPlan,
    ChunkResult,
    PlannedChunk,
    PendingChunks,
)


@dataclass(frozen=True, slots=True)
class ResumeJobCommand:
    """The plan a restart reads against, and whatever the last run committed."""

    plan: ChunkPlan
    results: tuple[ChunkResult, ...]


class ResumeJobHandler:
    """Answers with the chunks still owed, through the jobs-owned seam.

    The derivation arrives at construction — a dependency like any other — so
    the handler never learns which side implements it, and a test hands it a
    lambda without either side importing the other's domain.
    """

    def __init__(self, *, pending_chunks: PendingChunks) -> None:
        self._pending_chunks = pending_chunks

    def handle(self, command: ResumeJobCommand) -> tuple[PlannedChunk, ...]:
        return self._pending_chunks(command.plan, command.results)
