"""Fake conforming to TranscriptStoragePort — in-memory dicts + a real export file."""

from pathlib import Path

from transcribe.domain.chunking import ChunkPlan, ChunkResult
from transcribe.domain.generation import GenerationResult
from transcribe.domain.ids import JobId
from transcribe.domain.jobs import JobRecord
from transcribe.domain.transcript import Transcript


class FakeTranscriptStoragePort:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._jobs: dict[JobId, JobRecord] = {}
        self._chunk_plans: dict[JobId, ChunkPlan] = {}
        self._chunk_results: dict[JobId, list[ChunkResult]] = {}
        self._transcripts: dict[JobId, Transcript] = {}
        self._artifacts: dict[JobId, GenerationResult] = {}
        self._export_paths: dict[JobId, Path] = {}

    def create_job(self, job: JobRecord) -> None:
        self._jobs[job.job_id] = job

    def load_job(self, job_id: JobId) -> JobRecord:
        return self._jobs[job_id]

    def update_job(self, job: JobRecord) -> None:
        self._jobs[job.job_id] = job

    def list_jobs(self) -> tuple[JobRecord, ...]:
        return tuple(self._jobs.values())

    def save_chunk_plan(self, job_id: JobId, plan: ChunkPlan) -> None:
        self._chunk_plans[job_id] = plan

    def load_chunk_plan(self, job_id: JobId) -> ChunkPlan | None:
        return self._chunk_plans.get(job_id)

    def save_chunk_result(self, result: ChunkResult) -> None:
        self._chunk_results.setdefault(result.job_id, []).append(result)

    def load_chunk_results(self, job_id: JobId) -> tuple[ChunkResult, ...]:
        return tuple(self._chunk_results.get(job_id, []))

    def save_transcript(self, transcript: Transcript) -> None:
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
