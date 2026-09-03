"""The worker builds a real engine, instead of announcing it cannot.

Slice 7a-ii registered the local adapter behind a lazy factory and stopped there:
`main` still passed `resolver=None` and exited 3, so a spawned worker reported
"no ASR engine is configured in this build" on a machine where the engine was
installed and working. The pipeline ran end to end only against a fake.

The missing piece was never code — it was the model size. It decides transcript
quality and hours of runtime and it is recorded on every chunk result as
provenance, so the adapter refuses to invent one and `production_factories`
refuses in turn. Somewhere it has to be *chosen*, and a process entrypoint
reading its own environment is where a composition root is allowed to choose.

Which is also why an unset variable does not fall back to a default size. The
local engine is simply not configured, `production_factories` returns nothing for
it, and the worker says so by name before it touches a job — the same
no-substitution rule the resolver already applies between engines.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from onevoicecut.domain.errors import EngineUnavailable
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import (
    EngineChoice,
    JobRecord,
    JobState,
    SpeakerMode,
)
from onevoicecut.runtime import worker
from onevoicecut.runtime.engine_resolver import EngineResolver, production_factories
from onevoicecut.adapters.asr.local.declarations import HF_TOKEN_ENV
from onevoicecut.runtime.worker import (
    EXIT_UNUSABLE,
    LOCAL_MODEL_SIZE_ENV,
    configured_resolver,
)

JOB_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFD"
MODEL_SIZE = "small"


def _argv(data_dir: Path) -> list[str]:
    return ["--job-id", JOB_ID, "--data-dir", str(data_dir)]


class TestTheFactoryMap:
    def test_an_unchosen_model_size_registers_no_local_engine(self) -> None:
        """Not a default — an absence. A build with no chosen size cannot run the
        local engine, and saying so is the same discipline that stops the
        resolver substituting cloud for local."""
        assert production_factories(local_model_size=None) == {}

    def test_a_chosen_model_size_registers_it(self) -> None:
        assert EngineChoice.LOCAL in production_factories(local_model_size=MODEL_SIZE)

    def test_an_unconfigured_build_still_refuses_by_name(self) -> None:
        """The operator needs to know their engine is missing, not merely that
        something went wrong."""
        with pytest.raises(EngineUnavailable) as refusal:
            EngineResolver(production_factories(local_model_size=None)).resolve(
                EngineChoice.LOCAL
            )

        assert "local" in str(refusal.value)


class TestReadingTheChoice:
    def test_it_comes_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The worker is spawned with an inherited environment, which is how the
        choice reaches a process that only receives a job id and a data dir on
        its argv."""
        monkeypatch.setenv(LOCAL_MODEL_SIZE_ENV, MODEL_SIZE)

        resolver = configured_resolver()

        assert resolver is not None

    def test_an_unset_variable_configures_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(LOCAL_MODEL_SIZE_ENV, raising=False)

        assert configured_resolver() is None

    def test_a_blank_variable_is_not_a_model_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An exported-but-empty variable is the shape a half-written `.env` or a
        shell typo takes. Passing "" through would reach the adapter, which would
        fail while loading a model named nothing."""
        monkeypatch.setenv(LOCAL_MODEL_SIZE_ENV, "   ")

        assert configured_resolver() is None

    def test_reading_the_choice_loads_no_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """This test runs in the default suite, on a checkout that may have no
        local-ASR extras at all. It passing is the assertion: registration stays
        cheap and construction waits for `resolve()`."""
        monkeypatch.setenv(LOCAL_MODEL_SIZE_ENV, "large-v3")

        assert configured_resolver() is not None


class TestTheWorkerEntrypoint:
    def test_it_falls_back_to_the_configured_resolver(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The whole point of the unit: with no resolver injected, `main` builds
        one rather than giving up."""
        built = EngineResolver({})
        received: list[EngineResolver] = []

        monkeypatch.setattr(worker, "configured_resolver", lambda: built)
        monkeypatch.setattr(worker, "run_job", _spy(received))

        worker.main(_argv(tmp_path))

        assert received == [built]

    def test_an_injected_resolver_still_wins(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Tests and the E2E harness drive this entrypoint with a fake engine.
        Reading the environment when a resolver was handed over would make the
        machine's configuration leak into a run that supplied its own."""
        injected = EngineResolver({})
        received: list[EngineResolver] = []

        monkeypatch.setattr(
            worker,
            "configured_resolver",
            lambda: pytest.fail("the environment was read despite an injected engine"),
        )
        monkeypatch.setattr(worker, "run_job", _spy(received))

        worker.main(_argv(tmp_path), resolver=injected)

        assert received == [injected]

    def test_an_unconfigured_build_exits_unusable_naming_the_variable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Exit 3 is still right when nothing is configured — but the operator now
        gets the name of the thing to set, not a statement about "this build"."""
        monkeypatch.delenv(LOCAL_MODEL_SIZE_ENV, raising=False)

        assert worker.main(_argv(tmp_path)) == EXIT_UNUSABLE
        assert LOCAL_MODEL_SIZE_ENV in capsys.readouterr().err

    def test_it_refuses_before_touching_the_job(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A worker with no engine must not claim the record, write a pid, or
        move the job out of QUEUED — the drain would count a slot as busy for a
        process about to exit."""
        monkeypatch.delenv(LOCAL_MODEL_SIZE_ENV, raising=False)
        monkeypatch.setattr(
            worker,
            "run_job",
            lambda *a, **k: pytest.fail("the job was started without an engine"),
        )

        worker.main(_argv(tmp_path))


def _spy(received: list[EngineResolver]) -> Callable[..., JobRecord]:
    """Stands in for `run_job`, recording the engine it was handed.

    The resolver is the whole subject here, and constructing one for real would
    load CTranslate2 weights inside the default suite — the run that exists
    specifically to need none of them.
    """

    def run_job(
        job_id: JobId,
        data_dir: Path,
        *,
        resolver: EngineResolver,
        extractor_factory: object = None,
        # Accepted and ignored: this spy exists to capture the resolver, and a
        # signature that refused the entrypoint's other arguments would break
        # every time one is added rather than when this test's subject changes.
        chunk_timeout_s: float = 0.0,
    ) -> JobRecord:
        received.append(resolver)
        return _completed()

    return run_job


def _completed() -> JobRecord:
    return JobRecord(
        job_id=make_job_id(JOB_ID),
        media_id=make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE"),
        state=JobState.COMPLETED,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=2.0,
        worker_pid=1,
        error=None,
        owner=make_operator_id("maria"),
    )


class TestTheDiarizationLicenceTokenReachesTheFactory:
    """The token is configuration, so the composition root reads it.

    Same split as the device and the cloud key: the adapter takes a value and
    knows only the variable's *name*, for its own refusal. An adapter reading
    its own environment could not be pointed at a test value, and would make
    `capabilities()` depend on the machine rather than on what it was given.

    Asserted at this seam rather than through a constructed adapter, because
    constructing one loads CTranslate2 weights — inside the default suite, which
    exists specifically to need none of them.
    """

    def test_a_configured_token_is_passed_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(LOCAL_MODEL_SIZE_ENV, MODEL_SIZE)
        monkeypatch.setenv(HF_TOKEN_ENV, "hf_not-a-real-token")
        seen: dict[str, object] = {}
        monkeypatch.setattr(worker, "production_factories", _recording(seen))

        configured_resolver()

        assert seen["hf_token"] == "hf_not-a-real-token"

    def test_an_unset_token_arrives_as_absent_rather_than_blank(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`None` is what the probe reads as "no licence". An empty string would
        be a token as far as a naive check is concerned."""
        monkeypatch.setenv(LOCAL_MODEL_SIZE_ENV, MODEL_SIZE)
        monkeypatch.delenv(HF_TOKEN_ENV, raising=False)
        seen: dict[str, object] = {}
        monkeypatch.setattr(worker, "production_factories", _recording(seen))

        configured_resolver()

        assert seen["hf_token"] is None


def _recording(seen: dict[str, object]) -> Callable[..., dict[EngineChoice, object]]:
    def production_factories(**kwargs: object) -> dict[EngineChoice, object]:
        seen.update(kwargs)
        return {EngineChoice.LOCAL: lambda: None}

    return production_factories
