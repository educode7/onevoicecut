"""The core loop: one job, from source media to a stored transcript.

Everything it coordinates is already proven in isolation — planning, slicing,
transcription, stitching, persistence. What lives here is the *order*, and one
property that none of those pieces can hold on its own: **a chunk result is
committed the moment it completes**, before the next chunk is attempted. Batching
the writes to the end would satisfy every other test in the suite and lose three
hours of work to a crash at chunk 86.

The job record is updated at each phase rather than only at the end, because the
web process polls it and a multi-hour job that reports nothing until it finishes
reports nothing at all.

This module owns no adapter. The engine has already been resolved by the
composition root, so the loop never learns whether it is driving the local or the
cloud one — which is what keeps engine choice out of the use-case layer entirely.
"""

import time
from collections.abc import Callable
from dataclasses import replace

from transcribe.domain.chunking import ChunkPlan, ChunkResult, ChunkState
from transcribe.domain.ids import JobId
from transcribe.domain.jobs import JobRecord, JobState, SpeakerMode
from transcribe.domain.media import AudioTrack, SourceMedia
from transcribe.domain.transcript import Transcript
from transcribe.ports.audio_extractor import AudioExtractorPort
from transcribe.ports.transcript_storage import TranscriptStoragePort
from transcribe.ports.transcription import TranscriptionPort, TranscriptionRequest
from transcribe.usecases.plan_chunks import DEFAULT_TARGET_CHUNK_S, plan_chunks
from transcribe.usecases.stitch_transcript import stitch_transcript

SOURCE_LANGUAGE = "es"

# Per chunk, never per job. A job runs for hours by design, so a total deadline
# would abort correct work; a chunk that has not returned in this long is stuck.
DEFAULT_CHUNK_TIMEOUT_S = 30 * 60.0

Clock = Callable[[], float]


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
) -> Transcript:
    job = storage.load_job(job_id)

    track = _extract(job, media, extractor=extractor, storage=storage, now=now)
    plan = _plan(
        job,
        track,
        transcriber=transcriber,
        storage=storage,
        now=now,
        target_chunk_s=target_chunk_s,
    )

    _advance(job, JobState.TRANSCRIBING, storage=storage, now=now)
    request = TranscriptionRequest(
        language=SOURCE_LANGUAGE,
        speaker_mode=job.speaker_mode,
        timeout_s=chunk_timeout_s,
    )
    for planned in plan.chunks:
        chunk = extractor.slice(
            track, planned, storage.chunk_path(job_id, planned.index)
        )
        segments = transcriber.transcribe(chunk, request)
        # Committed here, inside the loop, not accumulated for a final batch.
        storage.save_chunk_result(
            ChunkResult(
                job_id=job_id,
                index=planned.index,
                state=ChunkState.DONE,
                segments=segments,
                engine_id=transcriber.capabilities().engine_id,
                attempts=1,
                error=None,
                finished_at=now(),
            )
        )

    return _stitch(job, plan, transcriber=transcriber, storage=storage, now=now)


def _extract(
    job: JobRecord,
    media: SourceMedia,
    *,
    extractor: AudioExtractorPort,
    storage: TranscriptStoragePort,
    now: Clock,
) -> AudioTrack:
    _advance(job, JobState.EXTRACTING, storage=storage, now=now)
    return extractor.extract(media, storage.audio_path(job.job_id))


def _plan(
    job: JobRecord,
    track: AudioTrack,
    *,
    transcriber: TranscriptionPort,
    storage: TranscriptStoragePort,
    now: Clock,
    target_chunk_s: float = DEFAULT_TARGET_CHUNK_S,
) -> ChunkPlan:
    """Persisted before any chunk runs, because resume reads it.

    A plan held only in memory makes resume impossible: after a crash nothing on
    disk says how many chunks the job was supposed to have, so completed results
    cannot be told apart from a job that was always this short.
    """
    plan = plan_chunks(
        job.job_id,
        track,
        transcriber.capabilities(),
        target_chunk_s=target_chunk_s,
    )
    storage.save_chunk_plan(job.job_id, plan)
    _advance(job, JobState.PLANNED, storage=storage, now=now)
    return plan


def _stitch(
    job: JobRecord,
    plan: ChunkPlan,
    *,
    transcriber: TranscriptionPort,
    storage: TranscriptStoragePort,
    now: Clock,
) -> Transcript:
    _advance(job, JobState.STITCHING, storage=storage, now=now)
    transcript = Transcript(
        job_id=job.job_id,
        # The one place chunk-local times become track-relative. Skipping it would
        # restart every chunk's segments at zero, pointing every clip timestamp at
        # the opening seconds of the sermon.
        segments=stitch_transcript(plan, storage.load_chunk_results(job.job_id)),
        engine_id=transcriber.capabilities().engine_id,
        diarized=job.speaker_mode is SpeakerMode.MULTI,
        language=SOURCE_LANGUAGE,
    )
    storage.save_transcript(transcript)
    _advance(job, JobState.COMPLETED, storage=storage, now=now)
    return transcript


def _advance(
    job: JobRecord,
    state: JobState,
    *,
    storage: TranscriptStoragePort,
    now: Clock,
) -> None:
    """Update, never create: the worker owns a job the web process already admitted."""
    storage.update_job(replace(job, state=state, updated_at=now()))
