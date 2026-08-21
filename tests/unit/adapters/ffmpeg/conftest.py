"""Unit tests of the ffmpeg adapter must not depend on the machine's PATH.

The availability check is deliberately *not* injected — it reads the real PATH,
because that is the thing it exists to check. Without this fixture every
runner-injected unit test would also assert something about whether ffmpeg
happens to be installed, which is the coupling the whole adapter design avoids.

`test_availability.py` overrides this to drive both answers explicitly.
"""

import shutil

import pytest


@pytest.fixture(autouse=True)
def assume_binaries_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
