"""Generation runs after a COMPLETED transcription, or never, or not at all.

The wiring under test is the seam this feature exists for: a finished
transcript plus a configured generator plus a positive probe is the *only*
path that produces `artifacts.json`. Every other combination — no model
configured, model not pulled, Ollama dead mid-run, transcription failed, job
already terminal, targets misconfigured — leaves the job COMPLETED with its
transcript and no artifacts, because the transcript is the primary deliverable
and `ArtifactsNotAvailable`'s docstring already blesses that state.

Proven with injected spies, in the default suite: the generator factory and
the probe are parameters, so nothing here touches a live Ollama and the
assertions are about which seam was called, in what state, and what landed on
disk.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.errors import GenerationFailed
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.media import SourceMedia
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.text_generation import TextGenerationPort
from onevoicecut.ports.transcription import TranscriptionPort
from onevoicecut.runtime import worker
from onevoicecut.runtime.engine_resolver import EngineResolver
from onevoicecut.runtime.worker import (
    DEFAULT_OLLAMA_HOST,
    LLM_MODEL_ENV,
    OLLAMA_HOST_ENV,
    SCRIPT_TARGETS_ENV,
    GenerationSetup,
    configured_generation,
    run_job,
)
from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.text_generation import FakeTextGenerationPort
from tests.fakes.transcription import FakeTranscriptionPort, FlakyFakeTranscriptionPort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")

MODEL = "qwen2.5:7b-instruct"

# The fake transcriber lays out one SPEECH segment ("hola mundo") as id 0, so
# this is a valid one-window map answer for exactly that transcript.
MAP_REPLY = json.dumps(
    {
        "summary": "el resumen del sermon",
        "moments": [
            {
                "segment_ids": [0],
                "hook": "gancho",
                "quote": "cita",
                "rationale": "motivo",
                "score": 0.8,
            }
        ],
    }
)

A_SETUP = GenerationSetup(
    model=MODEL, base_url=DEFAULT_OLLAMA_HOST, script_targets="tiktok"
)


class RecordingExtractorFactory:
    def __init__(self) -> None:
        self.built: list[JobId] = []

    def __call__(self, job_dir: Path, job_id: JobId) -> AudioExtractorPort:
        self.built.append(job_id)
        return FakeAudioExtractorPort(job_id)


def _resolver(transcriber: TranscriptionPort | None = None) -> EngineResolver:
    def build() -> TranscriptionPort:
        return transcriber if transcriber is not None else FakeTranscriptionPort()

    return EngineResolver({EngineChoice.LOCAL: build})


def a_job_in(data_dir: Path, state: JobState) -> FilesystemTranscriptStorage:
    storage = FilesystemTranscriptStorage(data_dir)
    storage.create_job(
        JobRecord(
            job_id=JOB_ID,
            media_id=MEDIA_ID,
            state=state,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=1.0,
            updated_at=1.0,
            worker_pid=None,
            error=None,
            owner=OWNER,
        )
    )
    storage.save_media(
        JOB_ID,
        SourceMedia(
            media_id=MEDIA_ID,
            original_filename="predicacion.mp4",
            stored_path=storage.job_dir(JOB_ID) / "source",
            size_bytes=4096,
            container="mp4",
            checksum="deadbeef",
        ),
    )
    return storage


def _forbidden(name: str) -> Callable[..., NoReturn]:
    """Stands in for any seam that must not be reached. `NoReturn` makes it
    assignable to both the probe's and the factory's signature, so a test
    cannot pass it where the types merely happen to line up."""

    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError(f"{name} was reached when it must not have been")

    return fail


def _fake_factory(calls: list[str]) -> Callable[[GenerationSetup], TextGenerationPort]:
    def build(setup: GenerationSetup) -> TextGenerationPort:
        calls.append(setup.model)
        return FakeTextGenerationPort(replies=(MAP_REPLY, "el guion"))

    return build


class TestTheOnlyPathThatGenerates:
    def test_a_completed_job_with_a_pulled_model_saves_artifacts(
        self, tmp_path: Path
    ) -> None:
        storage = a_job_in(tmp_path, JobState.QUEUED)
        built: list[str] = []

        record = run_job(
            JOB_ID,
            tmp_path,
            resolver=_resolver(),
            extractor_factory=RecordingExtractorFactory(),
            generation=A_SETUP,
            generator_factory=_fake_factory(built),
            model_probe=lambda setup: True,
        )

        assert record.state is JobState.COMPLETED
        assert built == [MODEL]
        artifacts = storage.load_artifacts(JOB_ID)
        assert artifacts is not None
        assert artifacts.job_id == JOB_ID
        assert artifacts.summary == "el resumen del sermon"
        assert len(artifacts.clip_candidates) == 1
        assert artifacts.clip_candidates[0].variants[0].body == "el guion"

    def test_the_record_stays_the_one_transcribe_job_wrote(
        self, tmp_path: Path
    ) -> None:
        """Generation must not touch `job.json`: the single-writer rule and the
        drain's terminal classification both read the record the transcription
        wrote, and a generation pass rewriting it would be a second author."""
        storage = a_job_in(tmp_path, JobState.QUEUED)

        record = run_job(
            JOB_ID,
            tmp_path,
            resolver=_resolver(),
            extractor_factory=RecordingExtractorFactory(),
            generation=A_SETUP,
            generator_factory=_fake_factory([]),
            model_probe=lambda setup: True,
        )

        assert storage.load_job(JOB_ID) == record


class TestEveryPathThatDoesNot:
    def test_no_generation_configured_touches_nothing(self, tmp_path: Path) -> None:
        storage = a_job_in(tmp_path, JobState.QUEUED)

        record = run_job(
            JOB_ID,
            tmp_path,
            resolver=_resolver(),
            extractor_factory=RecordingExtractorFactory(),
            model_probe=_forbidden("the probe"),
            generator_factory=_forbidden("the generator factory"),
        )

        assert record.state is JobState.COMPLETED
        assert storage.load_artifacts(JOB_ID) is None

    def test_a_negative_probe_skips_generation(self, tmp_path: Path) -> None:
        """The model is not pulled: one logged line and the proven 409 stay,
        rather than a `GenerationFailed` per window after the transcript exists."""
        storage = a_job_in(tmp_path, JobState.QUEUED)

        record = run_job(
            JOB_ID,
            tmp_path,
            resolver=_resolver(),
            extractor_factory=RecordingExtractorFactory(),
            generation=A_SETUP,
            generator_factory=_forbidden("the generator factory"),
            model_probe=lambda setup: False,
        )

        assert record.state is JobState.COMPLETED
        assert storage.load_artifacts(JOB_ID) is None

    def test_a_generation_failure_leaves_the_job_completed(
        self, tmp_path: Path
    ) -> None:
        """Ollama died mid-run. The transcription already succeeded, and a dead
        LLM must never eat it: the record stays COMPLETED, artifacts stay
        absent, and the error is a line on stderr, not a job state."""
        storage = a_job_in(tmp_path, JobState.QUEUED)

        def dying_factory(setup: GenerationSetup) -> TextGenerationPort:
            return FakeTextGenerationPort(fail_with=GenerationFailed("ollama died"))

        record = run_job(
            JOB_ID,
            tmp_path,
            resolver=_resolver(),
            extractor_factory=RecordingExtractorFactory(),
            generation=A_SETUP,
            generator_factory=dying_factory,
            model_probe=lambda setup: True,
        )

        assert record.state is JobState.COMPLETED
        assert storage.load_job(JOB_ID).state is JobState.COMPLETED
        assert storage.load_artifacts(JOB_ID) is None
        assert storage.load_transcript(JOB_ID) is not None

    def test_misconfigured_script_targets_leave_the_job_completed(
        self, tmp_path: Path
    ) -> None:
        """`resolve_script_targets` refuses an empty selection with a
        `GenerationFailed`; a config typo is no reason to fail a finished
        transcription either."""
        storage = a_job_in(tmp_path, JobState.QUEUED)
        broken = GenerationSetup(
            model=MODEL, base_url=DEFAULT_OLLAMA_HOST, script_targets=" , "
        )

        record = run_job(
            JOB_ID,
            tmp_path,
            resolver=_resolver(),
            extractor_factory=RecordingExtractorFactory(),
            generation=broken,
            generator_factory=_fake_factory([]),
            model_probe=lambda setup: True,
        )

        assert record.state is JobState.COMPLETED
        assert storage.load_artifacts(JOB_ID) is None

    def test_a_failed_transcription_never_generates(self, tmp_path: Path) -> None:
        storage = a_job_in(tmp_path, JobState.QUEUED)

        record = run_job(
            JOB_ID,
            tmp_path,
            resolver=_resolver(FlakyFakeTranscriptionPort(failures={0: 99})),
            extractor_factory=RecordingExtractorFactory(),
            generation=A_SETUP,
            generator_factory=_forbidden("the generator factory"),
            model_probe=_forbidden("the probe"),
        )

        assert record.state is JobState.FAILED
        assert storage.load_artifacts(JOB_ID) is None

    def test_an_already_terminal_job_never_generates(self, tmp_path: Path) -> None:
        """The early-return path: a worker born for a job that finished (the
        losing half of the spawn-versus-cancel race) must not see a COMPLETED
        record and decide generation is owed."""
        storage = a_job_in(tmp_path, JobState.COMPLETED)

        record = run_job(
            JOB_ID,
            tmp_path,
            resolver=_resolver(),
            extractor_factory=RecordingExtractorFactory(),
            generation=A_SETUP,
            generator_factory=_forbidden("the generator factory"),
            model_probe=_forbidden("the probe"),
        )

        assert record.state is JobState.COMPLETED
        assert storage.load_artifacts(JOB_ID) is None


class TestReadingTheConfiguration:
    def test_the_model_comes_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(LLM_MODEL_ENV, MODEL)
        monkeypatch.setenv(OLLAMA_HOST_ENV, "http://gpu-box:11434")
        monkeypatch.setenv(SCRIPT_TARGETS_ENV, "tiktok,youtube")

        assert configured_generation() == GenerationSetup(
            model=MODEL, base_url="http://gpu-box:11434", script_targets="tiktok,youtube"
        )

    def test_the_host_and_targets_have_defaults(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from onevoicecut.usecases.generate_artifacts import DEFAULT_SCRIPT_TARGETS

        monkeypatch.setenv(LLM_MODEL_ENV, MODEL)
        monkeypatch.delenv(OLLAMA_HOST_ENV, raising=False)
        monkeypatch.delenv(SCRIPT_TARGETS_ENV, raising=False)

        assert configured_generation() == GenerationSetup(
            model=MODEL,
            base_url=DEFAULT_OLLAMA_HOST,
            script_targets=DEFAULT_SCRIPT_TARGETS,
        )

    def test_an_unset_model_registers_no_generation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No default, the `LOCAL_MODEL_SIZE` lesson: which model writes the
        scripts decides their quality, and a default would make that choice
        invisible at the one place it is made."""
        monkeypatch.delenv(LLM_MODEL_ENV, raising=False)

        assert configured_generation() is None

    def test_a_blank_model_is_absent_rather_than_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(LLM_MODEL_ENV, "   ")

        assert configured_generation() is None


