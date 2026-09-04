"""Owner immutability across the lifecycle — OWN-02.

Ownership is written once at admission. Every transition that exists at this
point — the media commit, the worker's claim, a worker-shaped state advance,
and the startup reconcile rewrite — must carry the owner through unchanged.
The second half is structural: `dataclasses.replace` is the only vehicle any
code uses to mutate a record, and `replace` carries every field it is not
told to change — so a future transition written the same way cannot drop the
owner by accident.
"""

import ast
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.ids import make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobState, SpeakerMode
from onevoicecut.domain.media import SourceMedia
from onevoicecut.runtime.app import reconcile_interrupted_jobs
from onevoicecut.usecases.admit_job import admit_job

OPERATOR_A = make_operator_id("a")
OPERATOR_B = make_operator_id("b")
WORKER_PID = 4812
SRC_ROOT = Path(__file__).resolve().parents[3] / "src" / "onevoicecut"


def test_owner_survives_every_transition_that_exists(
    tmp_path: Path,
) -> None:
    """Admission → media commit → worker claim → worker advance → reconcile
    rewrite: the record re-loaded from disk carries owner "a" at every point."""
    storage = FilesystemTranscriptStorage(tmp_path)

    job = admit_job(
        engine=EngineChoice.LOCAL,
        speaker_mode=SpeakerMode.SINGLE,
        operator=OPERATOR_A,
        storage=storage,
    ).job
    assert storage.load_job(job.job_id).owner == OPERATOR_A

    storage.save_media(
        job.job_id,
        SourceMedia(
            media_id=job.media_id,
            original_filename="sermón.mp4",
            stored_path=storage.source_path(job.job_id),
            size_bytes=10,
            container="mov,mp4,m4a",
            checksum="0" * 64,
        ),
    )
    assert storage.load_job(job.job_id).owner == OPERATOR_A

    # The worker's claim, shaped exactly like `run_job`'s first write.
    claimed = replace(job, worker_pid=WORKER_PID)
    storage.update_job(claimed)
    assert storage.load_job(job.job_id).owner == OPERATOR_A

    # A worker-shaped state advance, shaped like `transcribe_job`'s `_advance`.
    advanced = replace(claimed, state=JobState.TRANSCRIBING, updated_at=2.0)
    storage.update_job(advanced)
    assert storage.load_job(job.job_id).owner == OPERATOR_A

    # The startup reconcile rewrite over that same record with a dead worker.
    reconciled = reconcile_interrupted_jobs(
        storage, now=lambda: 5.0, is_alive=lambda pid: False
    )
    assert reconciled == (job.job_id,)
    final = storage.load_job(job.job_id)
    assert final.state is JobState.INTERRUPTED
    assert final.owner == OPERATOR_A


def test_the_record_is_frozen_so_ownership_cannot_be_reassigned(
    tmp_path: Path,
) -> None:
    """The immutability is structural, not conventional: assigning to `owner`
    on a live record refuses."""
    storage = FilesystemTranscriptStorage(tmp_path)
    job = admit_job(
        engine=EngineChoice.LOCAL,
        speaker_mode=SpeakerMode.SINGLE,
        operator=OPERATOR_A,
        storage=storage,
    ).job

    with pytest.raises(FrozenInstanceError):
        job.owner = OPERATOR_B  # type: ignore[misc]


def _update_job_call_sites(root: Path) -> list[tuple[Path, ast.Call]]:
    sites: list[tuple[Path, ast.Call]] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update_job"
            ):
                sites.append((path, node))
    return sites


def _replace_assignments(tree: ast.Module) -> dict[str, ast.Call]:
    """Local names whose assignment is a `replace(...)` call."""
    assigned: dict[str, ast.Call] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            func = node.value.func
            if isinstance(func, ast.Name) and func.id == "replace":
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned[target.id] = node.value
    return assigned


def test_every_record_mutation_goes_through_dataclasses_replace() -> None:
    """OWN-02 structural half: no call site passes `update_job` a record built
    any other way. `replace` copies every field it is not told to change, so
    the owner travels with every transition by construction — a mutation path
    that could drop it cannot be written in this style, and one written in
    another style fails here."""
    sites = _update_job_call_sites(SRC_ROOT)
    assert len(sites) > 0, "the scan found no update_job call sites"

    for path, call in sites:
        assert call.args, f"{path}: update_job called without a record argument"
        record = call.args[0]
        if isinstance(record, ast.Call):
            func = record.func
            assert (
                isinstance(func, ast.Name) and func.id == "replace"
            ), f"{path}:{call.lineno} passes update_job a record not built by replace"
            continue
        if isinstance(record, ast.Name):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            built_by = _replace_assignments(tree).get(record.id)
            assert built_by is not None, (
                f"{path}:{call.lineno} passes update_job a name not assigned "
                f"from replace(...)"
            )
            continue
        raise AssertionError(
            f"{path}:{call.lineno} passes update_job something the scan "
            f"cannot recognise as replace(...)"
        )
