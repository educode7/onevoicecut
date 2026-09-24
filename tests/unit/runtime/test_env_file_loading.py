"""The operator's `.env` beside the app, and the precedence it must not break.

This is a shared server whose composition roots are launched by hand from the
app's directory. Requiring every operator to export `HUGGING_FACE_TOKEN` and
`CLOUD_ASR_API_KEY` per session means a secret typed into a shell history, or a
worker spawned without it — so a gitignored `.env` beside the app is read at
each root instead.

The precedence is the invariant: a real exported variable always wins
(`override=False`). The `.env` is a floor, not a mask — an operator who exports
a variable for one run must get that run, not the file's opinion of it. And a
missing file is a silent no-op, because an environment-only install is equally
supported.

The wiring tests assert the two roots that read configuration — the web factory
and the worker entrypoint — against a token that exists nowhere but in the
file, which is the only proof that the load happens before the reading rather
than somewhere after it.
"""

import os
from collections.abc import Callable
from pathlib import Path

import pytest

from onevoicecut.adapters.asr.local.declarations import HF_TOKEN_ENV
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.runtime import worker
from onevoicecut.runtime.app import get_app
from onevoicecut.runtime.engine_resolver import EngineResolver
from onevoicecut.runtime.settings import load_env_file
from onevoicecut.runtime.worker import LOCAL_MODEL_SIZE_ENV

JOB_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFD"
PROBE_NAME = "ONEVOICECUT_ENV_FILE_PROBE"


class TestTheHelper:
    def test_an_unset_name_is_populated_from_the_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        (tmp_path / ".env").write_text(f"{PROBE_NAME}=from-file\n", encoding="utf-8")
        monkeypatch.delenv(PROBE_NAME, raising=False)
        monkeypatch.chdir(tmp_path)

        load_env_file()

        assert os.environ[PROBE_NAME] == "from-file"

    def test_an_exported_variable_is_not_overridden(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The precedence the whole feature must not break: the file is a floor,
        not a mask. An operator who exports a variable for one run gets that
        run."""
        (tmp_path / ".env").write_text(f"{PROBE_NAME}=from-file\n", encoding="utf-8")
        monkeypatch.setenv(PROBE_NAME, "exported")
        monkeypatch.chdir(tmp_path)

        load_env_file()

        assert os.environ[PROBE_NAME] == "exported"

    def test_a_missing_file_is_a_silent_no_op(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """An environment-only install is equally supported; a root that refused
        to boot without a `.env` would invent a requirement nobody stated."""
        monkeypatch.delenv(PROBE_NAME, raising=False)
        monkeypatch.chdir(tmp_path)

        load_env_file()

        assert PROBE_NAME not in os.environ


class TestTheWebRootLoadsTheFile:
    def test_settings_read_a_dotenv_provided_configuration(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`Settings()` has no default for the data dir and refuses an empty
        token map, so this factory returning at all is the proof: both reached
        it from the file, loaded before `Settings` was constructed. The token
        assertion covers the second reader in this root, the capabilities lambda
        in `build_dependencies`."""
        (tmp_path / ".env").write_text(
            f"ONEVOICECUT_DATA_DIR={tmp_path.as_posix()}/data\n"
            "ONEVOICECUT_OPERATOR_TOKENS=maria:tok\n"
            f"{HF_TOKEN_ENV}=hf-from-file\n",
            encoding="utf-8",
        )
        monkeypatch.delenv("ONEVOICECUT_DATA_DIR", raising=False)
        monkeypatch.delenv("ONEVOICECUT_OPERATOR_TOKENS", raising=False)
        monkeypatch.delenv(HF_TOKEN_ENV, raising=False)
        monkeypatch.chdir(tmp_path)

        get_app()

        assert os.environ[HF_TOKEN_ENV] == "hf-from-file"


class TestTheWorkerRootLoadsTheFile:
    def test_a_dotenv_provided_token_reaches_the_engine_factory(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The worker is a composition root in its own right: spawned with an
        inherited environment, but also launchable by hand, and the hand-launched
        case is exactly the one with no exports in it."""
        (tmp_path / ".env").write_text(
            f"{LOCAL_MODEL_SIZE_ENV}=small\n"
            f"{HF_TOKEN_ENV}=hf-from-file\n",
            encoding="utf-8",
        )
        monkeypatch.delenv(LOCAL_MODEL_SIZE_ENV, raising=False)
        monkeypatch.delenv(HF_TOKEN_ENV, raising=False)
        monkeypatch.chdir(tmp_path)
        seen: dict[str, object] = {}
        monkeypatch.setattr(worker, "production_factories", _recording(seen))
        monkeypatch.setattr(worker, "run_job", _completed_job)

        exit_code = worker.main(
            ["--job-id", JOB_ID, "--data-dir", str(tmp_path)]
        )

        assert exit_code == worker.EXIT_OK
        assert seen["hf_token"] == "hf-from-file"

    def test_an_injected_resolver_still_bypasses_the_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The E2E harness drives the entrypoint with a fake engine, and the
        entrypoint's existing promise is that a supplied resolver runs without
        the machine's configuration leaking in — a `.env` is the machine's
        configuration."""
        (tmp_path / ".env").write_text(
            f"{LOCAL_MODEL_SIZE_ENV}=small\n", encoding="utf-8"
        )
        monkeypatch.delenv(LOCAL_MODEL_SIZE_ENV, raising=False)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            worker,
            "configured_resolver",
            lambda: pytest.fail("the environment was read despite an injected engine"),
        )
        monkeypatch.setattr(worker, "run_job", _completed_job)

        worker.main(
            ["--job-id", JOB_ID, "--data-dir", str(tmp_path)],
            resolver=EngineResolver({}),
        )


def _recording(seen: dict[str, object]) -> Callable[..., dict[EngineChoice, object]]:
    def production_factories(**kwargs: object) -> dict[EngineChoice, object]:
        seen.update(kwargs)
        return {EngineChoice.LOCAL: lambda: None}

    return production_factories


def _completed_job(
    job_id: JobId,
    data_dir: Path,
    *,
    resolver: EngineResolver,
    extractor_factory: object = None,
    # Accepted and ignored: this spy exists so the entrypoint can be driven past
    # resolver construction without touching storage or loading model weights,
    # and a signature that refused the entrypoint's other arguments would break
    # every time one is added rather than when this test's subject changes.
    chunk_timeout_s: float = 0.0,
    generation: object = None,
    generator_factory: object = None,
    model_probe: object = None,
) -> JobRecord:
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
