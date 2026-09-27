"""The resume seam: transcripts derives, the jobs command receives — across a jobs-owned interface.

`pending_chunks` is pure over `ChunkPlan` and `ChunkResult`, both transcripts
domain, so the derivation lives there and is published through the transcripts
module API, which is how the supervisor reaches it. The jobs-side resume command
must NOT follow it across: a jobs application module naming `transcripts.domain`
would put one system's application on the other system's domain types, which
AB-07 forbids (and the architecture suite enforces automatically). So the jobs
command receives the derivation through a Protocol declared in
`jobs/domain/interfaces` — its own seam, its own vocabulary.

This test pins both hops: the module API exposes the derivation, and the jobs
command's handler consumes it through that interface without either side
importing the other's domain.
"""

from typing import Any, Protocol, cast, get_type_hints

from onevoicecut.shared.domain.ids import make_job_id
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.resume_job import (
    ResumeJobCommand,
    ResumeJobHandler,
)
from onevoicecut.systems.pipeline.jobs.domain.interfaces.resume import PendingChunks
from onevoicecut.systems.pipeline.transcripts.domain.chunking import (
    ChunkPlan,
    ChunkResult,
    ChunkState,
    PlannedChunk,
)
from onevoicecut.systems.pipeline.transcripts import transcripts_module_api

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")


def a_plan(count: int) -> ChunkPlan:
    return ChunkPlan(
        job_id=JOB_ID,
        stride_s=600.0,
        overlap_s=5.0,
        chunks=tuple(
            PlannedChunk(index=i, start_s=i * 600.0, end_s=(i + 1) * 600.0 + 5.0)
            for i in range(count)
        ),
    )


def a_result(index: int, state: ChunkState = ChunkState.DONE) -> ChunkResult:
    return ChunkResult(
        job_id=JOB_ID,
        index=index,
        state=state,
        segments=(),
        engine_id="fake-asr",
        attempts=1,
        error=None,
        finished_at=1.0,
    )


def indices(chunks: tuple[PlannedChunk, ...]) -> list[int]:
    return [chunk.index for chunk in chunks]


def test_the_transcripts_module_api_exposes_the_derivation() -> None:
    """Hop one: roots (the supervisor) reach `pending_chunks` through the module
    API rather than by importing a use-case file."""
    owed = transcripts_module_api.pending_chunks(a_plan(3), (a_result(0),))

    assert indices(owed) == [1, 2]


def test_the_jobs_application_declares_its_own_seam_interface() -> None:
    """Hop two: the jobs side names its Protocol, never `transcripts.domain`.

    The parameter types here are transcripts domain entities, which is exactly
    what `jobs/domain/interfaces` may say out loud — the module that may not is
    the jobs *application*.
    """
    # Not `issubclass(X, Protocol)`: mypy rejects Protocol as a class arg.
    assert Protocol in cast(Any, PendingChunks.__bases__)

    hints = get_type_hints(PendingChunks.__call__)
    assert hints["plan"] is ChunkPlan
    # Compared, not `is`: each subscription builds a fresh alias object.
    assert hints["results"] == tuple[ChunkResult, ...]


def test_the_jobs_resume_command_receives_the_derivation() -> None:
    """The handler is wired with the callable, not with a domain import: give it
    the module API's derivation and it answers with the owed chunks."""
    handler = ResumeJobHandler(pending_chunks=transcripts_module_api.pending_chunks)

    owed = handler.handle(ResumeJobCommand(plan=a_plan(3), results=(a_result(0),)))

    assert indices(owed) == [1, 2]
