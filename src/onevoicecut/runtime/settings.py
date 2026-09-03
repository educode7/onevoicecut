"""Configuration, read once at the composition root and nowhere else.

Nothing below `runtime/` reads the environment. A use case that consulted a
setting could not be driven by a test without one, and an adapter that read its
own configuration could not be pointed at a `tmp_path` — which is why storage
takes a `data_dir` and the ffmpeg adapter takes a `job_dir` rather than looking
either up.
"""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from onevoicecut.adapters.web.app import DEFAULT_MAX_UPLOAD_BYTES


# Two processes enforce the per-chunk timeout — the web process's watchdog from
# outside, the worker's in-call budget from inside — and they are separate
# programs reading separate environments. The names live here so a spelling
# cannot drift between them into a setting that silently applies to one and not
# the other, which is the failure the alias below was already added to prevent.
# Documented name first: that is also the precedence `AliasChoices` gives it.
CHUNK_TIMEOUT_ENV_NAMES = (
    "ONEVOICECUT_CHUNK_TIMEOUT_SECONDS",
    "ONEVOICECUT_CHUNK_TIMEOUT_S",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ONEVOICECUT_", extra="ignore")

    # No default, deliberately. A default would put multi-hour sermons somewhere
    # the operator did not choose and might not find, and the first they would
    # know of it is a full disk.
    data_dir: Path

    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES

    # `name:token;name:token`. Defaults to empty rather than absent so the
    # token-map parser — not a bare pydantic validation error — is what refuses
    # an unconfigured boot, with a message naming the actual failure.
    operator_tokens: str = ""

    # One global integer — not per engine, not per operator. Default 1 because
    # local ASR saturates this machine by itself, so two concurrent jobs mostly
    # time-slice; anything higher is a measurement the operator has made and
    # this project has not. `ge=1` because 0 is not "unlimited", it is a queue
    # with no exit, and the server refuses to boot rather than strand every job.
    max_concurrent_jobs: int = Field(default=1, ge=1)

    # Thirty minutes per chunk, and an operator's to set — unlike the two-hour
    # liveness bound, which is a property of the rule rather than of the machine.
    # This one depends on the hardware, the model size and the chunk length.
    # `gt=0` because zero would kill every worker on its first sweep.
    #
    # This value reaches the *watchdog*. The worker reads the same variables for
    # itself, through `CHUNK_TIMEOUT_ENV_NAMES` above, because it is a separate
    # process — it cannot be handed a value the web process parsed.
    #
    # Aliased because the derived name would be `ONEVOICECUT_CHUNK_TIMEOUT_S`,
    # and design.md documents `..._SECONDS`. An operator setting the documented
    # variable and watching it do nothing is the worst of both.
    chunk_timeout_s: float = Field(
        default=1800.0,
        gt=0,
        validation_alias=AliasChoices(*CHUNK_TIMEOUT_ENV_NAMES),
    )
