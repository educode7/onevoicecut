"""Turns the engine recorded on a job into the adapter that will run it.

Engine choice has no global default. The operator picks per job because the
choice is content-dependent — a sermon dealing with something private goes to the
local engine, the rest may go to a provider — so this resolves a request and
never expresses a preference.

Which is why there is no fallback. Substituting the other engine when the
requested one is unavailable would, on precisely the job where the distinction
mattered, send a church's private material to a third party and report success.
An unavailable engine is an error.

Adapters are *constructed* here, not looked up. That is deliberate: a missing API
key surfaces at resolution, before the job starts, rather than on the first cloud
call three hours in — after the local work is done and the operator has walked
away. It also keeps secrets out of the use-case layer entirely; nothing below this
package ever sees one.
"""

from collections.abc import Callable, Mapping

from onevoicecut.domain.errors import EngineUnavailable
from onevoicecut.domain.jobs import EngineChoice
from onevoicecut.ports.transcription import TranscriptionPort

TranscriberFactory = Callable[[], TranscriptionPort]


class EngineResolver:
    def __init__(self, factories: Mapping[EngineChoice, TranscriberFactory]) -> None:
        self._factories = dict(factories)

    def resolve(self, engine: EngineChoice) -> TranscriptionPort:
        """A fresh adapter per call.

        One job, one instance: an adapter may scope per-job state to itself, as
        the ffmpeg extractor already does with its job id, and sharing one across
        jobs would share that too.
        """
        try:
            build = self._factories[engine]
        except KeyError as error:
            configured = ", ".join(sorted(self._factories)) or "none"
            raise EngineUnavailable(
                f"job requested the {engine} engine, which is not configured "
                f"(configured: {configured}). Engine choice is per job and is "
                f"never substituted."
            ) from error
        return build()


def local_transcriber(
    model_size: str, *, device: str = "auto", compute_type: str = "default"
) -> TranscriberFactory:
    """A factory that imports the engine when called, never at import time.

    The indirection is not style. `faster_whisper` is an optional extra —
    CTranslate2 and onnxruntime, some ninety megabytes of wheels before a single
    model weight is fetched — and this module is imported by the composition
    root, which is imported by most of the test suite. A module-level import
    here would make the default run, the one that exists specifically to need
    none of that, uninstallable without it.

    Everything expensive still happens at `resolve()`, which is exactly where it
    should: the model loads before the job starts rather than three hours in.
    """

    def build() -> TranscriptionPort:
        from onevoicecut.adapters.asr.local.faster_whisper_adapter import (
            FasterWhisperTranscriber,
        )

        return FasterWhisperTranscriber(
            model_size, device=device, compute_type=compute_type
        )

    return build


def cloud_transcriber(api_key: str, *, model: str | None = None) -> TranscriberFactory:
    """A factory that imports the adapter when called, like its local sibling.

    The reason differs, and saying so matters because the local one documents
    laziness as "not style". `httpx` is a core dependency, so there is no weight
    to defer here. What is deferred is the same thing: a composition root names
    the adapters this build has, it does not carry them — so replacing this
    provider with one that ships a heavy SDK cannot change what importing this
    module costs.

    The key is *not* validated here. Registration must stay cheap, and judging
    it belongs to the adapter's constructor, which `resolve()` calls — which is
    what puts a bad key before the job rather than on the first cloud call.
    """

    def build() -> TranscriptionPort:
        from onevoicecut.adapters.asr.cloud.openai_whisper_adapter import (
            DEFAULT_MODEL,
            OpenAiWhisperTranscriber,
        )

        return OpenAiWhisperTranscriber(api_key, model=model or DEFAULT_MODEL)

    return build


def production_factories(
    *,
    local_model_size: str | None,
    local_device: str = "auto",
    cloud_api_key: str | None = None,
) -> dict[EngineChoice, TranscriberFactory]:
    """What this build can actually run.

    One rule, applied per engine: **a missing required value registers no
    engine, rather than a broken one.** An unset model size does not become
    `tiny`; an unset API key does not become an adapter that discovers the
    problem on its first request. Both absences are visible at resolution, by
    name, before a three-hour job starts.

    `local_model_size` has no default because it decides both transcript quality
    and hours of runtime, and it is persisted on every chunk result as
    provenance — a default would make that choice invisible at the one place it
    is made. `cloud_api_key` may default to `None` because its absence is
    unambiguous: there is no quality dimension a forgotten key silently picks.

    An engine absent from this map is not silently downgraded to one that is
    present — `resolve` raises. A job that asked for the local engine because
    its material is private must never quietly reach a third party instead, and
    now that both engines can be configured on one machine that refusal is doing
    real work rather than describing a hypothetical.
    """
    factories: dict[EngineChoice, TranscriberFactory] = {}
    if local_model_size is not None:
        factories[EngineChoice.LOCAL] = local_transcriber(
            local_model_size, device=local_device
        )
    if cloud_api_key is not None:
        factories[EngineChoice.CLOUD] = cloud_transcriber(cloud_api_key)
    return factories
