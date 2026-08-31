"""The composition root: where configuration is read and the two processes meet.

Everything below this takes what it needs as an argument, which is the only reason
the rest of the system can be tested without an environment. So the things worth
testing here are the ones that exist nowhere else — what the worker is launched
with, and what happens to a job whose worker died.
"""

from pathlib import Path

import pytest

from transcribe.domain.ids import make_job_id
from transcribe.runtime.app import WORKER_MODULE, build_dependencies, spawn_worker
from transcribe.runtime.settings import Settings

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")



def test_settings_read_the_data_directory_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRANSCRIBE_DATA_DIR", str(tmp_path))

    assert Settings().data_dir == tmp_path  # type: ignore[call-arg]


def test_a_missing_data_directory_is_refused_rather_than_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A default would put multi-hour sermons somewhere the operator did not
    choose, and the first they would know of it is a full disk."""
    monkeypatch.delenv("TRANSCRIBE_DATA_DIR", raising=False)

    with pytest.raises(Exception):
        Settings()  # type: ignore[call-arg]


def test_the_app_is_wired_to_the_configured_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TRANSCRIBE_DATA_DIR", str(tmp_path))

    deps = build_dependencies(Settings())  # type: ignore[call-arg]

    assert deps.storage.job_dir(JOB_ID) == tmp_path / "jobs" / JOB_ID


def test_the_worker_is_launched_as_its_own_process(tmp_path: Path) -> None:
    """A process, not a task. It can be killed when a three-hour job goes wrong,
    and while it lives it is the sole writer of that job's record."""
    launched: list[list[str]] = []

    spawn_worker(tmp_path, launch=launched.append)(JOB_ID)

    argv = launched[0]
    assert argv[1:3] == ["-m", WORKER_MODULE]
    assert argv[argv.index("--job-id") + 1] == JOB_ID
    assert argv[argv.index("--data-dir") + 1] == str(tmp_path)


def test_the_worker_argv_is_a_list_never_a_command_line(tmp_path: Path) -> None:
    """Same rule as the ffmpeg adapter: nothing parses these tokens as a shell
    command, so nothing in them can become one."""
    launched: list[list[str]] = []

    spawn_worker(tmp_path, launch=launched.append)(JOB_ID)

    assert all(isinstance(token, str) for token in launched[0])
    assert launched[0][0].endswith(("python", "python.exe"))