class TestTheEntrypointWiring:
    def test_main_passes_the_configured_generation_to_run_job(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        seen: list[object] = []

        monkeypatch.setattr(worker, "configured_resolver", lambda: EngineResolver({}))
        monkeypatch.setattr(worker, "configured_generation", lambda: A_SETUP)
        monkeypatch.setattr(worker, "run_job", _run_job_spy(seen))

        worker.main(["--job-id", str(JOB_ID), "--data-dir", str(tmp_path)])

        assert seen == [A_SETUP]

    def test_an_injected_resolver_means_the_environment_is_not_read(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The E2E harness drives `main` with a fake engine. Reading the
        machine's generation config anyway would leak it into a run that
        supplied its own — the same rule `configured_resolver` is behind."""
        seen: list[object] = []

        monkeypatch.setattr(
            worker,
            "configured_generation",
            _forbidden("configured_generation"),
        )
        monkeypatch.setattr(worker, "run_job", _run_job_spy(seen))

        worker.main(
            ["--job-id", str(JOB_ID), "--data-dir", str(tmp_path)],
            resolver=EngineResolver({}),
        )

        assert seen == [None]


def _run_job_spy(seen: list[object]) -> object:
    def spy(
        job_id: JobId,
        data_dir: Path,
        *,
        resolver: EngineResolver,
        extractor_factory: object = None,
        chunk_timeout_s: float = 0.0,
        generation: GenerationSetup | None = None,
        generator_factory: object = None,
        model_probe: object = None,
    ) -> JobRecord:
        seen.append(generation)
        return JobRecord(
            job_id=job_id,
            media_id=MEDIA_ID,
            state=JobState.COMPLETED,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=1.0,
            updated_at=2.0,
            worker_pid=1,
            error=None,
            owner=OWNER,
        )

    return spy
