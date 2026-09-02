"""The heartbeat file: proof a worker is still working, not merely still running.

A pid alone is not liveness. Two things break it, and both are ordinary here
rather than exotic: a hung worker keeps its pid and stops doing anything, and a
recycled pid makes a dead worker's number belong to somebody else's process. A
job that reads as alive under either is a job nobody will ever reconcile — it
sits occupying a slot on the shared board forever.

So the worker writes a timestamp at every chunk boundary, and freshness is what
liveness actually asks about. Everything here fails closed: absent, torn, or
unreadable all read as *not fresh*, because the cost of wrongly believing a
worker is alive is an orphaned job, while the cost of wrongly believing it is
dead is a re-run that resumes from the chunks already on disk.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    HEARTBEAT,
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.ids import make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")

STALE_AFTER_S = 7200.0
WROTE_AT = 1_700_000_000.0


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemTranscriptStorage:
    store = FilesystemTranscriptStorage(tmp_path)
    store.create_job(
        JobRecord(
            job_id=JOB_ID,
            media_id=MEDIA_ID,
            state=JobState.TRANSCRIBING,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=1.0,
            updated_at=1.0,
            worker_pid=4812,
            error=None,
            owner=OWNER,
        )
    )
    return store


def heartbeat_path(storage: FilesystemTranscriptStorage) -> Path:
    return storage.job_dir(JOB_ID) / HEARTBEAT


class TestWriting:
    def test_it_lands_in_the_job_directory_under_the_layout_name(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """Storage owns the on-disk layout; nobody else may name this file."""
        storage.write_heartbeat(JOB_ID, at_s=WROTE_AT)

        assert heartbeat_path(storage).is_file()

    def test_the_content_is_the_single_timestamp_it_was_given(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        storage.write_heartbeat(JOB_ID, at_s=WROTE_AT)

        assert float(heartbeat_path(storage).read_text(encoding="utf-8")) == WROTE_AT

    def test_a_later_write_replaces_the_earlier_one(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """One value, overwritten. A log would grow without bound across a
        multi-hour job for a question that only ever asks about the newest entry."""
        storage.write_heartbeat(JOB_ID, at_s=WROTE_AT)
        storage.write_heartbeat(JOB_ID, at_s=WROTE_AT + 600.0)

        assert float(heartbeat_path(storage).read_text(encoding="utf-8")) == (
            WROTE_AT + 600.0
        )

    def test_the_write_leaves_no_partial_file_behind(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """Same atomic discipline as every other persisted write.

        A torn heartbeat read as fresh would be worse than no heartbeat: it would
        vouch for a worker using bytes that were never fully written.
        """
        storage.write_heartbeat(JOB_ID, at_s=WROTE_AT)

        assert list(storage.job_dir(JOB_ID).glob("*.tmp")) == []


class TestFreshness:
    def test_a_recent_heartbeat_is_fresh(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        storage.write_heartbeat(JOB_ID, at_s=WROTE_AT)

        assert storage.heartbeat_is_fresh(
            JOB_ID, now_s=WROTE_AT + 60.0, stale_after_s=STALE_AFTER_S
        )

    def test_exactly_at_the_bound_still_counts_as_fresh(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """The boundary is inclusive, so a worker that is precisely as slow as the
        bound allows is not killed off by a rounding difference."""
        storage.write_heartbeat(JOB_ID, at_s=WROTE_AT)

        assert storage.heartbeat_is_fresh(
            JOB_ID, now_s=WROTE_AT + STALE_AFTER_S, stale_after_s=STALE_AFTER_S
        )

    def test_one_second_past_the_bound_is_stale(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        storage.write_heartbeat(JOB_ID, at_s=WROTE_AT)

        assert not storage.heartbeat_is_fresh(
            JOB_ID, now_s=WROTE_AT + STALE_AFTER_S + 1.0, stale_after_s=STALE_AFTER_S
        )

    def test_an_absent_heartbeat_is_not_fresh(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """A job that never wrote one has nothing vouching for it. Reading absence
        as fresh would make every pre-heartbeat record immortal."""
        assert not storage.heartbeat_is_fresh(
            JOB_ID, now_s=WROTE_AT, stale_after_s=STALE_AFTER_S
        )

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param("", id="empty"),
            pytest.param("   ", id="whitespace"),
            pytest.param("not-a-number", id="text"),
            pytest.param("1700000000.0\n1700000600.0", id="two-values"),
            pytest.param("\x00\x00", id="nul-bytes"),
        ],
    )
    def test_unreadable_content_is_not_fresh(
        self, storage: FilesystemTranscriptStorage, content: str
    ) -> None:
        """Fail closed. The alternative — treating garbage as a timestamp, or
        raising — either keeps a dead job alive forever or turns one damaged file
        into a startup that cannot complete."""
        heartbeat_path(storage).parent.mkdir(parents=True, exist_ok=True)
        heartbeat_path(storage).write_text(content, encoding="utf-8")

        assert not storage.heartbeat_is_fresh(
            JOB_ID, now_s=WROTE_AT, stale_after_s=STALE_AFTER_S
        )

    def test_a_heartbeat_from_the_future_is_still_fresh(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """Clock skew should not orphan a job that is plainly working.

        `now - value` goes negative, which is under any positive bound. That is
        the forgiving direction, and it matches the rest of the rule: the pid
        check is what says the process exists at all.
        """
        storage.write_heartbeat(JOB_ID, at_s=WROTE_AT + 300.0)

        assert storage.heartbeat_is_fresh(
            JOB_ID, now_s=WROTE_AT, stale_after_s=STALE_AFTER_S
        )


class TestItDoesNotDisturbTheRestOfTheLayout:
    def test_the_heartbeat_file_is_never_listed_as_a_job(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """LEG-09: a file inside a job directory is not a job directory.

        This is what makes the new file harmless to a build that predates it —
        an older listing walks the same tree and simply never looks at it.
        """
        storage.write_heartbeat(JOB_ID, at_s=WROTE_AT)
        storage.request_cancellation(JOB_ID)

        assert [job.job_id for job in storage.list_jobs()] == [JOB_ID]

    def test_writing_it_does_not_touch_the_job_record(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        before = storage.load_job(JOB_ID)

        storage.write_heartbeat(JOB_ID, at_s=WROTE_AT)

        assert storage.load_job(JOB_ID) == before
