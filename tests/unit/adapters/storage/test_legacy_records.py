"""Pre-change records against the real filesystem adapter (LEG-01..08).

Operator machines already hold `job.json` files written before owners existed.
The codec matrix in `test_serialization.py` proves the tolerant decode in
isolation; this file proves it where it actually bites — against the adapter
that owns the on-disk layout, with boot-time listing and reconcile running over
mixed populations. Every record here is either hand-written byte-for-byte as the
pre-change build wrote it, or written by the post-change `encode_job`.
"""

import json
from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    JOB_RECORD,
    FilesystemTranscriptStorage,
)
from onevoicecut.adapters.storage.serialization import encode_job
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.runtime.app import reconcile_interrupted_jobs

# Ids chosen to sort F0 < F1 < F2 < F3, so listing assertions are pinned to
# creation order the way ULID directory names give it.
LEGACY_PENDING_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCF0")
LEGACY_DEAD_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCF1")
OWNED_DEAD_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCF2")
OWNED_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCF3")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OPERATOR = make_operator_id("maria")
NOW = 2000.0
DEAD_PID = 4813


def pre_change_payload(
    job_id: JobId, *, state: JobState, worker_pid: int | None
) -> dict[str, object]:
    """Exactly what the pre-change `asdict` wrote: every known field, no owner."""
    return {
        "job_id": str(job_id),
        "media_id": str(MEDIA_ID),
        "state": state,
        "speaker_mode": SpeakerMode.SINGLE,
        "engine": EngineChoice.LOCAL,
        "created_at": 1.0,
        "updated_at": 1.0,
        "worker_pid": worker_pid,
        "error": None,
    }


def write_pre_change_record(
    storage: FilesystemTranscriptStorage,
    job_id: JobId,
    *,
    state: JobState,
    worker_pid: int | None,
) -> Path:
    """Hand-write the bytes a pre-change build would have left on disk."""
    directory = storage.job_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / JOB_RECORD
    path.write_text(
        json.dumps(
            pre_change_payload(job_id, state=state, worker_pid=worker_pid), indent=2
        ),
        encoding="utf-8",
    )
    return path


def an_owned_job(job_id: JobId, *, worker_pid: int | None = None) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        media_id=MEDIA_ID,
        state=JobState.TRANSCRIBING,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1.0,
        updated_at=1.0,
        worker_pid=worker_pid,
        error=None,
        owner=OPERATOR,
    )


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemTranscriptStorage:
    return FilesystemTranscriptStorage(tmp_path)


def test_a_pre_change_record_loads_and_lists_ownerless(
    storage: FilesystemTranscriptStorage,
) -> None:
    """LEG-01 at the adapter level: absence is the legacy shape, not corruption."""
    write_pre_change_record(
        storage, LEGACY_PENDING_ID, state=JobState.PENDING, worker_pid=None
    )

    loaded = storage.load_job(LEGACY_PENDING_ID)
    listed = storage.list_jobs()

    assert loaded.owner is None
    assert [record.job_id for record in listed] == [LEGACY_PENDING_ID]
    assert listed[0].owner is None


def test_a_legacy_only_directory_lists_completely(
    storage: FilesystemTranscriptStorage,
) -> None:
    """LEG-05: boot over a pre-change data directory loses no job."""
    write_pre_change_record(
        storage, LEGACY_PENDING_ID, state=JobState.PENDING, worker_pid=None
    )
    write_pre_change_record(
        storage, LEGACY_DEAD_ID, state=JobState.COMPLETED, worker_pid=None
    )

    listed = storage.list_jobs()

    assert [record.job_id for record in listed] == [LEGACY_PENDING_ID, LEGACY_DEAD_ID]
    assert all(record.owner is None for record in listed)


def test_a_mixed_directory_lists_every_record_attributed(
    storage: FilesystemTranscriptStorage,
) -> None:
    """LEG-06 listing half: legacy and owned records coexist in one listing."""
    write_pre_change_record(
        storage, LEGACY_PENDING_ID, state=JobState.PENDING, worker_pid=None
    )
    storage.create_job(an_owned_job(OWNED_ID))

    listed = storage.list_jobs()
    owners = {record.job_id: record.owner for record in listed}

    assert len(listed) == 2
    assert owners[LEGACY_PENDING_ID] is None
    assert owners[OWNED_ID] == OPERATOR


