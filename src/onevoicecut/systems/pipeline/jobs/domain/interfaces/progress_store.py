"""What a status read needs of storage, declared where the jobs side may name it.

`get_job` loads the job record and then counts the chunk plan and results
against it, so its handler needs two transcripts-domain reads beside the twelve
`jobs` owns. The jobs *application* may not import `transcripts.domain` — AB-07
forbids that with a test rather than a convention — so the seam is declared
here, exactly as `resume.py` declares `pending_chunks` for the same reason, and
the application reads only `jobs.*`.

It narrows rather than unions: the two methods are copied verbatim from
`TranscriptStore`, so a caller passing the composition root's storage satisfies
`TranscriptStore` and this independently, and mypy compares the two signatures
at every construction site — a drift in either one stops being a silent
agreement. What is left out is the point: a handler that cannot name
`save_transcript` cannot write one while proving that a read is read-only.
"""

from typing import Protocol

from onevoicecut.shared.domain.ids import JobId
from onevoicecut.systems.pipeline.jobs.domain.interfaces.job_store import JobStore
from onevoicecut.systems.pipeline.transcripts.domain.chunking import ChunkPlan
from onevoicecut.systems.pipeline.transcripts.domain.chunking import ChunkResult


class JobProgressStore(JobStore, Protocol):
    """`JobStore` plus the two counts a progress derivation is computed from."""

    def load_chunk_plan(self, job_id: JobId) -> ChunkPlan | None:
        """The plan, or `None` for a job whose chunks were never planned."""
        ...

    def load_chunk_results(self, job_id: JobId) -> tuple[ChunkResult, ...]:
        """Every finished chunk's result, for the count against the plan."""
        ...
