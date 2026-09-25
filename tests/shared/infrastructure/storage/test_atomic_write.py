"""Committing a write: durable bytes first, then the rename.

`fsync` before `os.replace` is the invariant. A rename is atomic only with
respect to what is already durable, so committing a name whose bytes are still
in the page cache commits the name and not the data behind it. Every record
write goes through this primitive, which is why it is pinned here against the
core itself rather than through one facade's domain round trip.
"""

import os
from pathlib import Path
from typing import Any

import pytest

from onevoicecut.shared.domain.ids import make_job_id
from onevoicecut.shared.infrastructure.storage.core import StorageCore

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")


@pytest.fixture
def core(tmp_path: Path) -> StorageCore:
    return StorageCore(tmp_path)


def test_the_bytes_are_on_disk_before_the_rename_commits_them(
    core: StorageCore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rename is only atomic with respect to what was already durable. Renaming
    a file whose contents are still in the page cache commits a name, not data."""
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def spy_fsync(fd: int) -> None:
        events.append("fsync")
        real_fsync(fd)

    def spy_replace(src: Any, dst: Any) -> None:
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "replace", spy_replace)

    core.write_atomic(core.job_dir(JOB_ID) / "results" / "0000.json", '{"index": 0}')

    assert events == ["fsync", "replace"]
