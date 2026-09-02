"""What actually happens to a running job when the web process cancels it.

The seam itself is old — the loop has polled the control file at every chunk
boundary since slice 4b. What was never specified is the handoff now that a
cancel *route* exists: the web process writes the signal, and the worker writes
the outcome. Two processes, one record, and the rule that keeps them from
colliding is that only one of them ever writes it.

These are characterization tests. They pin behavior that already works, because
the way this breaks later is subtle — someone "helpfully" stamps CANCELLED in
the route to make the UI update faster, and the next crash-resume overwrites a
worker's chunk commit from another process.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.domain.ids import make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.media import SourceMedia
from onevoicecut.domain.transcript import TranscriptSegment
from onevoicecut.ports.transcription import TranscriptionRequest
from onevoicecut.usecases.cancel_job import cancel_job
from onevoicecut.usecases.transcribe_job import transcribe_job
from tests.fakes.audio_extractor import FAKE_DURATION_S, FakeAudioExtractorPort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.fakes.transcription import FakeTranscriptionPort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")
FIXED_NOW = 1723501234.5

# Four chunks out of the fake track, so "stops at the next boundary" has
# boundaries left to stop at and the assertion is not vacuous.
QUARTER_TRACK_S = FAKE_DURATION_S / 4


class CountingTranscriber:
    """Delegates, and remembers which chunks it was actually asked to do.

    Zero *saved results* is weaker evidence than zero *calls*: a loop that
    transcribed a chunk and then declined to persist it would satisfy the first
    and still have spent the money and the minutes.
    """

    def __init__(self) -> None:
        self._inner = FakeTranscriptionPort()
        self.transcribed: list[int] = []

    def capabilities(self) -> object:
        return self._inner.capabilities()

    def transcribe(
        self, chunk: AudioChunk, request: TranscriptionRequest
    ) -> tuple[TranscriptSegment, ...]:
        self.transcribed.append(chunk.index)
        return self._inner.transcribe(chunk, request)


def a_media() -> SourceMedia:
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicacion.mp4",
        stored_path=Path("source.mp4"),
        size_bytes=4096,
        container="mp4",
        checksum="deadbeef",
    )


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    store = FakeTranscriptStoragePort(tmp_path)
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
    store.calls.clear()
    return store


def run(
    storage: FakeTranscriptStoragePort, transcriber: CountingTranscriber
) -> JobRecord:
    return transcribe_job(
        JOB_ID,
        a_media(),
        extractor=FakeAudioExtractorPort(JOB_ID),
        transcriber=transcriber,  # type: ignore[arg-type]
        storage=storage,
        now=lambda: FIXED_NOW,
        target_chunk_s=QUARTER_TRACK_S,
    )


def cancel_after_chunk(storage: FakeTranscriptStoragePort, index: int) -> None:
    """Wire the web process's cancel to fire between two chunks.

    The realistic shape of CXL-04: the operator hits cancel while the worker is
    mid-run, not before it started.
    """

    def hook(saved_index: int) -> None:
        if saved_index == index:
            cancel_job(JOB_ID, operator=OWNER, storage=storage, now=lambda: FIXED_NOW)

    storage.on_chunk_saved = hook


class TestCancellationMidRun:
    """CXL-04: stops at the next boundary, and the worker records the outcome."""

    def test_the_uncancelled_run_has_boundaries_left_to_stop_at(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """Without this, every stop assertion below could pass vacuously.

        A single-chunk plan satisfies `transcribed == [0]` whether cancellation
        works or not. Pinning the uncancelled run to more than one chunk is what
        makes the comparison a real difference rather than a coincidence of the
        fixture — and it fails loudly if someone retunes the fake track.
        """
        transcriber = CountingTranscriber()

        run(storage, transcriber)

        assert len(transcriber.transcribed) > 1

    def test_the_loop_stops_after_the_chunk_it_was_already_doing(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        transcriber = CountingTranscriber()
        cancel_after_chunk(storage, 0)

        run(storage, transcriber)

        assert transcriber.transcribed == [0]

    def test_the_partial_work_is_kept_not_discarded(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """The chunk that finished stays committed.

        Cancelling is not undoing. The operator stopped a run; they did not ask
        for the ten minutes already transcribed to be thrown away, and a resume
        would otherwise redo work that was already paid for.
        """
        transcriber = CountingTranscriber()
        cancel_after_chunk(storage, 0)

        run(storage, transcriber)

        assert len(storage.load_chunk_results(JOB_ID)) == 1

    def test_the_terminal_state_is_written_by_the_worker(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """CXL-03 + CXL-04 together: web signals, worker records.

        The record write must appear *after* the chunk commit — that ordering is
        what proves it came from the loop rather than from the cancel call,
        which ran in between and wrote nothing.
        """
        transcriber = CountingTranscriber()
        cancel_after_chunk(storage, 0)

        job = run(storage, transcriber)

        assert job.state is JobState.CANCELLED
        assert storage.calls.index("save_chunk_result:0") < storage.calls.index(
            "update_job:cancelled"
        )
        assert storage.calls.count("update_job:cancelled") == 1

    def test_no_transcript_is_exported_for_a_cancelled_run(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """A transcript written over the chunks that never ran reads as a
        complete sermon with a silent hole in it."""
        transcriber = CountingTranscriber()
        cancel_after_chunk(storage, 0)

        run(storage, transcriber)

        assert storage.load_export_path(JOB_ID) is None


class TestCancellationBeforeTheFirstChunk:
    """CXL-05: the containment that makes the spawn-versus-cancel race safe."""

    def test_zero_chunks_are_transcribed(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """Nothing billable and nothing slow happens.

        This is what makes it acceptable for a cancelled-but-already-spawned job
        to run at all: the worker starts, reads the signal, and stops — so the
        gate never has to win that race.
        """
        transcriber = CountingTranscriber()
        cancel_job(JOB_ID, operator=OWNER, storage=storage, now=lambda: FIXED_NOW)

        run(storage, transcriber)

        assert transcriber.transcribed == []
        assert storage.load_chunk_results(JOB_ID) == ()

    def test_the_job_still_ends_cancelled_rather_than_running_forever(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        transcriber = CountingTranscriber()
        cancel_job(JOB_ID, operator=OWNER, storage=storage, now=lambda: FIXED_NOW)

        job = run(storage, transcriber)

        assert job.state is JobState.CANCELLED
        assert storage.load_job(JOB_ID).state is JobState.CANCELLED
