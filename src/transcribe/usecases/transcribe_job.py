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

from transcribe.domain.chunking import AudioChunk, ChunkPlan, ChunkResult, ChunkState
from transcribe.domain.errors import TranscriptionFailed
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

# An error listing 87 chunk indices is no more useful than one listing six.
MAX_REPORTED_FAILURES = 6

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
) -> JobRecord:
    """Returns the job as it ended, not the transcript.

    Three outcomes are normal here — completed, failed with chunks preserved for
    resume, and cancelled — and only one of them produces a transcript. Returning
    the record says which happened without making the caller catch an exception
    for an outcome that is not exceptional. The transcript, when there is one, is
    in storage.
    """
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

    failed: list[int] = []
    for planned in plan.chunks:
        # Polled every iteration, not once before the loop: a three-hour job
        # checked at the start would ignore the stop button for three hours.
        if storage.cancellation_requested(job_id):
            return _finish(job, JobState.CANCELLED, storage=storage, now=now)

        chunk = extractor.slice(
            track, planned, storage.chunk_path(job_id, planned.index)
        )
        result = _transcribe_chunk(
            chunk, request, transcriber=transcriber, now=now
        )
        # Committed here, inside the loop, not accumulated for a final batch.
        storage.save_chunk_result(result)
        if result.state is ChunkState.FAILED:
            failed.append(planned.index)

    if failed:
        return _finish(
            job,
            JobState.FAILED,
            storage=storage,
            now=now,
            error=_failure_summary(failed, len(plan.chunks)),
        )

    return _stitch(job, plan, transcriber=transcriber, storage=storage, now=now)


def _transcribe_chunk(
    chunk: AudioChunk,
    request: TranscriptionRequest,
    *,
    transcriber: TranscriptionPort,
    now: Clock,
) -> ChunkResult:
    """One chunk's outcome, never the job's.

    `TranscriptionFailed` is contained: chunk 84 of 87 failing must not discard
    the first 83, so it becomes a recorded `FAILED` result and the loop continues.

    `DiarizationUnsupported` is deliberately *not* contained. It is a capability
    gap, not a chunk defect — every remaining chunk would raise it identically, so
    grinding through 87 of them discovers the same fact 87 times and then blames
    the last chunk for the job's configuration.
    """
    try:
        segments = transcriber.transcribe(chunk, request)
    except TranscriptionFailed as error:
        return ChunkResult(
            job_id=chunk.job_id,
            index=chunk.index,
            state=ChunkState.FAILED,
            segments=(),
            engine_id=transcriber.capabilities().engine_id,
            attempts=1,
            error=str(error),
            finished_at=now(),
        )

    return ChunkResult(
        job_id=chunk.job_id,
        index=chunk.index,
        state=ChunkState.DONE,
        segments=segments,
        engine_id=transcriber.capabilities().engine_id,
        attempts=1,
        error=None,
        finished_at=now(),
    )


def _failure_summary(failed: list[int], total: int) -> str:
    """Names the chunks, so the operator knows where in a three-hour sermon to look.

    Truncated past a handful: an error listing 87 indices is not more informative
    than one listing six and saying how many there were.
    """
    shown = ", ".join(str(index) for index in failed[:MAX_REPORTED_FAILURES])
    if len(failed) > MAX_REPORTED_FAILURES:
        shown += f", … (+{len(failed) - MAX_REPORTED_FAILURES} more)"
    return f"{len(failed)} of {total} chunks failed: {shown}"


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
) -> JobRecord:
    """Only reached when every chunk succeeded.

    A hole must never be stitched. The words either side of a missing chunk simply
    run together, so the resulting transcript reads perfectly and nothing in it
    announces that a quarter of the sermon is absent.
    """
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
    return _finish(job, JobState.COMPLETED, storage=storage, now=now)


def _advance(
    job: JobRecord,
    state: JobState,
    *,
    storage: TranscriptStoragePort,
    now: Clock,
) -> JobRecord:
    """Update, never create: the worker owns a job the web process already admitted."""
    moved = replace(job, state=state, updated_at=now())
    storage.update_job(moved)
    return moved


def _finish(
    job: JobRecord,
    state: JobState,
    *,
    storage: TranscriptStoragePort,
    now: Clock,
    error: str | None = None,
) -> JobRecord:
    moved = replace(job, state=state, updated_at=now(), error=error)
    storage.update_job(moved)
    return moved
