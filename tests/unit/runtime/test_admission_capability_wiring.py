"""The admission guard, connected to something.

Slice 6 built it and proved it thoroughly against fakes: a `MULTI` job reaching an
engine that cannot diarize is refused before an id is minted, before storage is
touched. Every one of those tests passed. None of them asked whether anything
supplies the callable in production, and `build_dependencies` never did — so
`WebDependencies.capabilities` defaulted to `None` and `admit_job` skipped the
guard entirely on the one path an operator actually uses.

What that costs is not an error message. The job is admitted, queued, and given a
worker; ffmpeg extracts the audio from a three-hour recording; the chunk plan is
written; TRANSCRIBING begins — and *then* the adapter's own
`_validate_compatibility` raises on the first chunk. The operator learns at the
end of the expensive part what was knowable before it started, and the extraction
is thrown away. Slice 6's whole point was to move that discovery to the front.

**The guard asks one question, so it now depends on one answer.** It read
`capabilities(engine).diarization` and used nothing else, while the callable's
type promised a whole `TranscriptionCapabilities` — which is why it could not be
wired: assembling that object in the web process means constructing an adapter,
and constructing the local one loads CTranslate2 weights inside an HTTP request.

Narrowing it to `DiarizationSupport` makes the honest answer cheap. Both engines
can state their diarization support without being built: the local one from
`diarization.py`, which imports nothing heavier than `ports.capabilities`, and the
cloud one from a constant. The web process needs neither set of extras installed
to refuse a job correctly.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.asr.local.declarations import HF_TOKEN_ENV
from onevoicecut.domain.jobs import EngineChoice
from onevoicecut.ports.capabilities import DiarizationSupport
from onevoicecut.runtime.app import build_dependencies
from onevoicecut.runtime.engine_resolver import declared_diarization
from onevoicecut.runtime.settings import Settings
from onevoicecut.runtime.worker import CLOUD_API_KEY_ENV, LOCAL_MODEL_SIZE_ENV

TOKEN_MAP = "maria:some-token"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in (LOCAL_MODEL_SIZE_ENV, CLOUD_API_KEY_ENV, HF_TOKEN_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ONEVOICECUT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ONEVOICECUT_OPERATOR_TOKENS", TOKEN_MAP)


def _settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


class TestTheCompositionRootSuppliesTheGuard:
    def test_the_capability_callable_is_not_none(self) -> None:
        """The defect, stated. `None` here is not a missing feature — it is a
        feature that was built, tested and left disconnected."""
        assert build_dependencies(_settings()).capabilities is not None

    def test_it_answers_for_every_engine(self) -> None:
        """A callable that raised on `CLOUD` would turn a refusal into a 500 on
        the route it was added to protect."""
        answer = build_dependencies(_settings()).capabilities
        assert answer is not None

        for engine in EngineChoice:
            assert answer(engine).diarization in set(DiarizationSupport)

    def test_answering_constructs_no_engine(self) -> None:
        """The reason this could not be wired before. Assembling a whole
        `TranscriptionCapabilities` means building an adapter, and building the
        local one loads CTranslate2 weights — inside an HTTP request, on a web
        process that may not have the extras installed at all.

        This test passing in the default suite *is* the assertion: no extras are
        installed in the run that gates every commit.
        """
        answer = build_dependencies(_settings()).capabilities
        assert answer is not None

        assert answer(EngineChoice.LOCAL).diarization is DiarizationSupport.REQUIRES_SETUP


class TestWhatEachEngineDeclares:
    def test_cloud_can_never_diarize(self) -> None:
        """`UNSUPPORTED` means never: that API returns no speaker labels and
        offers no way to ask for them. No configuration changes this."""
        assert (
            declared_diarization(EngineChoice.CLOUD, hf_token="anything")
            is DiarizationSupport.UNSUPPORTED
        )

    def test_local_without_the_package_needs_setup(self) -> None:
        assert (
            declared_diarization(EngineChoice.LOCAL, hf_token=None)
            is DiarizationSupport.REQUIRES_SETUP
        )

    def test_local_without_a_licence_token_needs_setup(self) -> None:
        """Even with the package present. The models are gated, so code without
        a credential is as unable to diarize as no code at all."""
        assert (
            declared_diarization(EngineChoice.LOCAL, hf_token=None)
            is DiarizationSupport.REQUIRES_SETUP
        )

    def test_it_uses_the_same_function_the_adapter_uses(self) -> None:
        """The anti-drift assertion, and the reason this is a consolidation
        rather than a second implementation.

        A composition root that computed the declaration its own way would be a
        second definition of "can this build diarize" — and the failure mode is
        admission accepting a job the adapter then refuses three hours later,
        which is the exact defect this unit exists to close.
        """
        from onevoicecut.adapters.asr.local import declarations

        expected = declarations.diarization_support(
            installed=declarations.is_installed(), token=None
        )

        assert declared_diarization(EngineChoice.LOCAL, hf_token=None) is expected


class TestTheGuardActuallyRefuses:
    def test_a_speaker_mode_job_is_refused_before_storage_is_touched(
        self, tmp_path: Path
    ) -> None:
        """End of the wire. Slice 6 proved the use case refuses; this proves the
        refusal is reachable from the composition root the server actually runs.
        """
        from onevoicecut.domain.errors import DiarizationUnsupported
        from onevoicecut.domain.ids import make_operator_id
        from onevoicecut.domain.jobs import SpeakerMode
        from onevoicecut.usecases.admit_job import admit_job
        from tests.fakes.transcript_storage import FakeTranscriptStoragePort

        storage = FakeTranscriptStoragePort(tmp_path)
        deps = build_dependencies(_settings())

        with pytest.raises(DiarizationUnsupported):
            admit_job(
                engine=EngineChoice.LOCAL,
                speaker_mode=SpeakerMode.MULTI,
                operator=make_operator_id("maria"),
                storage=storage,
                capabilities=deps.capabilities,
            )

        assert storage.calls == []
