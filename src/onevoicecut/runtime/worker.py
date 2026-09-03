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
from typing import Protocol, runtime_checkable

from onevoicecut.adapters.ffmpeg.extractor import FfmpegAudioExtractor
from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.errors import DomainError
from onevoicecut.domain.ids import InvalidIdError, JobId, make_job_id
from onevoicecut.domain.jobs import TERMINAL_STATES, JobRecord, JobState
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.transcription import TranscriptionPort
from onevoicecut.runtime.engine_resolver import EngineResolver, production_factories
from onevoicecut.usecases.transcribe_job import transcribe_job

ExtractorFactory = Callable[[Path, JobId], AudioExtractorPort]

# Read here rather than passed on argv. The supervisor spawns this process with
# a job id and a data dir and nothing else, deliberately — argv is visible to
# every user on a shared machine, which is why secrets travel the same way. The
# environment is inherited from the web process, so one export configures both.
LOCAL_MODEL_SIZE_ENV = "ONEVOICECUT_LOCAL_MODEL_SIZE"

# `auto` keeps CTranslate2's own choice, which prefers a GPU when one is present.
# It stays the default because the operator who has a working GPU should not have
# to ask for it — and the adapter now proves the chosen device can actually
# compute before a job starts, so `auto` picking an unusable one is an error at
# resolution rather than a job that dies on its first chunk with speech in it.
LOCAL_DEVICE_ENV = "ONEVOICECUT_LOCAL_DEVICE"
DEFAULT_LOCAL_DEVICE = "auto"

# The one variable here that carries a secret, which is exactly why it travels
# this way and not on argv. It keeps the name the task list gave it rather than
# the project's `ONEVOICECUT_` prefix; the adapter names it in its own refusal,
# so the two must agree.
CLOUD_API_KEY_ENV = "CLOUD_ASR_API_KEY"

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

    # Resolved before the extractor, which is what this function's docstring
    # already claimed. Keyword arguments evaluate left to right, so building the
    # extractor inline ahead of it meant an extraction failure could preempt the
    # engine's own refusal — and the engine is the one that costs a model load,
    # a device proof or an API key check to find out about.
    transcriber = resolver.resolve(job.engine)
    try:
        return transcribe_job(
            job_id,
            storage.load_media(job_id),
            extractor=extractor_factory(storage.job_dir(job_id), job_id),
            transcriber=transcriber,
            storage=storage,
            now=now,
        )
    finally:
        _release(transcriber)


@runtime_checkable
class _Closable(Protocol):
    def close(self) -> None: ...


def _release(transcriber: TranscriptionPort) -> None:
    """Hand back whatever the adapter was holding.

    `TranscriptionPort` deliberately does not declare `close`. The local engine
    holds nothing releasable and would have to implement one empty, which is the
    kind of method that later gets called on the wrong thing. The cloud adapter
    holds an `httpx` connection pool, so it has one and this finds it.

    Narrow by design: one worker process builds one adapter and then exits, so
    the pool dies with the process either way. The difference is between
    releasing a socket deliberately and leaving it to interpreter shutdown — and
    the failure path is where it earns its keep, because a job that raised is
    exactly when a connection is most likely to still be open.
    """
    if isinstance(transcriber, _Closable):
        transcriber.close()


def configured_resolver() -> EngineResolver | None:
    """The engines this machine is configured to run, or `None` for none of them.

    This process is a composition root in its own right — it is where the real
    adapters are constructed — so reading its own environment here is the same
    act `runtime/app.py` performs for the web process, not a use case reaching
    for configuration.

    A resolver comes back when *either* engine is configured. A machine with an
    API key and no local model is a perfectly usable cloud-only build, and one
    with a model and no key is the offline install this project was designed
    around; only a build with neither can run nothing.
    """
    factories = production_factories(
        local_model_size=_configured(LOCAL_MODEL_SIZE_ENV),
        local_device=_configured(LOCAL_DEVICE_ENV) or DEFAULT_LOCAL_DEVICE,
        cloud_api_key=_configured(CLOUD_API_KEY_ENV),
    )
    return EngineResolver(factories) if factories else None


def _configured(name: str) -> str | None:
    """An environment variable, with blank read as absent.

    An exported-but-empty variable is the shape a half-written `.env` or a shell
    typo takes, and forwarding `""` would reach an adapter that then fails
    loading a model named nothing, or authenticating with an empty key — errors
    about the wrong thing.

    Stripping also matters on its own: a key read out of a file carries the
    newline with it, and a newline in an HTTP header value is header injection,
    which the client rejects outright.
    """
    return os.environ.get(name, "").strip() or None


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

    # An injected resolver wins: the E2E harness drives this entrypoint with a
    # fake engine, and reading the environment anyway would let the machine's
    # configuration leak into a run that supplied its own.
    resolver = resolver if resolver is not None else configured_resolver()
    if resolver is None:
        # Before the record is touched. A worker that claimed the job, wrote its
        # pid and then exited would leave the drain counting a slot as busy for a
        # process that is already gone.
        # Both engines are named. The message predates the cloud adapter and
        # sent every operator to set a model size — including one who has an API
        # key and no intention of ever running a local model, for whom that was
        # the wrong remedy stated with complete confidence.
        print(
            f"transcribe-worker: no ASR engine is configured; set "
            f"{LOCAL_MODEL_SIZE_ENV} to a faster-whisper model size "
            f"(tiny | base | small | medium | large-v3), or {CLOUD_API_KEY_ENV} "
            f"to run jobs on the cloud engine",
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
