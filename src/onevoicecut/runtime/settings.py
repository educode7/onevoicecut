"""Configuration, read once at the composition root and nowhere else.

Nothing below `runtime/` reads the environment. A use case that consulted a
setting could not be driven by a test without one, and an adapter that read its
own configuration could not be pointed at a `tmp_path` — which is why storage
takes a `data_dir` and the ffmpeg adapter takes a `job_dir` rather than looking
either up.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from onevoicecut.adapters.web.app import DEFAULT_MAX_UPLOAD_BYTES


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
