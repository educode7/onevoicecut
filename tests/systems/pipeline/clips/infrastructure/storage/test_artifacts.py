"""The artifact set a job accumulates once its record exists — `ClipStore`'s half.

`artifacts.json` is clip-production state (OQ3), so it is read and written through
the clips facade over the shared `core`. Absence is the interesting case here: a
job with no artifacts yet is a normal mid-run state, not an error, and the store
says so by returning `None` — the same reading rule the plan and transcript
loaders next door follow.

The job record these writes are strict about is admission's to create, so the
fixtures build the jobs facade and this one over a single `StorageCore`, the way
a composition root builds them.
"""

from pathlib import Path

import pytest

from onevoicecut.shared.domain.ids import JobId, make_job_id, make_media_id
from onevoicecut.shared.domain.speaker import SpeakerMode
from onevoicecut.shared.infrastructure.storage.core import StorageCore
from onevoicecut.systems.pipeline.clips.domain.generation import (
    ClipCandidate,
    GenerationResult,
    ScriptVariant,
)
from onevoicecut.systems.pipeline.clips.infrastructure.storage.clip_store import (
    FilesystemClipStore,
    decode_artifacts,
)
from onevoicecut.systems.pipeline.jobs.domain.jobs import (
    EngineChoice,
    JobRecord,
    JobState,
)
from onevoicecut.systems.pipeline.jobs.infrastructure.storage.job_store import (
    FilesystemJobStore,
)

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")


def a_job(job_id: JobId) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        media_id=MEDIA_ID,
        state=JobState.TRANSCRIBING,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1723501234.5,
        updated_at=1723501234.5,
        worker_pid=None,
        error=None,
        owner=None,
    )


@pytest.fixture
def core(tmp_path: Path) -> StorageCore:
    return StorageCore(tmp_path)


@pytest.fixture
def jobs(core: StorageCore) -> FilesystemJobStore:
    """Admission's half: the job record every artifact write is strict about."""
    store = FilesystemJobStore(core)
    store.create_job(a_job(JOB_ID))
    return store


@pytest.fixture
def storage(core: StorageCore, jobs: FilesystemJobStore) -> FilesystemClipStore:
    return FilesystemClipStore(core)


def an_artifact_set() -> GenerationResult:
    return GenerationResult(
        job_id=JOB_ID,
        summary="Resumen del mensaje.",
        clip_candidates=(
            ClipCandidate(
                start_s=10.0,
                end_s=45.0,
                hook="gancho",
                quote="cita",
                rationale="razon",
                score=0.7,
                variants=(
                    ScriptVariant(
                        target="tiktok",
                        format="vertical",
                        body="guion",
                        duration_target_s=45.0,
                    ),
                ),
            ),
        ),
    )


def test_artifacts_are_persisted(storage: FilesystemClipStore) -> None:
    storage.save_artifacts(JOB_ID, an_artifact_set())

    stored = (storage.job_dir(JOB_ID) / "artifacts.json").read_text(encoding="utf-8")
    assert decode_artifacts(stored) == an_artifact_set()


def test_artifacts_round_trip_through_load(
    storage: FilesystemClipStore,
) -> None:
    storage.save_artifacts(JOB_ID, an_artifact_set())

    assert storage.load_artifacts(JOB_ID) == an_artifact_set()


def test_a_job_with_no_artifacts_yet_reports_none(
    storage: FilesystemClipStore,
) -> None:
    """Absence is a normal mid-run state, the way an unplanned job has no chunk
    plan — not an error a caller has to catch."""
    assert storage.load_artifacts(JOB_ID) is None
