"""Where on-disk things go, as answered by the module that owns the layout.

These are the layout's claims rather than the persistence facade's: a path is
computed and never created, a chunk file sorts with its zero-padded index, and
a name that is not a job id is refused before any path exists. The extractor is
handed these answers, so the layout has to live in one module instead of at
every call site.
"""

from pathlib import Path

import pytest

from onevoicecut.shared.domain.errors import JobNotFound
from onevoicecut.shared.domain.ids import JobId, make_job_id
from onevoicecut.shared.infrastructure.storage.core import StorageCore

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")


@pytest.fixture
def storage(tmp_path: Path) -> StorageCore:
    core = StorageCore(tmp_path)
    core.job_dir(JOB_ID).mkdir(parents=True)
    return core


def test_the_working_paths_stay_inside_the_job_directory(
    storage: StorageCore,
) -> None:
    """The extractor is handed these and writes to them. Storage answers where
    things go so the layout lives in one module instead of at every call site."""
    assert storage.audio_path(JOB_ID).parent == storage.job_dir(JOB_ID)
    assert storage.chunk_path(JOB_ID, 7).parent == storage.job_dir(JOB_ID) / "chunks"


def test_a_chunk_file_is_named_by_its_zero_padded_index(
    storage: StorageCore,
) -> None:
    assert storage.chunk_path(JOB_ID, 7).name == "0007.flac"


def test_asking_where_a_file_goes_creates_nothing(
    storage: StorageCore,
) -> None:
    """A path is an answer, not a side effect. The extractor owns making the file,
    and a job that was planned but never ran must not leave an empty chunks/."""
    before = sorted(path.name for path in storage.job_dir(JOB_ID).iterdir())

    storage.audio_path(JOB_ID)
    storage.chunk_path(JOB_ID, 0)

    assert sorted(p.name for p in storage.job_dir(JOB_ID).iterdir()) == before


def test_a_hostile_job_id_is_refused_before_a_path_is_built(
    storage: StorageCore,
) -> None:
    with pytest.raises(JobNotFound):
        storage.chunk_path(JobId("../../etc"), 0)
