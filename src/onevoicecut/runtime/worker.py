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
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol, runtime_checkable

from onevoicecut.adapters.asr.local.declarations import HF_TOKEN_ENV
from onevoicecut.adapters.ffmpeg.extractor import FfmpegAudioExtractor
from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.errors import DomainError
from onevoicecut.domain.ids import InvalidIdError, JobId, make_job_id
from onevoicecut.domain.jobs import TERMINAL_STATES, JobRecord, JobState
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.text_generation import TextGenerationPort
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.ports.transcription import TranscriptionPort
from onevoicecut.runtime.engine_resolver import EngineResolver, production_factories
from onevoicecut.runtime.settings import CHUNK_TIMEOUT_ENV_NAMES, load_env_file
from onevoicecut.usecases.generate_artifacts import (
    DEFAULT_SCRIPT_TARGETS,
    resolve_script_targets,
    run_generation,
)
from onevoicecut.usecases.transcribe_job import DEFAULT_CHUNK_TIMEOUT_S, transcribe_job

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

# The LLM that writes the script artifacts, and no default — the
# LOCAL_MODEL_SIZE lesson restated on a second axis: which model writes the
# scripts decides the quality of every artifact, and a default would hide that
# choice at the one place it is made. Unset registers no generator at all;
# jobs then stay COMPLETED with no artifacts, and clips keep answering the
# proven `ArtifactsNotAvailable` 409.
LLM_MODEL_ENV = "ONEVOICECUT_LLM_MODEL"

# Ollama is a system service on this machine, like ffmpeg — installed by the
# operator, never a pip dependency. The default is the address its installer
# binds, which is why it is the one variable here that may safely have one.
OLLAMA_HOST_ENV = "ONEVOICECUT_OLLAMA_HOST"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"

# The web process reads the same variable into `Settings`; this is a separate
# program and cannot be handed the parsed value — the `CHUNK_TIMEOUT_ENV_NAMES`
# reasoning, on a third variable. Parsed here by the use case's own resolver,
# so a spelling cannot drift into two meanings.
SCRIPT_TARGETS_ENV = "ONEVOICECUT_SCRIPT_TARGETS"


@dataclass(frozen=True, slots=True)
class GenerationSetup:
    """The values generation needs, already read from the environment.

    Data rather than a constructed generator so `configured_generation()`
    stays as cheap as `configured_resolver()` — registration reads names,
    construction happens at the seam where a test can watch it.
    """

    model: str
    base_url: str
    script_targets: str


GeneratorFactory = Callable[[GenerationSetup], TextGenerationPort]
ModelProbe = Callable[[GenerationSetup], bool]


def _ollama_generator(generation: GenerationSetup) -> TextGenerationPort:
    """Imports the adapter when called, the engine-factory discipline.

    `httpx` is light, so the deferral is not about import weight the way
    `local_transcriber`'s is — it is the same claim `cloud_transcriber` makes:
    a composition root names the adapters this build has, it does not carry
    them, so swapping providers cannot change what importing this module costs.
    """
    from onevoicecut.adapters.llm.ollama_generator import OllamaTextGenerator

    return OllamaTextGenerator(generation.model, base_url=generation.base_url)


