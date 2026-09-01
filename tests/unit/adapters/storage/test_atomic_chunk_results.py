"""Resume is built on this file, so the test is about the crash, not the round trip.

A chunk result is committed while the job is still running. The process holding it
can die at any instruction, and the next process must be able to tell a committed
result from a half-written one by looking at the directory alone — there is no
recovery pass and no journal. That is only true if the commit is a rename.

The crash is simulated rather than real: a `.tmp` left behind with no final file is
exactly what a process killed between `write` and `os.replace` leaves on disk, and
it is the state the loader must survive.
"""

import os
from pathlib import Path
from typing import Any

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.chunking import ChunkResult, ChunkState
from onevoicecut.domain.errors import JobNotFound
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
OTHER_JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFF")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")


def a_job(job_id: JobId) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        media_id=MEDIA_ID,
        state=JobState.TRANSCRIBING,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=1.0,
        worker_pid=None,
        error=None,
        owner=None,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemTranscriptStorage:
    store = FilesystemTranscriptStorage(tmp_path)
    store.create_job(a_job(JOB_ID))
    return store


def a_result(
    index: int, job_id: JobId = JOB_ID, text: str = "hola a todos"
) -> ChunkResult:
    return ChunkResult(
        job_id=job_id,
        index=index,
        state=ChunkState.DONE,
        segments=(
            TranscriptSegment(
                start_s=float(index) * 600.0,
                end_s=float(index) * 600.0 + 4.5,
                text=text,
                speaker=None,
                confidence=0.9,
                kind=SegmentKind.SPEECH,
            ),
        ),
        engine_id="faster-whisper/large-v3",
        attempts=1,
        error=None,
        finished_at=1723501999.25,
    )


def results_dir(storage: FilesystemTranscriptStorage) -> Path:
    return storage.job_dir(JOB_ID) / "results"


def test_a_saved_chunk_result_loads_back_identical(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.save_chunk_result(a_result(0))

    assert storage.load_chunk_results(JOB_ID) == (a_result(0),)


def test_a_job_with_no_completed_chunks_has_no_results(
    storage: FilesystemTranscriptStorage,
) -> None:
    assert storage.load_chunk_results(JOB_ID) == ()


def test_a_chunk_result_is_retrievable_before_the_job_completes(
    storage: FilesystemTranscriptStorage,
) -> None:
    """Chunk 10 of 87 is readable the moment it lands. This is what makes
    chunk-level progress and resume real rather than in-memory."""
    storage.save_chunk_result(a_result(10))

    assert storage.load_job(JOB_ID).state is JobState.TRANSCRIBING
    assert [r.index for r in storage.load_chunk_results(JOB_ID)] == [10]


def test_results_come_back_in_chunk_order_however_they_were_written(
    storage: FilesystemTranscriptStorage,
) -> None:
    """Chunks may finish out of order after a retry; the transcript may not be
    assembled out of order."""
    for index in (11, 2, 7):
        storage.save_chunk_result(a_result(index))

    assert [r.index for r in storage.load_chunk_results(JOB_ID)] == [2, 7, 11]


def test_a_chunk_result_is_named_by_its_zero_padded_index(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.save_chunk_result(a_result(7))

    assert (results_dir(storage) / "0007.json").is_file()


def test_no_temporary_file_survives_a_completed_save(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.save_chunk_result(a_result(0))

    assert list(results_dir(storage).glob("*.tmp")) == []


def test_a_temporary_file_left_by_a_crash_is_ignored(
    storage: FilesystemTranscriptStorage,
) -> None:
    """The exact residue of a process killed between the write and the rename."""
    storage.save_chunk_result(a_result(0))
    (results_dir(storage) / "0001.json.tmp").write_text(
        "half-written garbage", encoding="utf-8"
    )

    assert storage.load_chunk_results(JOB_ID) == (a_result(0),)


def test_a_crash_before_the_rename_loses_only_the_chunk_in_flight(
    storage: FilesystemTranscriptStorage,
) -> None:
    """Committed chunks stay committed. That is the whole value of the rename:
    a three-hour job resumes from chunk 3 instead of chunk 0."""
    for index in (0, 1, 2):
        storage.save_chunk_result(a_result(index))
    (results_dir(storage) / "0003.json.tmp").write_text("torn", encoding="utf-8")

    assert [r.index for r in storage.load_chunk_results(JOB_ID)] == [0, 1, 2]


def test_a_retry_overwrites_the_previous_result_for_that_chunk(
    storage: FilesystemTranscriptStorage,
) -> None:
    """`os.replace`, not `os.rename`: on Windows a rename onto an existing
    destination fails, and an existing destination is precisely the retry case."""
    storage.save_chunk_result(a_result(4, text="first attempt"))

    storage.save_chunk_result(a_result(4, text="second attempt"))

    results = storage.load_chunk_results(JOB_ID)
    assert len(results) == 1
    assert results[0].segments[0].text == "second attempt"


def test_a_retry_over_a_stale_temporary_file_succeeds(
    storage: FilesystemTranscriptStorage,
) -> None:
    results_dir(storage).mkdir(parents=True, exist_ok=True)
    (results_dir(storage) / "0004.json.tmp").write_text("torn", encoding="utf-8")

    storage.save_chunk_result(a_result(4))

    assert storage.load_chunk_results(JOB_ID) == (a_result(4),)
    assert list(results_dir(storage).glob("*.tmp")) == []


def test_the_bytes_are_on_disk_before_the_rename_commits_them(
    storage: FilesystemTranscriptStorage, monkeypatch: pytest.MonkeyPatch
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

    storage.save_chunk_result(a_result(0))

    assert events == ["fsync", "replace"]


def test_one_jobs_results_are_not_visible_from_another(
    storage: FilesystemTranscriptStorage,
) -> None:
    storage.create_job(a_job(OTHER_JOB_ID))

    storage.save_chunk_result(a_result(0, JOB_ID))

    assert len(storage.load_chunk_results(JOB_ID)) == 1
    assert storage.load_chunk_results(OTHER_JOB_ID) == ()


def test_saving_a_result_for_an_uncreated_job_is_refused(
    storage: FilesystemTranscriptStorage,
) -> None:
    with pytest.raises(JobNotFound):
        storage.save_chunk_result(a_result(0, OTHER_JOB_ID))


def test_the_job_record_is_committed_the_same_way(
    storage: FilesystemTranscriptStorage,
) -> None:
    """A torn `job.json` is no more survivable than a torn chunk result, and the
    worker rewrites it at every state transition."""
    storage.update_job(a_job(JOB_ID))

    assert list(storage.job_dir(JOB_ID).glob("*.tmp")) == []
    assert storage.load_job(JOB_ID) == a_job(JOB_ID)
