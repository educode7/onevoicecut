"""Which engine runs a job, decided from the job record and nowhere else.

There is no global default engine — that is a binding product decision, not a
configuration style. The operator picks per job because the choice is
content-dependent: a sermon dealing with something private goes to the local
engine, the rest may go to a provider.

Which makes the interesting test a negative one. A resolver that quietly fell back
to the other engine when the requested one was unavailable would, on exactly the
job where it mattered, ship a church's private material to a third party and
report success.
"""

import pytest

from transcribe.domain.errors import EngineUnavailable
from transcribe.domain.jobs import EngineChoice
from transcribe.runtime.engine_resolver import EngineResolver
from tests.fakes.transcription import (
    DiarizingFakeTranscriptionPort,
    FakeTranscriptionPort,
)


def test_the_requested_engine_is_the_one_returned() -> None:
    resolver = EngineResolver(
        {
            EngineChoice.LOCAL: FakeTranscriptionPort,
            EngineChoice.CLOUD: DiarizingFakeTranscriptionPort,
        }
    )

    assert resolver.resolve(EngineChoice.LOCAL).capabilities().engine_id == "fake-asr"
    assert (
        resolver.resolve(EngineChoice.CLOUD).capabilities().engine_id
        == "diarizing-fake-asr"
    )


def test_an_unconfigured_engine_is_refused_rather_than_substituted() -> None:
    """The privacy-critical case. Falling back from local to cloud would send
    material chosen for the local engine to a provider, and look like success."""
    resolver = EngineResolver({EngineChoice.CLOUD: DiarizingFakeTranscriptionPort})

    with pytest.raises(EngineUnavailable):
        resolver.resolve(EngineChoice.LOCAL)


def test_the_error_names_the_engine_that_is_missing() -> None:
    resolver = EngineResolver({})

    with pytest.raises(EngineUnavailable, match="local"):
        resolver.resolve(EngineChoice.LOCAL)


def test_a_broken_adapter_fails_at_resolution_not_mid_run() -> None:
    """Adapters are constructed here, which is where a missing API key surfaces.

    The alternative is discovering it on the first cloud call — three hours into a
    job, after the local work is done and the operator has walked away.
    """

    def needs_a_key_that_is_not_set() -> FakeTranscriptionPort:
        raise EngineUnavailable("TRANSCRIBE_CLOUD_API_KEY is not set")

    resolver = EngineResolver({EngineChoice.CLOUD: needs_a_key_that_is_not_set})

    with pytest.raises(EngineUnavailable):
        resolver.resolve(EngineChoice.CLOUD)


def test_each_resolution_builds_a_fresh_adapter() -> None:
    """One job, one adapter instance. Sharing one across jobs would share whatever
    per-job state an adapter keeps — the ffmpeg extractor already scopes itself to
    a job id for exactly this reason."""
    resolver = EngineResolver({EngineChoice.LOCAL: FakeTranscriptionPort})

    assert resolver.resolve(EngineChoice.LOCAL) is not resolver.resolve(
        EngineChoice.LOCAL
    )
