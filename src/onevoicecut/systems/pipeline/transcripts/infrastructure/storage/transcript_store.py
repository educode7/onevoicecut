"""`TranscriptStore` over the shared filesystem core: the transcripts half of
the persistence boundary (OQ3), with a real filesystem behind it.

The interface lives in `transcripts/domain/interfaces/transcript_store.py` and is
imported by nobody here — satisfaction is structural, so this class binds to it
without the domain ever learning that a filesystem exists. What this module does
import is the transcripts domain and the shared `core`, and nothing else: the
layout, the atomic rename and the JSON field reads all live in `core`, while the
transcripts records' own codec lives beside the methods that write it, because a
codec taking `ChunkResult` can live neither in the domain-agnostic core (AB-08:
no `systems.*` vocabulary crosses into `shared`) nor in `adapters/storage`, which
this facade is the start of replacing.

The codec functions are therefore defined here and re-exported from
`adapters.storage.serialization` (`X as X`, the same seam slice 2b-ii used) so the
monolith keeps resolving them until slice 4f retires it — a facade reaching back
into the adapter it exists to replace would break the day that adapter went.

`core` is injected rather than built from `data_dir`: a composition root
constructs it once and hands the same object to every facade, so one process has
exactly one owner of where things go. `runtime/` keeps constructing the monolith,
which satisfies `TranscriptStore` structurally, until slice 4f.
"""

from dataclasses import asdict
from pathlib import Path

from onevoicecut.shared.domain.ids import JobId, make_job_id
from onevoicecut.shared.infrastructure.storage.core import (
    CHUNK_PLAN,
    RESULTS_DIRNAME,
    TRANSCRIPT,
    TRANSCRIPT_TEXT,
    Record,
    StorageCore,
    _dumps,
    _flag,
    _id_field,
    _loads,
    _member,
    _number,
    _objects,
    _optional_number,
    _optional_text,
    _text,
    _whole,
)
from onevoicecut.systems.pipeline.transcripts.domain.chunking import (
    ChunkPlan,
    ChunkResult,
    ChunkState,
    PlannedChunk,
)
from onevoicecut.systems.pipeline.transcripts.domain.transcript import (
    SegmentKind,
    Transcript,
    TranscriptSegment,
    WordTiming,
)


class FilesystemTranscriptStore:
    def __init__(self, core: StorageCore) -> None:
        self._core = core

    def job_dir(self, job_id: JobId) -> Path:
        """The directory that holds everything belonging to one job.

        Public because the job directory is not private to persistence: the ffmpeg
        adapter is constructed against it, and it is `core` that decides where it
        is. Not part of the `TranscriptStore` table — `jobs` owns admission, which
        is what creates the directory — but no transcript can be located without
        it, so it is answered from the same `core` rather than recomposed here.
        """
        return self._core.job_dir(job_id)

    def audio_path(self, job_id: JobId) -> Path:
        """Where the extractor writes the normalized track.

        Storage answers this rather than the caller composing it, so the layout
        stays in one module. Like `job_dir`, it computes a path and creates
        nothing — the extractor owns making the file.
        """
        return self._core.audio_path(job_id)

    def chunk_path(self, job_id: JobId, index: int) -> Path:
        """Zero-padded so the directory sorts the way the chunks are numbered."""
        return self._core.chunk_path(job_id, index)

    def save_chunk_plan(self, job_id: JobId, plan: ChunkPlan) -> None:
        self._core.write_atomic(self._core.writable(job_id) / CHUNK_PLAN, encode_chunk_plan(plan))

    def load_chunk_plan(self, job_id: JobId) -> ChunkPlan | None:
        payload = self._core.read_optional(self.job_dir(job_id) / CHUNK_PLAN)
        return None if payload is None else decode_chunk_plan(payload)

    def save_chunk_result(self, result: ChunkResult) -> None:
        """Committed by rename, because this is what resume reads.

        A chunk lands while the job is still running and the process holding it can
        die at any instruction. The next process distinguishes a committed result
        from a half-written one by the directory alone — there is no journal and no
        recovery pass — which is only true if the last step is atomic.
        """
        directory = self._core.writable(result.job_id) / RESULTS_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        self._core.write_atomic(directory / f"{result.index:04d}.json", encode_chunk_result(result))

    def load_chunk_results(self, job_id: JobId) -> tuple[ChunkResult, ...]:
        """Sorted by chunk index: a retry can commit chunk 7 after chunk 11, but the
        transcript may not be assembled in that order. A stale `.tmp` is skipped by
        the glob rather than by a check, so there is no path that forgets to."""
        directory = self.job_dir(job_id) / RESULTS_DIRNAME
        if not directory.is_dir():
            return ()
        results = [
            decode_chunk_result(path.read_text(encoding="utf-8"))
            for path in directory.glob("*.json")
        ]
        return tuple(sorted(results, key=lambda result: result.index))

    def save_transcript(self, transcript: Transcript) -> None:
        directory = self._core.writable(transcript.job_id)
        self._core.write_atomic(directory / TRANSCRIPT, encode_transcript(transcript))

    def load_transcript(self, job_id: JobId) -> Transcript | None:
        payload = self._core.read_optional(self.job_dir(job_id) / TRANSCRIPT)
        return None if payload is None else decode_transcript(payload)

    def export_text(self, job_id: JobId, text: str) -> Path:
        """Writes the derived `.txt`. `transcript.json` is untouched: the export is
        one rendering of the transcript, never a replacement for it."""
        path = self._core.writable(job_id) / TRANSCRIPT_TEXT
        self._core.write_atomic(path, text)
        return path


