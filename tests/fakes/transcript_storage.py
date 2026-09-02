"""Fake conforming to TranscriptStoragePort — in-memory dicts + a real export file.

It records the calls it received. That is not decoration: the core loop's central
claim is that a chunk result is committed *as it completes*, and ordering is the
only way to observe that from outside. A fake that merely stored the final state
would pass identically for a loop that batched every write to the end.
"""

from collections.abc import Callable
from pathlib import Path

from onevoicecut.domain.chunking import ChunkPlan, ChunkResult
from onevoicecut.domain.errors import JobNotFound
from onevoicecut.domain.generation import GenerationResult
from onevoicecut.domain.ids import JobId
from onevoicecut.domain.jobs import JobRecord, JobState
from onevoicecut.domain.media import SourceMedia
from onevoicecut.domain.transcript import Transcript


class FakeTranscriptStoragePort:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._jobs: dict[JobId, JobRecord] = {}
        self._chunk_plans: dict[JobId, ChunkPlan] = {}
        self._chunk_results: dict[JobId, list[ChunkResult]] = {}
        self._transcripts: dict[JobId, Transcript] = {}
        self._artifacts: dict[JobId, GenerationResult] = {}
        self._export_paths: dict[JobId, Path] = {}
        self._cancelled: dict[JobId, bool] = {}
        self._media: dict[JobId, SourceMedia] = {}
        self.calls: list[str] = []
        self._states: dict[JobId, list[JobState]] = {}
        # Lets a test act *between* chunks — the only way to exercise a stop
        # request that arrives mid-run rather than before the loop starts.
        self.on_chunk_saved: Callable[[int], None] | None = None

    def state_history(self, job_id: JobId | None = None) -> list[JobState]:
        """Every state the job was *moved to*, in order. Excludes its initial one."""
        if job_id is None:
            job_id = next(iter(self._states))
        return self._states.get(job_id, [])

    def job_dir(self, job_id: JobId) -> Path:
        return self._root / job_id

    def source_path(self, job_id: JobId) -> Path:
        return self.job_dir(job_id) / "source"

    def audio_path(self, job_id: JobId) -> Path:
        return self.job_dir(job_id) / "audio.flac"

    def chunk_path(self, job_id: JobId, index: int) -> Path:
        return self.job_dir(job_id) / "chunks" / f"{index:04d}.flac"

    def create_job(self, job: JobRecord) -> None:
        self.calls.append("create_job")
        self._jobs[job.job_id] = job

    def load_job(self, job_id: JobId) -> JobRecord:
        # `JobNotFound`, not `KeyError`. A fake that raises a different type than
        # the real adapter is not a fake, it is a second implementation with its
        # own contract — and callers written against it break in production.
        if job_id not in self._jobs:
            raise JobNotFound(f"no job stored under {job_id!r}")
        return self._jobs[job_id]

    def update_job(self, job: JobRecord) -> None:
        self.calls.append(f"update_job:{job.state}")
        self._states.setdefault(job.job_id, []).append(job.state)
        self._jobs[job.job_id] = job

    def list_jobs(self) -> tuple[JobRecord, ...]:
        # Sorted, like the real adapter, because the port promises it and the
        # drain gate's FIFO fairness rests on it. Returning insertion order here
        # would let ordering bugs pass every unit test and only appear against a
        # real directory.
        return tuple(sorted(self._jobs.values(), key=lambda job: job.job_id))

    def save_media(self, job_id: JobId, media: SourceMedia) -> None:
        # Recorded so the queue contract can be asserted as an *ordering*: the
        # media must be described before the record says QUEUED, because QUEUED
        # is what makes a supervisor spawn a worker that reads it.
        self.calls.append("save_media")
        self._media[job_id] = media

    def load_media(self, job_id: JobId) -> SourceMedia:
        if job_id not in self._media:
            raise JobNotFound(f"no source media recorded for {job_id!r}")
        return self._media[job_id]

    def save_chunk_plan(self, job_id: JobId, plan: ChunkPlan) -> None:
        self.calls.append("save_chunk_plan")
        self._chunk_plans[job_id] = plan

    def load_chunk_plan(self, job_id: JobId) -> ChunkPlan | None:
        return self._chunk_plans.get(job_id)

    def save_chunk_result(self, result: ChunkResult) -> None:
        self.calls.append(f"save_chunk_result:{result.index}")
        kept = [
            existing
            for existing in self._chunk_results.setdefault(result.job_id, [])
            if existing.index != result.index
        ]
        kept.append(result)
        self._chunk_results[result.job_id] = kept
        if self.on_chunk_saved is not None:
            self.on_chunk_saved(result.index)

    def load_chunk_results(self, job_id: JobId) -> tuple[ChunkResult, ...]:
        return tuple(
            sorted(self._chunk_results.get(job_id, []), key=lambda r: r.index)
        )

    def save_transcript(self, transcript: Transcript) -> None:
        self.calls.append("save_transcript")
        self._transcripts[transcript.job_id] = transcript

    def load_transcript(self, job_id: JobId) -> Transcript | None:
        return self._transcripts.get(job_id)

    def save_artifacts(self, job_id: JobId, artifacts: GenerationResult) -> None:
        self._artifacts[job_id] = artifacts

    def export_text(self, job_id: JobId, text: str) -> Path:
        path = self._root / f"{job_id}.txt"
        path.write_text(text, encoding="utf-8")
        self._export_paths[job_id] = path
        return path

    def load_export_path(self, job_id: JobId) -> Path | None:
        return self._export_paths.get(job_id)

    def request_cancellation(self, job_id: JobId, *, requested: bool = True) -> None:
        # Recorded, because cancellation's central claim is about *who writes
        # what*: a terminal job must come out of the route with the control file
        # untouched, and an untouched file is only observable as an absent call.
        self.calls.append(f"request_cancellation:{requested}")
        self._cancelled[job_id] = requested

    def cancellation_requested(self, job_id: JobId) -> bool:
        self.calls.append("cancellation_requested")
        return self._cancelled.get(job_id, False)
