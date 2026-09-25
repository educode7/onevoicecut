"""The upload pipeline as a command: bytes in, then a queueable job record.

The route hands over a stream, a filename that is metadata and nothing more, and
the caller's identity; everything that decides *whether* those bytes are welcome
lives here, where it is testable without an HTTP client. Statement order is the
load-bearing part and is unchanged: ownership, state check, size pre-check, store,
re-read, probe, `save_media`, `update_job(QUEUED)`.

Two decisions in that order are worth restating, because both are easy to
reorder into a bug:

- **Ownership before the writer exists.** A non-owner's request never opens a
  partial file and never accepts a byte; the state check follows rather than
  precedes it, so a stranger learns nothing about somebody else's job.
- **Re-read after the transfer.** The record consulted before the transfer is
  hours stale by the time a multi-hour upload finishes, and the cancel it missed
  is exactly the one worth catching. Checked before the probe because probing a
  job nobody wants is wasted work.

The command carries an `AsyncIterator[bytes]` rather than a framework request:
this is application code, and the only thing it needs from HTTP is the body.
"""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace

from fastapi import HTTPException

from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.shared.application.principal import Principal
from onevoicecut.shared.domain.errors import UnsupportedContainer
from onevoicecut.shared.domain.ids import JobId
from onevoicecut.systems.pipeline.jobs.domain.interfaces.media_source import (
    MediaSourcePort,
)
from onevoicecut.systems.pipeline.jobs.domain.jobs import JobRecord, JobState
from onevoicecut.shared.domain.media import SourceMedia
from onevoicecut.systems.pipeline.jobs.domain.ownership import require_owner


def _accepting_media(job: JobRecord) -> None:
    """Media is only legal while the job is still PENDING.

    Before the cancel route existed this could not go wrong: nothing moved a job
    out of PENDING until its upload had finished. Now the operator can cancel
    mid-transfer, and an upload that committed afterwards would resurrect the
    job — bytes on disk, a media record, and a worker spawned for work that was
    explicitly called off.

    It also closes an older hazard nobody had a reason to hit: a second upload
    into a job already extracting used to be accepted, replacing the file a
    worker was reading at that moment.
    """
    if job.state is not JobState.PENDING:
        raise HTTPException(
            status_code=409,
            detail=f"job is {job.state}, which does not accept media",
        )


def _refuse_if_declared_too_large(declared: str | None, max_bytes: int) -> None:
    """The cheap half of the size limit, and the only half that costs nothing.

    `Content-Length` is a claim, so this cannot be the whole defence — the writer
    keeps counting in case the claim was false. But when a client honestly
    declares sixteen gigabytes, refusing here is the difference between an instant
    answer and an hour of transfer nobody wanted.

    An absent header means chunked transfer encoding, which is what a browser
    sends for a large file: there is simply nothing to check. An unparseable one
    is treated the same way — it tells us nothing, and it is not evidence of being
    small.
    """
    if declared is None:
        return
    try:
        length = int(declared)
    except ValueError:
        return
    if length > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"declared {length} bytes, limit is {max_bytes}",
        )


def _verified_media(
    media: SourceMedia,
    *,
    extractor: AudioExtractorPort,
    writer: MediaSourcePort,
) -> SourceMedia:
    """Decide what the file is by looking inside it, and record the answer.

    An extension is a claim by whoever named the file, and it is wrong in both
    directions: it does not stop a text file called `sermon.mp4`, and it would
    reject a real recording someone named `sermon`. So the bytes are probed and
    the probe is believed.

    The rejection worth naming is the second one. A container with no audio
    stream looks entirely fine — it extracts cleanly to a silent track and
    transcribes to an empty sermon, and nothing in the output says why. Catching
    it here means the operator hears about it while they are still standing at
    the upload form.

    A refused file is discarded rather than kept. The retention rule protects the
    operator's uploaded video; this was never accepted as one. The discard is
    this command's job; the 415 it answers with belongs to the central table, so
    the error rises untranslated.
    """
    try:
        probe = extractor.probe(media)
    except UnsupportedContainer:
        writer.discard(media)
        raise

    if not probe.has_audio:
        writer.discard(media)
        raise HTTPException(
            status_code=415,
            detail=f"{probe.container} has no audio stream to transcribe",
        )

    return replace(media, container=probe.container)