def test_reconcile_marks_dead_jobs_of_both_kinds_without_backfill(
    storage: FilesystemTranscriptStorage,
) -> None:
    """LEG-07 and LEG-06's reconcile half, byte-checked.

    Reconcile is owner-blind: a dead worker is interrupted whether or not the
    record has an owner, and nothing invents an owner for a legacy record. The
    only bytes that move are the reconciled state rewrites — and the legacy
    rewrite gains `"owner": null` by the encode rule, never a name.
    """
    untouched = write_pre_change_record(
        storage, LEGACY_PENDING_ID, state=JobState.PENDING, worker_pid=None
    )
    legacy_dead = write_pre_change_record(
        storage, LEGACY_DEAD_ID, state=JobState.TRANSCRIBING, worker_pid=DEAD_PID
    )
    storage.create_job(an_owned_job(OWNED_DEAD_ID, worker_pid=DEAD_PID))
    before_untouched = untouched.read_text(encoding="utf-8")
    before_legacy_dead = json.loads(legacy_dead.read_text(encoding="utf-8"))
    before_owned_dead = json.loads(
        (storage.job_dir(OWNED_DEAD_ID) / JOB_RECORD).read_text(encoding="utf-8")
    )

    storage.list_jobs()
    reconciled = reconcile_interrupted_jobs(
        storage, now=lambda: NOW, is_alive=lambda pid: False
    )

    # Both dead jobs reconciled, the pending one untouched — ids in ULID order.
    assert reconciled == (LEGACY_DEAD_ID, OWNED_DEAD_ID)
    assert storage.load_job(LEGACY_DEAD_ID).state is JobState.INTERRUPTED
    assert storage.load_job(OWNED_DEAD_ID).state is JobState.INTERRUPTED
    assert storage.load_job(LEGACY_PENDING_ID).state is JobState.PENDING

    # No owner was invented anywhere.
    assert storage.load_job(LEGACY_DEAD_ID).owner is None
    assert storage.load_job(OWNED_DEAD_ID).owner == OPERATOR
    assert storage.load_job(LEGACY_PENDING_ID).owner is None

    # A record reconcile never visits keeps its exact bytes.
    assert untouched.read_text(encoding="utf-8") == before_untouched

    # The legacy rewrite changes state and timestamp only; the owner key joins
    # as null because encoding always writes it — never as a backfilled name.
    after_legacy_dead = json.loads(legacy_dead.read_text(encoding="utf-8"))
    assert set(after_legacy_dead) - set(before_legacy_dead) == {"owner"}
    assert after_legacy_dead["owner"] is None
    legacy_changed = {
        key
        for key in before_legacy_dead
        if before_legacy_dead[key] != after_legacy_dead[key]
    }
    assert legacy_changed == {"state", "updated_at"}

    # The owned rewrite changes state and timestamp only — owner intact.
    after_owned_dead = json.loads(
        (storage.job_dir(OWNED_DEAD_ID) / JOB_RECORD).read_text(encoding="utf-8")
    )
    assert set(after_owned_dead) == set(before_owned_dead)
    owned_changed = {
        key
        for key in before_owned_dead
        if before_owned_dead[key] != after_owned_dead[key]
    }
    assert owned_changed == {"state", "updated_at"}
    assert after_owned_dead["owner"] == "maria"


def test_an_owned_record_survives_a_pre_change_decode() -> None:
    """LEG-08 decode half — the rollback invariant.

    The pre-change decode was field-explicit: it read exactly its known fields
    and ignored any unknown key. A record written by this build must therefore
    read cleanly if the deployment is reverted, with every pre-change field
    carrying its unchanged meaning.
    """
    job = an_owned_job(OWNED_ID)
    payload = json.loads(encode_job(job))

    def pre_change_field_explicit_decode(record: dict[str, object]) -> dict[str, object]:
        return {
            "job_id": record["job_id"],
            "media_id": record["media_id"],
            "state": record["state"],
            "speaker_mode": record["speaker_mode"],
            "engine": record["engine"],
            "created_at": record["created_at"],
            "updated_at": record["updated_at"],
            "worker_pid": record["worker_pid"],
            "error": record["error"],
        }

    decoded = pre_change_field_explicit_decode(payload)

    assert decoded["job_id"] == str(job.job_id)
    assert decoded["media_id"] == str(job.media_id)
    assert decoded["state"] == JobState.TRANSCRIBING
    assert decoded["speaker_mode"] == SpeakerMode.SINGLE
    assert decoded["engine"] == EngineChoice.LOCAL
    assert decoded["created_at"] == job.created_at
    assert decoded["updated_at"] == job.updated_at
    assert decoded["worker_pid"] == job.worker_pid
    assert decoded["error"] == job.error
    # The unknown key is ignored, not fatal.
    assert "owner" not in decoded
