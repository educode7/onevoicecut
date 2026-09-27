"""The transcripts module's API: how the worker-driven commands are wired.

A module API exists because the alternative spreads the same handler
constructions across every caller — the worker today, a second composition
root tomorrow — and two callers wiring the loop with different clocks or
chunk timeouts is two different transcription runs. One function, fed the
root's dependencies, decides it once.

There is deliberately no router here: transcripts exposes no HTTP operations.
Every job is driven by the worker process, so the entry point this file
publishes is `transcribe_job` — the facade the worker imports — plus the
handler a root may construct directly when it wants the command record
instead.

`transcribe_job` keeps the pre-migration function signature exactly. The
worker is a composition root whose call body is frozen by design, so the
facade is what lets its import line rewire without touching a line of the
loop.
"""

import time

from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.shared.domain.ids import JobId
from onevoicecut.shared.domain.media import SourceMedia
from onevoicecut.systems.pipeline.jobs.domain.jobs import JobRecord
from onevoicecut.systems.pipeline.transcripts.application.use_cases.commands.transcribe_job import (
    DEFAULT_CHUNK_TIMEOUT_S as DEFAULT_CHUNK_TIMEOUT_S,
)
from onevoicecut.systems.pipeline.transcripts.application.use_cases.commands.transcribe_job import (
    DEFAULT_MAX_ATTEMPTS as DEFAULT_MAX_ATTEMPTS,
)
from onevoicecut.systems.pipeline.transcripts.application.use_cases.commands.transcribe_job import (
    DEFAULT_MAX_SPLIT_DEPTH,
    DEFAULT_TARGET_CHUNK_S,
    Clock as Clock,
    TranscribeJobCommand,
    TranscribeJobHandler,
)
from onevoicecut.systems.pipeline.transcripts.domain.interfaces.audio_extractor import (
    AudioExtractorPort,
)
from onevoicecut.systems.pipeline.transcripts.domain.interfaces.transcription import (
    TranscriptionPort,
)


def transcribe_job(
    job_id: JobId,
    media: SourceMedia,
    *,
    extractor: AudioExtractorPort,
    transcriber: TranscriptionPort,
    storage: TranscriptStoragePort,
    now: Clock = time.time,
    target_chunk_s: float = DEFAULT_TARGET_CHUNK_S,
    chunk_timeout_s: float | None = DEFAULT_CHUNK_TIMEOUT_S,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    max_split_depth: int = DEFAULT_MAX_SPLIT_DEPTH,
) -> JobRecord:
    """Assemble one run's command and hand it to the handler.

    Construction, not orchestration: every branch, every phase transition and
    the commit-per-chunk property all live in `TranscribeJobHandler.handle`.
    """
    return TranscribeJobHandler(
        extractor=extractor,
        transcriber=transcriber,
        storage=storage,
        now=now,
        target_chunk_s=target_chunk_s,
        chunk_timeout_s=chunk_timeout_s,
        max_attempts=max_attempts,
        max_split_depth=max_split_depth,
    ).handle(TranscribeJobCommand(job_id=job_id, media=media))