def _job_id(record: Record) -> JobId:
    """Validated here because the value is about to become a path component."""
    return _id_field(record, "job_id", make_job_id)


def _word_timings(record: Record) -> tuple[WordTiming, ...]:
    """Word-level timings, treating an absent key as an older writer.

    The mirror image of how `kind` is read next door, and the asymmetry is the
    point. A stored segment always carries a kind, so an absent one is a broken
    file. **An absent `words` key is information**: every transcript written
    before slice 11 has none, and those files are on disk right now — a job that
    completed last week is old, not corrupt.

    Present-but-wrong is a different answer entirely. A key that is not a list of
    well-formed entries was written by something that meant to record timings and
    failed, and reading past it would put partial or fabricated timings into a
    transcript that then renders captions from them.

    `null` is refused rather than read as absent, because absent means "written
    before this existed" and `null` means something wrote the key with nothing in
    it. Those are different facts and only one of them is expected.
    """
    if "words" not in record:
        return ()

    return tuple(
        WordTiming(
            start_s=_number(word, "start_s"),
            end_s=_number(word, "end_s"),
            text=_text(word, "text"),
        )
        for word in _objects(record, "words")
    )


def _segment(record: Record) -> TranscriptSegment:
    # `kind` is read explicitly rather than left to the entity default. The entity
    # defaults to `UNCERTAIN` so a non-classifying adapter cannot assert speech;
    # a *stored* segment always carries a kind, so an absent one is a broken file,
    # not an unclassified one, and must not be quietly rewritten as uncertain.
    return TranscriptSegment(
        start_s=_number(record, "start_s"),
        end_s=_number(record, "end_s"),
        text=_text(record, "text"),
        speaker=_optional_text(record, "speaker"),
        confidence=_optional_number(record, "confidence"),
        kind=_member(record, "kind", SegmentKind),
        words=_word_timings(record),
    )


def _segments(record: Record) -> tuple[TranscriptSegment, ...]:
    return tuple(_segment(item) for item in _objects(record, "segments"))


def encode_chunk_plan(plan: ChunkPlan) -> str:
    return _dumps(asdict(plan))


def decode_chunk_plan(payload: str) -> ChunkPlan:
    record = _loads(payload)
    return ChunkPlan(
        job_id=_job_id(record),
        stride_s=_number(record, "stride_s"),
        overlap_s=_number(record, "overlap_s"),
        chunks=tuple(
            PlannedChunk(
                index=_whole(item, "index"),
                start_s=_number(item, "start_s"),
                end_s=_number(item, "end_s"),
            )
            for item in _objects(record, "chunks")
        ),
    )


def encode_chunk_result(result: ChunkResult) -> str:
    return _dumps(asdict(result))


def decode_chunk_result(payload: str) -> ChunkResult:
    record = _loads(payload)
    return ChunkResult(
        job_id=_job_id(record),
        index=_whole(record, "index"),
        state=_member(record, "state", ChunkState),
        segments=_segments(record),
        engine_id=_text(record, "engine_id"),
        attempts=_whole(record, "attempts"),
        error=_optional_text(record, "error"),
        finished_at=_optional_number(record, "finished_at"),
    )


def encode_transcript(transcript: Transcript) -> str:
    return _dumps(asdict(transcript))


def decode_transcript(payload: str) -> Transcript:
    record = _loads(payload)
    return Transcript(
        job_id=_job_id(record),
        segments=_segments(record),
        engine_id=_text(record, "engine_id"),
        diarized=_flag(record, "diarized"),
        language=_text(record, "language"),
    )