def _ollama_probe(generation: GenerationSetup) -> bool:
    from onevoicecut.adapters.llm.probe import model_is_pulled

    return model_is_pulled(generation.model, base_url=generation.base_url)

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
    chunk_timeout_s: float = DEFAULT_CHUNK_TIMEOUT_S,
    generation: GenerationSetup | None = None,
    generator_factory: GeneratorFactory = _ollama_generator,
    model_probe: ModelProbe = _ollama_probe,
) -> JobRecord:
    """Wire the adapters for one job and run it.

    The engine is resolved *before* any work starts, so a missing API key fails
    here rather than three hours in, after the local work is done.

    Generation, by contrast, runs *after* the transcription has succeeded, and
    only then: it consumes the finished transcript, and its failure must never
    become the job's — see `_generate_artifacts`.
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
        record = transcribe_job(
            job_id,
            storage.load_media(job_id),
            extractor=extractor_factory(storage.job_dir(job_id), job_id),
            transcriber=transcriber,
            storage=storage,
            now=now,
            chunk_timeout_s=chunk_timeout_s,
        )
    finally:
        _release(transcriber)

    if generation is not None and record.state is JobState.COMPLETED:
        _generate_artifacts(
            record.job_id,
            storage,
            generation,
            generator_factory=generator_factory,
            model_probe=model_probe,
        )
    return record


def _generate_artifacts(
    job_id: JobId,
    storage: TranscriptStoragePort,
    generation: GenerationSetup,
    *,
    generator_factory: GeneratorFactory,
    model_probe: ModelProbe,
) -> None:
    """Best-effort artifacts for a transcription that already succeeded.

    The record is COMPLETED before this function is reached and nothing here
    may change that. The transcript is the primary deliverable; COMPLETED with
    no artifacts is a state `ArtifactsNotAvailable` was written to describe,
    and the clip routes already answer it with the proven 409. So every domain
    failure — a dead server, a refused target list, a malformed answer in
    window one — becomes a line on stderr instead of a job state: a dead
    Ollama must never eat a finished three-hour transcription.

    The probe runs first and its negative is a *skip*, not a failure. Spending
    one cheap `GET /api/tags` to learn the model was never pulled beats a
    `GenerationFailed` per window after the transcript exists, and the log
    line carries the `ollama pull` remedy.
    """
    if not model_probe(generation):
        print(
            f"transcribe-worker: model {generation.model} is not pulled on the "
            f"Ollama server at {generation.base_url}; skipping artifact "
            f"generation (remedy: `ollama pull {generation.model}`)",
            file=sys.stderr,
        )
        return

    generator: TextGenerationPort | None = None
    try:
        generator = generator_factory(generation)
        transcript = storage.load_transcript(job_id)
        if transcript is None:
            print(
                f"transcribe-worker: job {job_id} completed with no readable "
                f"transcript; skipping artifact generation",
                file=sys.stderr,
            )
            return
        targets = resolve_script_targets(generation.script_targets)
        artifacts = run_generation(transcript, generate=generator, targets=targets)
        storage.save_artifacts(job_id, artifacts)
    except DomainError as error:
        print(
            f"transcribe-worker: artifact generation failed; the job stays "
            f"COMPLETED with its transcript: {error}",
            file=sys.stderr,
        )
    finally:
        if isinstance(generator, _Closable):
            generator.close()


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


def configured_generation() -> GenerationSetup | None:
    """The LLM configuration this process has, or `None` for none.

    The same composition-root act `configured_resolver` performs, under the
    same no-default rule for the value that decides quality: an unset model
    registers no generator, rather than a generator that discovers on its first
    call that it was never configured. Blank reads as absent through
    `_configured`, so a half-written `.env` cannot register a model named "".
    """
    model = _configured(LLM_MODEL_ENV)
    if model is None:
        return None
    return GenerationSetup(
        model=model,
        base_url=_configured(OLLAMA_HOST_ENV) or DEFAULT_OLLAMA_HOST,
        script_targets=_configured(SCRIPT_TARGETS_ENV) or DEFAULT_SCRIPT_TARGETS,
    )


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
        # The diarization licence credential. Absent is the normal case — the
        # local engine then declares REQUIRES_SETUP and a speaker-mode job is
        # refused up front rather than delivered unlabelled.
        hf_token=_configured(HF_TOKEN_ENV),
    )
    return EngineResolver(factories) if factories else None


class BadTimeout(ValueError):
    """A configured per-chunk budget this process cannot use.

    Its own type rather than a bare `ValueError` so `main` can refuse before the
    job is touched, which is where the distinction matters: a worker that
    claimed the record and then exited would leave the drain counting a slot as
    busy for a process that is already gone.
    """


def configured_chunk_timeout_s() -> float:
    """The per-chunk budget the adapters are given, from this process's own
    environment.

    The web process reads these same variables into `Settings` for the watchdog.
    This is a separate program: it cannot be handed that value, so it reads them
    again — under the shared names, in the shared precedence — and the two
    enforcement paths agree because they are reading the same thing rather than
    because someone remembered to keep them in step.

    A bad value refuses instead of falling back. The web process already declines
    to boot on one (`gt=0`), so silently substituting thirty minutes here would
    enforce a budget the operator did not ask for, in the one process where it
    actually applies.
    """
    for name in CHUNK_TIMEOUT_ENV_NAMES:
        raw = _configured(name)
        if raw is None:
            continue
        try:
            seconds = float(raw)
        except ValueError as error:
            raise BadTimeout(
                f"{name}={raw!r} is not a number of seconds"
            ) from error
        if seconds <= 0:
            # Same bound the web process enforces. Zero is not "no timeout", it
            # is a budget every chunk breaches on its first second.
            raise BadTimeout(f"{name}={raw!r} must be greater than zero")
        return seconds

    return DEFAULT_CHUNK_TIMEOUT_S


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
    generation: GenerationSetup | None = None,
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
    # configuration leak into a run that supplied its own — which is why the
    # `.env` load lives inside this branch too: it is part of consulting the
    # environment, and a spawned worker already inherits the web process's
    # loaded values, so override=False keeps the two sources in agreement.
    # The generation config obeys the same rule for the same reason: it is read
    # here, or handed in, and an injected engine implies an injected generator.
    if resolver is None:
        load_env_file()
        resolver = configured_resolver()
        generation = configured_generation()
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
        # Before the record is touched, for the same reason the engine check is:
        # a worker that claimed the job and then exited would hold a slot for a
        # process that is already gone.
        chunk_timeout_s = configured_chunk_timeout_s()
    except BadTimeout as error:
        print(f"transcribe-worker: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    try:
        job = run_job(
            job_id,
            args.data_dir,
            resolver=resolver,
            extractor_factory=extractor_factory,
            chunk_timeout_s=chunk_timeout_s,
            generation=generation,
        )
    except DomainError as error:
        # Every failure crossing a port is already a domain error, so the worker
        # reports it rather than printing a traceback at an operator.
        print(f"transcribe-worker: {error}", file=sys.stderr)
        return EXIT_FAILED

    return _EXIT_FOR_STATE.get(job.state, EXIT_FAILED)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess
    raise SystemExit(main())
