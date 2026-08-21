"""Shared fixtures.

`integration` tests are in the default run by design — they are free and fast —
so they must skip cleanly rather than fail on a machine without ffmpeg. A red
suite for a missing optional binary trains people to ignore red suites.
"""

import shutil

import pytest


@pytest.fixture
def ffmpeg_available() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        pytest.skip(
            f"{'/'.join(missing)} not on PATH — install ffmpeg to run this test"
        )
