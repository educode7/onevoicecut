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

from onevoicecut.domain.chunking import AudioChunk, ChunkPlan, ChunkResult, ChunkState
from onevoicecut.domain.errors import ChunkTimeout, TranscriptionFailed
from onevoicecut.domain.ids import JobId
from onevoicecut.domain.jobs import JobRecord, JobState, SpeakerMode
from onevoicecut.domain.media import AudioTrack, SourceMedia
from onevoicecut.domain.transcript import Transcript, render_message_text
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.ports.transcription import TranscriptionPort, TranscriptionRequest
from onevoicecut.usecases.plan_chunks import DEFAULT_TARGET_CHUNK_S, plan_chunks
from onevoicecut.usecases.resume_job import pending_chunks
from onevoicecut.usecases.stitch_transcript import stitch_transcript

SOURCE_LANGUAGE = "es"

# Per chunk, never per job. A job runs for hours by design, so a total deadline
# would abort correct work; a chunk that has not returned in this long is stuck.
DEFAULT_CHUNK_TIMEOUT_S = 30 * 60.0

# An error listing 87 chunk indices is no more useful than one listing six.
MAX_REPORTED_FAILURES = 6

# Enough to ride out a transient provider error, few enough that a chunk the
# engine will never accept does not stall a job that already runs for hours.
DEFAULT_MAX_ATTEMPTS = 3

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
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
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
    # Resume is not a mode: the loop simply skips what is already done, so a first
    # run and a restart after a crash take the same route.
    for planned in pending_chunks(plan, storage.load_chunk_results(job_id)):
        # Polled every iteration, not once before the loop: a three-hour job
        # checked at the start would ignore the stop button for three hours.
        if storage.cancellation_requested(job_id):
            return _finish(job, JobState.CANCELLED, storage=storage, now=now)

        chunk = extractor.slice(
            track, planned, storage.chunk_path(job_id, planned.index)
        )
        result = _transcribe_chunk(
            chunk, request, transcriber=transcriber, now=now, max_attempts=max_attempts
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
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
) -> ChunkResult:
    """One chunk's outcome, never the job's.

    `TranscriptionFailed` is contained and retried: a cloud engine dropping one
    request in eighty-seven is ordinary, and failing the chunk on the first
    refusal would cost an hour of a job over a network blip. Chunk 84 of 87
    failing for good must still not discard the first 83, so an exhausted chunk
    becomes a recorded `FAILED` result and the loop continues.

    `ChunkTimeout` is contained but **not** retried. It is the one failure where
    another attempt mostly spends the budget again — three tries at thirty minutes
    is ninety minutes to learn the same thing.

    `DiarizationUnsupported` is deliberately not contained at all. It is a
    capability gap, not a chunk defect: every remaining chunk would raise it
    identically, so grinding through 87 of them discovers the same fact 87 times
    and then blames the last chunk for the job's configuration.
    """
    engine_id = transcriber.capabilities().engine_id
    last_error = ""

    for attempt in range(1, max_attempts + 1):
        try:
            segments = transcriber.transcribe(chunk, request)
        except ChunkTimeout as error:
            # Before the TranscriptionFailed clause on purpose: ChunkTimeout is a
            # subclass, and the broader handler would otherwise retry it.
            return _failed_chunk(chunk, engine_id, attempt, str(error), now)
        except TranscriptionFailed as error:
            last_error = str(error)
            continue

        return ChunkResult(
            job_id=chunk.job_id,
            index=chunk.index,
            state=ChunkState.DONE,
            segments=segments,
            engine_id=engine_id,
            attempts=attempt,
            error=None,
            finished_at=now(),
        )

    return _failed_chunk(chunk, engine_id, max_attempts, last_error, now)


def _failed_chunk(
    chunk: AudioChunk,
    engine_id: str,
    attempts: int,
    error: str,
    now: Clock,
) -> ChunkResult:
    """`attempts` is evidence about the engine, not bookkeeping: a chunk that
    needed three tries and one that never ran are different facts."""
    return ChunkResult(
        job_id=chunk.job_id,
        index=chunk.index,
        state=ChunkState.FAILED,
        segments=(),
        engine_id=engine_id,
        attempts=attempts,
        error=error,
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

    An existing plan is reused rather than recomputed. Re-planning would usually
    produce the same boundaries, but "usually" is not good enough here: the stored
    chunk results are indexed against the *stored* plan, and a plan that differed
    by one chunk would silently re-map every completed result onto the wrong range.
    """
    existing = storage.load_chunk_plan(job.job_id)
    if existing is not None:
        _advance(job, JobState.PLANNED, storage=storage, now=now)
        return existing

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
    # The structured transcript is the source of truth; this is one rendering of
    # it, and it is the one the operator actually opens. Written only here, after
    # a complete run, because an export over a hole reads as a whole sermon.
    storage.export_text(job.job_id, render_message_text(transcript))
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
