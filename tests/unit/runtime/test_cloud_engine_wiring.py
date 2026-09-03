"""The second engine reaches the resolver, and the build stops being local-only.

Slice 8a-i built the cloud adapter and nothing constructed it. This is the wiring:
`production_factories` registers it, `configured_resolver` reads its key, and the
worker's refusal stops assuming the only engine anyone could have is the local one.

The registration rule is the same one the local half already follows, and it is
worth stating because it is a rule rather than a coincidence: **a missing required
value registers no engine, rather than a broken one**. An unset model size does not
become `tiny`; an unset API key does not become an adapter that fails on its first
request. The build says which engine it cannot run and names the variable, which is
the no-substitution discipline the resolver applies between engines, applied one
level out to whether an engine exists at all.

The key never travels on argv. The worker is spawned with a job id and a data dir
and inherits everything else, because argv is readable by every user on a shared
machine — and this is a shared-server app.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.asr.cloud.openai_whisper_adapter import (
    CLOUD_API_KEY_ENV,
    ENGINE_NAME,
)
from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.errors import EngineUnavailable, ExtractionFailed
from onevoicecut.domain.ids import (
    JobId,
    make_job_id,
    make_media_id,
    make_operator_id,
)
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.media import SourceMedia
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.transcription import TranscriptionPort
from onevoicecut.runtime import worker
from onevoicecut.runtime.engine_resolver import EngineResolver, production_factories
from onevoicecut.runtime.worker import (
    EXIT_UNUSABLE,
    LOCAL_MODEL_SIZE_ENV,
    configured_resolver,
)
from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.transcription import FakeTranscriptionPort

JOB_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFD"
JOB = make_job_id(JOB_ID)
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")
API_KEY = "sk-test-not-a-real-key"
MODEL_SIZE = "small"


def _argv(data_dir: Path) -> list[str]:
    return ["--job-id", JOB_ID, "--data-dir", str(data_dir)]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A queued cloud job on disk, ready for the worker to claim."""
    storage = FilesystemTranscriptStorage(tmp_path)
    storage.create_job(_queued())
    storage.save_media(
        JOB,
        SourceMedia(
            media_id=MEDIA_ID,
            original_filename="predicacion.mp4",
            stored_path=storage.job_dir(JOB) / "source",
            size_bytes=4096,
            container="mp4",
            checksum="deadbeef",
        ),
    )
    return tmp_path


def _only_cloud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LOCAL_MODEL_SIZE_ENV, raising=False)
    monkeypatch.setenv(CLOUD_API_KEY_ENV, API_KEY)


def _nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LOCAL_MODEL_SIZE_ENV, raising=False)
    monkeypatch.delenv(CLOUD_API_KEY_ENV, raising=False)


class TestTheFactoryMap:
    def test_a_key_registers_the_cloud_engine(self) -> None:
        assert EngineChoice.CLOUD in production_factories(
            local_model_size=None, cloud_api_key=API_KEY
        )

    def test_no_key_registers_no_cloud_engine(self) -> None:
        """Not a broken adapter that discovers the problem on its first request
        — an absence, the same shape an unchosen model size takes."""
        assert production_factories(local_model_size=None, cloud_api_key=None) == {}

    def test_the_two_engines_are_independent(self) -> None:
        """Configuring one must not register the other. The resolver refuses to
        substitute between them, and that refusal is worth nothing if the map
        quietly contains an engine nobody configured."""
        local_only = production_factories(local_model_size=MODEL_SIZE, cloud_api_key=None)
        cloud_only = production_factories(local_model_size=None, cloud_api_key=API_KEY)

        assert set(local_only) == {EngineChoice.LOCAL}
        assert set(cloud_only) == {EngineChoice.CLOUD}

    def test_both_configured_registers_both(self) -> None:
        factories = production_factories(
            local_model_size=MODEL_SIZE, cloud_api_key=API_KEY
        )

        assert set(factories) == {EngineChoice.LOCAL, EngineChoice.CLOUD}

    def test_registration_constructs_nothing(self) -> None:
        """Building the map must stay cheap. Construction — and therefore the
        key check, the model load, the device proof — waits for `resolve()`,
        which is what makes a missing resource an error before the job."""
        factories = production_factories(local_model_size=None, cloud_api_key="   ")

        assert callable(factories[EngineChoice.CLOUD])


class TestResolvingTheCloudEngine:
    def test_it_builds_the_real_adapter(self) -> None:
        resolver = EngineResolver(
            production_factories(local_model_size=None, cloud_api_key=API_KEY)
        )

        assert ENGINE_NAME in resolver.resolve(EngineChoice.CLOUD).capabilities().engine_id

    def test_a_blank_key_surfaces_at_resolution_not_mid_run(self) -> None:
        """The map is built from whatever the environment held; the adapter is
        what judges it. Deferring the judgement to `resolve()` is what puts the
        failure before the job rather than on the first cloud call."""
        resolver = EngineResolver(
            production_factories(local_model_size=None, cloud_api_key="   ")
        )

        with pytest.raises(EngineUnavailable, match=CLOUD_API_KEY_ENV):
            resolver.resolve(EngineChoice.CLOUD)

    def test_a_cloud_only_build_still_refuses_local_by_name(self) -> None:
        """The privacy-critical direction. A job that chose the local engine
        because its material is private must never quietly reach a provider
        because that is the only engine this build happens to have."""
        resolver = EngineResolver(
            production_factories(local_model_size=None, cloud_api_key=API_KEY)
        )

        with pytest.raises(EngineUnavailable, match="local"):
            resolver.resolve(EngineChoice.LOCAL)


