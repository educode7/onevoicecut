"""A composition root must not resurrect a variable a test blanked.

`worker.main` reads the operator's `.env` inside the branch that consults the
environment, so the root's own load runs *after* a test has decided the build
is unconfigured. The file is a floor, not a mask (`override=False`), and a key
holding `""` counts as present, so blanking — rather than deleting — is the one
expression of "not configured" that survives that load: a deleted key is
absent, and the file puts the value straight back.

`test_env_file_loading` proves the precedence against a probe name through the
helper alone. This file proves it through the root, against a `.env` the test
itself wrote, so the refusal asserted below reads the same on every machine
whatever the developer's real `.env` happens to configure — a test that
inherited the machine's file could only fail on the machines that already
agree with it.
"""

import os
from pathlib import Path

import pytest

from onevoicecut.runtime import worker
from onevoicecut.runtime.worker import (
    CLOUD_API_KEY_ENV,
    EXIT_UNUSABLE,
    LOCAL_MODEL_SIZE_ENV,
)

JOB_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFD"


def test_the_worker_refuses_a_build_whose_blank_the_env_file_cannot_refill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The refusal, driven from a `.env` this test owns.

    Crafted rather than inherited: the file says `small`, the environment says
    `""`, and the root must keep both answers. The file refilling the blank
    would make the build configured inside the very call the refusal is
    asserted on — the failure this whole file exists to prevent.
    """
    (tmp_path / ".env").write_text(f"{LOCAL_MODEL_SIZE_ENV}=small\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(LOCAL_MODEL_SIZE_ENV, "")
    monkeypatch.setenv(CLOUD_API_KEY_ENV, "")

    exit_code = worker.main(["--job-id", JOB_ID, "--data-dir", str(tmp_path)])

    assert exit_code == EXIT_UNUSABLE
    assert os.environ[LOCAL_MODEL_SIZE_ENV] == ""
    assert LOCAL_MODEL_SIZE_ENV in capsys.readouterr().err
