"""A chunk the engine refuses for its size is split, not surrendered.

The planner already sizes chunks against the declared cap, so this is the
recovery path for when that arithmetic is beaten by reality: a plan derived from
an *average* bitrate meets one chunk that encoded above it. On a three-hour
sermon that is one chunk in eighty-seven, and failing the job over it throws away
the other eighty-six.

Three properties make this harder than "retry smaller", and each has a test here
because getting any of them wrong is silent:

**The stored plan must not change.** `_plan` refuses to re-plan an existing job
for exactly this reason — chunk results are indexed against the persisted plan,
and a plan that differed by one chunk would re-map every completed result onto
the wrong range. So a split happens *inside* one planned chunk: the halves are
transcribed separately and their segments come back as one `ChunkResult` at the
original index. Resume never learns a split happened.

**Times must stay local to the original chunk.** The port promises chunk-local
times and the stitcher turns them into track-relative ones. A second half whose
segments restart at zero would stitch cleanly and aim every clip cut from it at
the wrong minute of the sermon.

**It must terminate.** Halving something the engine will never accept is an
infinite loop, and the job it hangs is already measured in hours.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.chunking import AudioChunk, ChunkState, PlannedChunk
from onevoicecut.domain.errors import ChunkTooLarge
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.media import AudioTrack, MediaProbe, SourceMedia
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DiarizationSupport,
    TranscriptionCapabilities,
)
from onevoicecut.ports.transcription import TranscriptionRequest
from onevoicecut.usecases.transcribe_job import transcribe_job
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")

TRACK_S = 1200.0
BYTES_PER_S = 1_000
# 600 s target over a 1200 s track: chunk 0 is [0, 605] because it carries the
# overlap tail, chunk 1 is [600, 1200]. So the largest chunk is 605_000 B, not
# the 600_000 the stride alone suggests — the same off-by-an-overlap that slice
# 8a-iii found in the byte cap itself.
WHOLE_CHUNK_BYTES = 605_000
# Accepts either chunk halved (302_500 and 300_000), refuses either whole.
HALF_LIMIT = 310_000
# Accepts either chunk quartered (151_250 and 150_000), refuses either halved.
QUARTER_LIMIT = 155_000


class SizedExtractor:
    """Like the real one in the way that matters here: bytes track duration.

    The shared fake returns the whole track's size for every slice, which is
    fine everywhere else and useless here — halving a chunk would not reduce
    anything, so no split could ever succeed and every test would pass or fail
    for the wrong reason.
    """

    def __init__(self, job_id: JobId, *, bytes_per_second: float = BYTES_PER_S) -> None:
        self._job_id = job_id
        self._bps = bytes_per_second
        self.sliced: list[tuple[float, float, Path]] = []

    def probe(self, media: SourceMedia) -> MediaProbe:  # pragma: no cover - unused
        raise NotImplementedError

    def extract(self, media: SourceMedia, dest: Path) -> AudioTrack:
        return AudioTrack(
            media_id=media.media_id,
            path=dest,
            duration_s=TRACK_S,
            size_bytes=int(TRACK_S * self._bps),
        )

    def slice(self, track: AudioTrack, planned: PlannedChunk, dest: Path) -> AudioChunk:
        self.sliced.append((planned.start_s, planned.end_s, dest))
        return AudioChunk(
            job_id=self._job_id,
            index=planned.index,
            path=dest,
            start_s=planned.start_s,
            end_s=planned.end_s,
            size_bytes=int((planned.end_s - planned.start_s) * self._bps),
        )


class CappedTranscriber:
    """Refuses anything over `limit_bytes`, exactly as the cloud adapter does.

    Declares no `max_chunk_bytes`, deliberately: if it declared one the planner
    would size around it and the recovery path would never be reached. This
    stands for the case the recovery path exists for — a chunk that came out
    larger than the plan's average-bitrate arithmetic predicted.
    """

    def __init__(self, limit_bytes: int) -> None:
        self._limit = limit_bytes
        self.seen: list[AudioChunk] = []

    def capabilities(self) -> TranscriptionCapabilities:
        return TranscriptionCapabilities(
            engine_id="capped-fake-asr",
            diarization=DiarizationSupport.UNSUPPORTED,
            non_speech_classification=ClassificationSupport.AVAILABLE,
            max_chunk_bytes=None,
            max_chunk_duration_s=None,
        )

    def transcribe(
        self, chunk: AudioChunk, request: TranscriptionRequest
    ) -> tuple[TranscriptSegment, ...]:
        self.seen.append(chunk)
        if chunk.size_bytes > self._limit:
            raise ChunkTooLarge(
                f"chunk {chunk.index} is {chunk.size_bytes} bytes, over the "
                f"{self._limit} cap"
            )
        # One segment spanning the whole chunk, chunk-local. Spanning it is what
        # makes an un-offset second half visible: its end would land inside the
        # first half's range instead of after it.
        return (
            TranscriptSegment(
                start_s=0.0,
                end_s=chunk.end_s - chunk.start_s,
                text=f"{chunk.start_s:.0f}-{chunk.end_s:.0f}",
                speaker=None,
                confidence=0.9,
                kind=SegmentKind.SPEECH,
            ),
        )


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    store = FakeTranscriptStoragePort(tmp_path)
    store.create_job(
        JobRecord(
            job_id=JOB_ID,
            media_id=MEDIA_ID,
            state=JobState.QUEUED,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.CLOUD,
            created_at=1.0,
            updated_at=1.0,
            worker_pid=None,
            error=None,
            owner=OWNER,
        )
    )
    return store


def _media() -> SourceMedia:
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicacion.mp4",
        stored_path=Path("source"),
        size_bytes=4096,
        container="mp4",
        checksum="deadbeef",
    )


def _run(
    storage: FakeTranscriptStoragePort,
    limit_bytes: int,
    *,
    extractor: SizedExtractor | None = None,
    transcriber: CappedTranscriber | None = None,
) -> JobRecord:
    return transcribe_job(
        JOB_ID,
        _media(),
        extractor=extractor or SizedExtractor(JOB_ID),
        transcriber=transcriber or CappedTranscriber(limit_bytes),
        storage=storage,
        now=lambda: 100.0,
    )


class TestTheJobSurvives:
    def test_an_oversized_chunk_no_longer_kills_the_job(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """Before this unit `ChunkTooLarge` was caught by nothing: not a
        `TranscriptionFailed` subclass, so it passed straight through the retry
        handler and out of `transcribe_job`, taking every completed chunk with
        it."""
        job = _run(storage, HALF_LIMIT)

        assert job.state is JobState.COMPLETED

    def test_every_chunk_result_is_recorded_as_done(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        _run(storage, HALF_LIMIT)

        results = storage.load_chunk_results(JOB_ID)
        assert [r.state for r in results] == [ChunkState.DONE, ChunkState.DONE]

    def test_a_chunk_that_already_fits_is_never_split(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """The recovery path must cost nothing on the normal case, which is
        every chunk of every job that is planned correctly."""
        transcriber = CappedTranscriber(WHOLE_CHUNK_BYTES * 10)

        _run(storage, 0, transcriber=transcriber)

        assert len(transcriber.seen) == 2


class TestThePlanIsUntouched:
    def test_the_stored_plan_still_has_its_original_chunks(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """The invariant `_plan` already protects: results are indexed against
        the persisted plan, so a plan that grew by a chunk would re-map every
        completed result onto the wrong range on the next resume."""
        _run(storage, QUARTER_LIMIT)

        plan = storage.load_chunk_plan(JOB_ID)
        assert plan is not None
        assert len(plan.chunks) == 2

    def test_results_keep_the_original_chunk_indices(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """A split produces one result, not several. Four sub-slices arriving as
        four results would give the job more chunks than its plan has, and
        `pending_chunks` compares the two by index."""
        _run(storage, QUARTER_LIMIT)

        assert [r.index for r in storage.load_chunk_results(JOB_ID)] == [0, 1]


class TestTimesStayLocalToTheOriginalChunk:
    def test_the_second_half_is_offset_rather_than_restarted(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """The failure this whole file is shaped around, and the silent one.

        Each sub-slice answers in times local to *itself*. Concatenating them
        unchanged puts the second half's segments back at zero, on top of the
        first half's — a transcript that stitches cleanly and aims every clip
        cut from its back half at the wrong minute of the sermon.
        """
        _run(storage, HALF_LIMIT)

        segments = storage.load_chunk_results(JOB_ID)[0].segments
        assert [s.start_s for s in segments] == sorted(s.start_s for s in segments)
        assert segments[-1].start_s > 0.0

    def test_no_segment_escapes_the_original_chunks_duration(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """The port's central promise, held across the recovery path too."""
        _run(storage, QUARTER_LIMIT)

        plan = storage.load_chunk_plan(JOB_ID)
        assert plan is not None
        by_index = {c.index: c.end_s - c.start_s for c in plan.chunks}

        for result in storage.load_chunk_results(JOB_ID):
            for segment in result.segments:
                assert 0.0 <= segment.start_s <= segment.end_s <= by_index[result.index]

    def test_the_halves_cover_the_chunk_without_overlapping(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """No overlap is added at a split seam, and that is a decision.

        The stitcher dedupes by the *plan's* overlap, and these sub-slices are
        not in the plan — so an overlap here would duplicate text with nothing
        left to remove it. A word clipped at one seam is the cheaper defect, and
        this is a recovery path that a correctly planned job never enters.
        """
        extractor = SizedExtractor(JOB_ID)

        _run(storage, HALF_LIMIT, extractor=extractor)

        whole_s = 605.0
        halves = sorted(
            (a, b) for a, b, _ in extractor.sliced if a < 600.0 and b - a < whole_s
        )
        assert halves[0][1] == halves[1][0]


class TestItHalvesOnlyAsFarAsItMust:
    def test_a_chunk_that_fits_at_half_is_split_once(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        extractor = SizedExtractor(JOB_ID)

        _run(storage, HALF_LIMIT, extractor=extractor)

        first_chunk_slices = [s for s in extractor.sliced if s[0] < 600.0]
        assert len(first_chunk_slices) == 3  # the whole, then its two halves

    def test_a_chunk_that_fits_only_at_a_quarter_splits_again(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """Recursive, not a single halving. One retry at half size would fail
        again and surrender a chunk the engine would have accepted."""
        transcriber = CappedTranscriber(QUARTER_LIMIT)

        _run(storage, 0, transcriber=transcriber)

        accepted = [c for c in transcriber.seen if c.size_bytes <= QUARTER_LIMIT]
        assert len(accepted) == 8  # four quarters per chunk, two chunks


class TestItTerminates:
    def test_a_chunk_no_split_can_rescue_fails_instead_of_looping(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """Halving forever is worse than failing: the job it hangs is already
        measured in hours, and nothing downstream would ever report why."""
        job = _run(storage, 1)

        assert job.state is JobState.FAILED

    def test_the_exhausted_chunk_is_recorded_rather_than_lost(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """Chunk-level failure isolation still applies. The operator needs to
        know which chunk and why, and resume needs the other results kept."""
        _run(storage, 1)

        results = storage.load_chunk_results(JOB_ID)
        assert all(r.state is ChunkState.FAILED for r in results)
        assert all(r.error for r in results)

    def test_the_failure_names_the_size_it_could_not_get_under(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """"Too large" without a number leaves nothing to act on. The operator's
        only lever is the source, and they need to know by how much."""
        _run(storage, 1)

        assert "bytes" in (storage.load_chunk_results(JOB_ID)[0].error or "")


class TestItDoesNotSpendTheOrdinaryRetryBudget:
    def test_an_oversized_chunk_is_not_retried_at_the_same_size(
        self, storage: FakeTranscriptStoragePort
    ) -> None:
        """`ChunkTooLarge` is deterministic. Re-sending identical bytes to an
        engine that just measured them is the retry loop's one guaranteed
        waste — and on a cloud engine it is a paid one."""
        transcriber = CappedTranscriber(HALF_LIMIT)

        _run(storage, 0, transcriber=transcriber)

        whole = [c for c in transcriber.seen if c.size_bytes > HALF_LIMIT]
        assert len(whole) == 2  # one refused attempt per planned chunk, not three


def test_sub_slices_are_written_to_distinct_paths(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Both halves reusing `chunk_path(index)` would have the second overwrite
    the first — and on a real extractor the second slice would then be reading a
    destination it is simultaneously writing."""
    extractor = SizedExtractor(JOB_ID)

    _run(storage, QUARTER_LIMIT, extractor=extractor)

    paths = [dest for _, _, dest in extractor.sliced]
    assert len(paths) == len(set(paths))


def test_every_sub_slice_stays_inside_the_jobs_chunk_directory(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Every path handed to the extractor is resolve-checked against the job
    directory before a spawn, so a derived name that escaped it would not be a
    subtle bug — it would be a refused slice mid-job."""
    extractor = SizedExtractor(JOB_ID)

    _run(storage, QUARTER_LIMIT, extractor=extractor)

    expected = storage.chunk_path(JOB_ID, 0).parent
    assert all(dest.parent == expected for _, _, dest in extractor.sliced)


def test_the_result_still_names_the_engine_that_produced_it(
    storage: FakeTranscriptStoragePort,
) -> None:
    """Provenance survives the recovery path: a split result was produced by the
    same engine, and a blank id would make a re-run unanswerable."""
    _run(storage, HALF_LIMIT)

    assert all(
        r.engine_id == "capped-fake-asr" for r in storage.load_chunk_results(JOB_ID)
    )


def test_a_split_chunk_is_not_re_run_on_resume(
    storage: FakeTranscriptStoragePort,
) -> None:
    """A split is invisible to resume by construction — it produced one DONE
    result at the planned index, which is the only thing `pending_chunks`
    reads."""
    _run(storage, HALF_LIMIT)
    transcriber = CappedTranscriber(WHOLE_CHUNK_BYTES)

    _run(storage, 0, transcriber=transcriber)

    assert transcriber.seen == []