class TestReadingTheKey:
    def test_it_comes_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _only_cloud(monkeypatch)

        resolver = configured_resolver()

        assert resolver is not None
        assert ENGINE_NAME in resolver.resolve(EngineChoice.CLOUD).capabilities().engine_id

    def test_a_blank_key_is_an_absent_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An exported-but-empty variable is the shape a half-written `.env`
        takes. The same normalization the model size already gets."""
        monkeypatch.delenv(LOCAL_MODEL_SIZE_ENV, raising=False)
        monkeypatch.setenv(CLOUD_API_KEY_ENV, "   ")

        assert configured_resolver() is None

    def test_a_key_with_a_trailing_newline_still_configures(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Reading a key out of a file is the normal way to hold one, and every
        such read carries the newline with it."""
        monkeypatch.delenv(LOCAL_MODEL_SIZE_ENV, raising=False)
        monkeypatch.setenv(CLOUD_API_KEY_ENV, f"{API_KEY}\n")

        assert configured_resolver() is not None

    def test_neither_configured_is_no_resolver_at_all(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _nothing(monkeypatch)

        assert configured_resolver() is None


class TestTheWorkerRefusal:
    def test_it_names_both_variables_now_that_there_are_two_engines(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """The message predates the cloud engine and told every operator to set
        a faster-whisper model size. An operator who has an API key and no
        intention of running a local model was being sent to fix the wrong
        thing — a refusal that names its own remedy has to name the right one.
        """
        _nothing(monkeypatch)

        assert worker.main(_argv(tmp_path)) == EXIT_UNUSABLE

        printed = capsys.readouterr().err
        assert LOCAL_MODEL_SIZE_ENV in printed
        assert CLOUD_API_KEY_ENV in printed

    def test_a_cloud_only_build_is_usable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Exit 3 means "this build can run nothing". A build with a cloud key
        can run cloud jobs, so refusing it would strand every one of them."""
        _only_cloud(monkeypatch)
        monkeypatch.setattr(worker, "run_job", lambda *a, **k: _completed())

        assert worker.main(_argv(tmp_path)) != EXIT_UNUSABLE


class TestReleasingTheAdapter:
    """`run_job` closes what it resolved.

    Narrower than it first looked. One worker process builds one adapter and
    then exits, so the connection pool dies with the process either way — this
    is not a leak that outlives anything. What it is: an `httpx.Client` nobody
    closed, which is the difference between releasing a socket deliberately and
    letting interpreter shutdown do it, and it costs a `finally` to fix.

    The failure path matters more than the happy one. A job that raises is
    exactly when an adapter is most likely to be holding an open connection.
    """

    def test_a_finished_job_releases_the_engine(self, data_dir: Path) -> None:
        engine = _ClosableEngine()

        worker.run_job(
            JOB, data_dir, resolver=_resolving(engine), extractor_factory=_extractor
        )

        assert engine.closed

    def test_a_failing_job_still_releases_the_engine(self, data_dir: Path) -> None:
        """The path that matters more. A job that raises is exactly when an
        adapter is most likely to be holding an open connection."""
        engine = _ClosableEngine()

        with pytest.raises(ExtractionFailed):
            worker.run_job(
                JOB, data_dir, resolver=_resolving(engine), extractor_factory=_exploding
            )

        assert engine.closed

    def test_an_engine_with_nothing_to_close_is_left_alone(
        self, data_dir: Path
    ) -> None:
        """The local adapter holds no closable resource and has no `close`, and
        `TranscriptionPort` does not declare one. Closing must stay optional
        rather than becoming a method every future adapter implements empty.
        """
        worker.run_job(
            JOB,
            data_dir,
            resolver=EngineResolver({EngineChoice.CLOUD: FakeTranscriptionPort}),
            extractor_factory=_extractor,
        )


class _ClosableEngine(FakeTranscriptionPort):
    def __init__(self) -> None:
        super().__init__()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _resolving(engine: TranscriptionPort) -> EngineResolver:
    return EngineResolver({EngineChoice.CLOUD: lambda: engine})


def _extractor(job_dir: Path, job_id: JobId) -> AudioExtractorPort:
    return FakeAudioExtractorPort(job_id)


def _exploding(job_dir: Path, job_id: JobId) -> AudioExtractorPort:
    raise ExtractionFailed("ffmpeg died")


def _queued() -> JobRecord:
    return _record(JobState.QUEUED)


def _completed() -> JobRecord:
    return _record(JobState.COMPLETED)


def _record(state: JobState) -> JobRecord:
    return JobRecord(
        job_id=JOB,
        media_id=MEDIA_ID,
        state=state,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.CLOUD,
        created_at=1.0,
        updated_at=2.0,
        worker_pid=None,
        error=None,
        owner=OWNER,
    )
