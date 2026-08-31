"""Create a job record from what the operator chose. Nothing else.

Admission is deliberately the smallest thing that can happen: it records a
decision and returns an id. No media is read, nothing is planned, no work starts.
That is what lets a multi-hour upload — and the hours of transcription after it —
happen without an HTTP request waiting on either.

The engine and speaker mode are captured here because this is the only moment the
operator is present to choose them. A worker reading the record hours later, in
another process, has nobody to ask.
"""

import time
from collections.abc import Callable

from onevoicecut.domain.errors import DiarizationUnsupported
from onevoicecut.domain.ids import (
    JobId,
    MediaId,
    generate_job_id,
    generate_media_id,
)
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.ports.capabilities import DiarizationSupport
from onevoicecut.ports.transcript_storage import TranscriptStoragePort


def _validate_compatibility(
    diarization: DiarizationSupport, speaker_mode: SpeakerMode
) -> None:
    """Reject MULTI when the engine cannot diarize.

    This is a pure helper — no I/O, no port calls, no entity creation —
    shared by the admission guard and the port-level defense-in-depth
    invariant so there is a single definition of compatibility.
    """
    if speaker_mode is SpeakerMode.MULTI and diarization is not DiarizationSupport.AVAILABLE:
        raise DiarizationUnsupported(
            f"engine declares diarization={diarization.value}; "
            f"switch to a diarizing engine or use speaker_mode=single"
        )


def admit_job(
    *,
    engine: EngineChoice,
    speaker_mode: SpeakerMode,
    storage: TranscriptStoragePort,
    now: Callable[[], float] = time.time,
    new_job_id: Callable[[], JobId] = generate_job_id,
    new_media_id: Callable[[], MediaId] = generate_media_id,
) -> JobRecord:
    """Both ids are minted server-side, before anything touches the filesystem.

    The media id is allocated now even though no bytes have arrived, so the
    upload that follows has somewhere to belong rather than inventing an identity
    from whatever the client sent.
    """
    job = JobRecord(
        job_id=new_job_id(),
        media_id=new_media_id(),
        state=JobState.PENDING,
        speaker_mode=speaker_mode,
        engine=engine,
        created_at=now(),
        updated_at=now(),
        worker_pid=None,
        error=None,
    )
    storage.create_job(job)
    return job