@dataclass(frozen=True, slots=True)
class IngestMediaCommand:
    """The caller, the job, and the bytes — nothing else from the request.

    `filename` is percent-decoded by the time it arrives here and is recorded as
    metadata only: storage decided where the file goes before this ran, so the
    name is never consulted as a path component.
    """

    principal: Principal
    job_id: JobId
    filename: str
    content_length: str | None
    stream: AsyncIterator[bytes]


class IngestMediaHandler:
    """Owns the store, the size ceiling and the two stream factories.

    The factories rather than their results: one writer and one extractor per
    job, built when the job is known, which is what keeps the multi-hour body
    out of memory and out of a second process.
    """

    def __init__(
        self,
        *,
        storage: TranscriptStoragePort,
        max_upload_bytes: int,
        media_source_for: Callable[[TranscriptStoragePort, JobId], MediaSourcePort],
        extractor_for: Callable[[TranscriptStoragePort, JobId], AudioExtractorPort],
        now: Callable[[], float],
    ) -> None:
        self._storage = storage
        self._max_upload_bytes = max_upload_bytes
        self._media_source_for = media_source_for
        self._extractor_for = extractor_for
        self._now = now

    async def handle(self, command: IngestMediaCommand) -> None:
        """Stream the body straight to disk, then describe it and queue it.

        `request.stream()` hands over chunks as they arrive off the socket, so
        the writer never holds the file — which is the only way a multi-hour
        upload works at all. FastAPI's `UploadFile` would spool the whole body
        first, and a test asserts that neither it nor `File`/`Form` appears
        anywhere in this module's sibling presentation.

        The filename is metadata and stays metadata: storage decided where the
        bytes go before this command was built.
        """
        job_id = command.job_id
        operator = command.principal.identity
        filename = command.filename
        content_length = command.content_length
        stream = command.stream
        storage = self._storage
        max_upload_bytes = self._max_upload_bytes
        media_source_for = self._media_source_for
        extractor_for = self._extractor_for
        now = self._now

        job = storage.load_job(job_id)
        # Ownership is decided before the writer exists: a non-owner's request
        # never opens a partial file, never accepts a byte. The state check
        # follows rather than precedes it, so a stranger learns nothing about
        # what happened to somebody else's job.
        require_owner(job, operator)
        _accepting_media(job)

        _refuse_if_declared_too_large(content_length, max_upload_bytes)

        writer = media_source_for(storage, job.job_id)
        # `UploadTooLarge` from a dishonest Content-Length claim rises to the
        # central table (413), same as the honest-declaration pre-check above.
        media = await writer.store(job.media_id, filename, stream, max_upload_bytes)

        # Read again, now that the bytes are in. The record consulted before the
        # transfer is hours stale by the time a multi-hour upload finishes, and
        # the cancel it missed is exactly the one worth catching. Checked before
        # the probe because probing a job nobody wants is wasted work.
        current = storage.load_job(job.job_id)
        if current.state is not JobState.PENDING:
            writer.discard(media)
            raise HTTPException(
                status_code=409,
                detail="job stopped accepting media while it was being uploaded",
            )

        verified = _verified_media(
            media, extractor=extractor_for(storage, job.job_id), writer=writer
        )
        # Described before it is queued, never after: QUEUED is what makes the
        # supervisor spawn a worker, and that worker's first act is to read this
        # record. The other order is a race against ourselves.
        storage.save_media(job.job_id, verified)
        # Queued, not started. This command does not spawn — the drain
        # supervisor is the only code that calls a launcher, which is what makes
        # "never exceed the cap" true by construction rather than by two code
        # paths agreeing. Written from the re-read record, not the one loaded
        # before the transfer, so nothing decided hours ago is written back.
        storage.update_job(
            replace(current, state=JobState.QUEUED, updated_at=now())
        )
