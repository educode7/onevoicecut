"""The transcripts half of the persistence boundary (design decision OQ3).

Narrow by construction: every method here takes or returns only types `transcripts`
owns — the chunk plan, the chunk results and the transcript. Job records and clip
state are declared on the interfaces of the modules that own them, so no single
Protocol has to import all three domains' types. The facade implementing this over
the shared filesystem core lands with slice 3b; nothing here knows how a record is
written.
"""

from pathlib import Path
from typing import Protocol

from onevoicecut.shared.domain.ids import JobId
from onevoicecut.systems.pipeline.transcripts.domain.chunking import ChunkPlan, ChunkResult
from onevoicecut.systems.pipeline.transcripts.domain.transcript import Transcript


class TranscriptStore(Protocol):
    def audio_path(self, job_id: JobId) -> Path: ...

    def chunk_path(self, job_id: JobId, index: int) -> Path: ...

    def save_chunk_plan(self, job_id: JobId, plan: ChunkPlan) -> None: ...

    def load_chunk_plan(self, job_id: JobId) -> ChunkPlan | None: ...

    def save_chunk_result(self, result: ChunkResult) -> None:
        """MUST be atomic."""
        ...

    def load_chunk_results(self, job_id: JobId) -> tuple[ChunkResult, ...]: ...

    def save_transcript(self, transcript: Transcript) -> None: ...

    def load_transcript(self, job_id: JobId) -> Transcript | None: ...

    def export_text(self, job_id: JobId, text: str) -> Path: ...
