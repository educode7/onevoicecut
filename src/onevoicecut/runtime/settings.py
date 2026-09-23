"""Configuration, read once at the composition root and nowhere else.

Nothing below `runtime/` reads the environment. A use case that consulted a
setting could not be driven by a test without one, and an adapter that read its
own configuration could not be pointed at a `tmp_path` — which is why storage
takes a `data_dir` and the ffmpeg adapter takes a `job_dir` rather than looking
either up.
"""

from collections.abc import Mapping
from pathlib import Path

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from onevoicecut.adapters.web.app import DEFAULT_MAX_UPLOAD_BYTES
from onevoicecut.domain.errors import RenderProfileInvalid
from onevoicecut.domain.rendering import RenderProfile
from onevoicecut.usecases.generate_artifacts import (
    DEFAULT_SCRIPT_TARGETS,
    SCRIPT_TARGETS,
    ScriptTarget,
)
from onevoicecut.usecases.render_profiles import RENDER_PROFILES


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


def load_env_file() -> None:
    """Read a gitignored `.env` beside the app, without overriding exported values.

    Operators of this shared server launch the composition roots by hand from
    the app's directory, and the secrets they need — `HUGGING_FACE_TOKEN`,
    `CLOUD_ASR_API_KEY` — otherwise have to be exported per session: a ritual
    that lands credentials in shell history and, when skipped, spawns a worker
    that refuses hours later. A `.env` in the working directory is read instead,
    once, at each root, before anything consults the environment.

    `override=False` is the invariant: a real exported variable always wins, so
    the file is a floor and not a mask. An operator who exports a variable for
    one run gets that run, not the file's opinion of it.

    The path is pinned to the working directory because `load_dotenv`'s own
    discovery resolves relative to *this module's* location and walks upward to
    the drive root — it would miss the file the operator actually placed and
    could pick up an unrelated ancestor `.env` nobody intended to configure this
    server with. A missing file is a silent no-op: an environment-only install
    is equally supported.
    """
    load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)


def check_target_profiles(
    targets: Mapping[str, ScriptTarget], profiles: Mapping[str, RenderProfile]
) -> None:
    """Refuse a script target that names a render profile nobody defined.

    The two registries are edited independently — adding a network is a row in
    one, adding a destination shape is a row in the other — and the only thing
    joining them is a string. `profil="vertcal"` type-checks, imports, and
    transcribes three hours of audio before anybody finds out. So they are read
    against each other while the server boots, which is the last moment before a
    job can start.

    **This asserts membership, not renderability, and the distinction is the
    reason the function exists rather than a call to `resolve_render_profiles`.**
    That resolver also refuses a profile whose caption safe area nobody has
    measured, and every profile shipped today is in exactly that state — on
    purpose, because the fractions are a measurement against each destination's
    live interface rather than a value this project may invent. Calling it here
    would refuse to boot the server, and would refuse it for transcription,
    which renders nothing.

    The two failures differ in both directions that matter. A dangling name is a
    typo: fixed by editing a row, identical on every retry, unrecoverable
    downstream — refused here. An unmeasured safe area is a recorded and
    intended state that the render path already refuses **by name**, at the point
    where a frame is genuinely needed. Escalating it to a boot refusal would take
    the transcription pipeline down for a gap in a capability the operator may
    not be using yet, and would make measuring four destinations a precondition
    for starting the server at all.

    Every offending row is named, not the first: fixing one and rebooting to be
    told about the next is a boot loop an operator walks through by hand.
    """
    dangling = sorted(
        f"{target.name} -> {target.profile}"
        for target in targets.values()
        if target.profile not in profiles
    )
    if dangling:
        raise RenderProfileInvalid(
            f"script target(s) {dangling} name a render profile that is not "
            f"defined; available: {', '.join(sorted(profiles))}"
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

    # Comma-separated names, the same shape `operator_tokens` uses, because an
    # operator who has to write a JSON list into an environment variable is an
    # operator who gets it wrong once. Resolved to real targets by the use case,
    # which refuses an unknown name rather than substituting a default target.
    # The default is all four confirmed destinations, which is also the billed
    # cost: four `complete()` calls per clip candidate, not one.
    script_targets: str = DEFAULT_SCRIPT_TARGETS

    # One global integer — not per engine, not per operator. Default 1 because
    # local ASR saturates this machine by itself, so two concurrent jobs mostly
    # time-slice; anything higher is a measurement the operator has made and
    # this project has not. `ge=1` because 0 is not "unlimited", it is a queue
    # with no exit, and the server refuses to boot rather than strand every job.
    max_concurrent_jobs: int = Field(default=1, ge=1)

    # The render side of `max_concurrent_jobs`, and independent of it: a
    # render is minutes of ffmpeg work, not hours of ASR, so the two caps have
    # no reason to move together. Default 1 for the same reason -- a
    # measurement nobody has made yet is not a default this project invents.
    max_concurrent_renders: int = Field(default=1, ge=1)

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

    @model_validator(mode="after")
    def _targets_name_defined_profiles(self) -> "Settings":
        """The whole registry, not only the selection in `script_targets`.

        A destination sitting unselected in the configuration is one an operator
        will select eventually, and a typo in its row would surface then — after
        the transcription hours are already spent. Reading the module globals at
        call time rather than binding them as defaults is what lets a test prove
        the refusal without editing the shipped registries.
        """
        check_target_profiles(SCRIPT_TARGETS, RENDER_PROFILES)
        return self
