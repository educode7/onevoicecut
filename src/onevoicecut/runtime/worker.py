"""Headless entrypoint: one supervised process per job.

    python -m onevoicecut.runtime.worker --job-id <ulid>

Not a thread and not a queue. A process is what can be killed when a three-hour
job goes wrong, and what the operating system cleans up when it does. It is also
what makes the single-writer rule enforceable rather than aspirational: while this
process lives it is the only writer of that job's record.

Everything above this module is already pure. This is where the real adapters are
finally constructed, which is why it is the only place a secret or a filesystem
root appears.
"""

import argparse
import os
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import replace
from pathlib import Path

from onevoicecut.adapters.ffmpeg.extractor import FfmpegAudioExtractor
from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.errors import DomainError
from onevoicecut.domain.ids import InvalidIdError, JobId, make_job_id
from onevoicecut.domain.jobs import TERMINAL_STATES, JobRecord, JobState
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.runtime.engine_resolver import EngineResolver
from onevoicecut.usecases.transcribe_job import transcribe_job

ExtractorFactory = Callable[[Path, JobId], AudioExtractorPort]

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CANCELLED = 2
EXIT_UNUSABLE = 3

_EXIT_FOR_STATE = {
    JobState.COMPLETED: EXIT_OK,
    JobState.FAILED: EXIT_FAILED,
    JobState.CANCELLED: EXIT_CANCELLED,
}


def _ffmpeg_extractor(job_dir: Path, job_id: JobId) -> AudioExtractorPort:
    return FfmpegAudioExtractor(job_dir, job_id=job_id)


def run_job(
    job_id: JobId,
    data_dir: Path,
    *,
    resolver: EngineResolver,
    extractor_factory: ExtractorFactory = _ffmpeg_extractor,
    now: Callable[[], float] = time.time,
) -> JobRecord:
    """Wire the adapters for one job and run it.

    The engine is resolved *before* any work starts, so a missing API key fails
    here rather than three hours in, after the local work is done.
    """
    storage = FilesystemTranscriptStorage(data_dir)
    job = storage.load_job(job_id)

    # Already finished: leave without touching anything. This is the losing half
    # of the spawn-versus-cancel race — the drain re-read said QUEUED, and the
    # cancel landed in the moment between that read and this process starting.
    #
    # Returning *before* the claim is the point. A `worker_pid` written onto a
    # cancelled record would make it read as worker-bound to the next drain
    # sweep, so a job nobody wants would hold the machine's only slot until
    # something noticed the process was gone.
    if job.state in TERMINAL_STATES:
        return job

    # Claim the job by writing this process's pid, before any work starts.
    # Startup reconciliation reads it to tell a worker that died from one that is
    # still going; without it every running job would look abandoned after a web
    # restart and be marked INTERRUPTED out from under a live process.
    storage.update_job(replace(job, worker_pid=os.getpid()))
    # Immediately, not after the first chunk. Extraction on a three-hour file
    # happens before any boundary exists, so a worker that died there would
    # otherwise have left no evidence it ever ran — and its slot would be held
    # on the strength of a pid alone.
    storage.write_heartbeat(job_id, at_s=now())

    return transcribe_job(
        job_id,
        storage.load_media(job_id),
        extractor=extractor_factory(storage.job_dir(job_id), job_id),
        transcriber=resolver.resolve(job.engine),
        storage=storage,
        now=now,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    resolver: EngineResolver | None = None,
    extractor_factory: ExtractorFactory = _ffmpeg_extractor,
) -> int:
    """Exit code carries the outcome, because the supervisor reads it, not stdout.

    A failed job and a cancelled one are deliberately different codes: one wants
    investigating, the other was asked for.
    """
    parser = argparse.ArgumentParser(prog="transcribe-worker")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        job_id = make_job_id(args.job_id)
    except InvalidIdError as error:
        print(f"transcribe-worker: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    if resolver is None:
        # Real engines land in slices 7a/8a. Until then nothing can be resolved,
        # and saying so beats failing later with a KeyError.
        print(
            "transcribe-worker: no ASR engine is configured in this build",
            file=sys.stderr,
        )
        return EXIT_UNUSABLE

    try:
        job = run_job(
            job_id,
            args.data_dir,
            resolver=resolver,
            extractor_factory=extractor_factory,
        )
    except DomainError as error:
        # Every failure crossing a port is already a domain error, so the worker
        # reports it rather than printing a traceback at an operator.
        print(f"transcribe-worker: {error}", file=sys.stderr)
        return EXIT_FAILED

    return _EXIT_FOR_STATE.get(job.state, EXIT_FAILED)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
