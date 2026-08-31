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
