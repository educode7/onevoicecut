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
    OperatorId,
    generate_job_id,
    generate_media_id,
)
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.errors import ClassificationUnsupported
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DeclaredSupport,
    DiarizationSupport,
)
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


def _validate_summarizable(classification: ClassificationSupport) -> None:
    """Reject a job whose engine could only produce a blank summary.

    MAP windows are built from confirmed `SPEECH`, so an engine declaring
    `UNSUPPORTED` marks every segment `UNCERTAIN` and its transcripts filter to
    nothing. Admitting that job means three hours of transcription ending
    COMPLETED with an empty summary and nothing saying why — which looks like
    success, and is the silent degradation the capability axes exist to stop.
    """
    if classification is not ClassificationSupport.AVAILABLE:
        raise ClassificationUnsupported(
            f"engine declares non_speech_classification={classification.value}; "
            f"it cannot tell speech from music, so script artifacts would be "
            f"generated from nothing. Choose an engine that classifies."
        )


def admit_job(
    *,
    engine: EngineChoice,
    speaker_mode: SpeakerMode,
    operator: OperatorId,
    storage: TranscriptStoragePort,
    capabilities: Callable[[EngineChoice], DeclaredSupport] | None = None,
    now: Callable[[], float] = time.time,
    new_job_id: Callable[[], JobId] = generate_job_id,
    new_media_id: Callable[[], MediaId] = generate_media_id,
) -> JobRecord:
    """Both ids are minted server-side, before anything touches the filesystem.

    The media id is allocated now even though no bytes have arrived, so the
    upload that follows has somewhere to belong rather than inventing an identity
    from whatever the client sent.

    The authenticated caller is recorded as owner here and never reassigned:
    ownership is written once, at admission. The capability guard above runs
    before any of this, so a refused admission still touches no storage.

    When a capabilities callable is supplied, engine/speaker-mode compatibility
    is validated before any storage operation — IDs are not minted, no storage
    is touched, when validation fails.

    It answers with `DiarizationSupport` rather than whole capabilities because
    that is the only field read here, and the wider type is what kept the guard
    disconnected: the rest of a `TranscriptionCapabilities` cannot be known
    without constructing an engine, and the web process must not do that.
    """
    if capabilities is not None:
        declared = capabilities(engine)
        # Speaker mode first, deliberately: it is something the operator asked
        # for and can withdraw, while classification is a property of the engine
        # they picked. Naming the retractable one first offers the cheaper fix.
        _validate_compatibility(declared.diarization, speaker_mode)
        _validate_summarizable(declared.non_speech_classification)

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
        owner=operator,
    )
    storage.create_job(job)
    return job
