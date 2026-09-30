"""The jobs controller's contract, pinned before the routes reach it.

The route tests beside this file prove the five operations over HTTP. This one
pins the three decisions the controller makes on the way in, each of which a
route-level test can only see indirectly:

- a malformed id is refused *before* the store is touched, and it answers with
  the route's own HTTP refusal rather than a domain error, because the path
  parameter is the controller's question;
- an ownership refusal arrives generic — the domain's message names the job and
  the operator, and neither belongs in a 403 body;
- nothing else is caught. `JobNotFound` and every other domain refusal must
  keep travelling to the composition root, or the single table in `main.py`
  stops being the single table and a new error silently answers 500.
"""

import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from onevoicecut.main import filesystem_media_source
from onevoicecut.systems.pipeline.jobs.domain.interfaces.progress_store import JobProgressStore
from onevoicecut.shared.application.principal import Principal
from onevoicecut.shared.domain.errors import JobNotFound
from onevoicecut.shared.domain.ids import make_operator_id
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.admit_job import AdmitJobHandler
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.cancel_job import CancelJobHandler
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.ingest_media import IngestMediaHandler
from onevoicecut.systems.pipeline.jobs.application.use_cases.queries.get_job import GetJobHandler
from onevoicecut.systems.pipeline.jobs.application.use_cases.queries.list_jobs import ListJobsHandler
from onevoicecut.systems.pipeline.jobs.domain.jobs import EngineChoice
from onevoicecut.systems.pipeline.jobs.presentation.controllers.v1.job_controller import JobsController
from onevoicecut.systems.pipeline.jobs.presentation.schemas.v1.job_schemas import AdmitJobRequest
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import accepting_extractor

PRINCIPAL_A = Principal(identity=make_operator_id("maria"), roles=frozenset())
PRINCIPAL_B = Principal(identity=make_operator_id("rita"), roles=frozenset())
# Well-formed and known to no store: the id check and the load are separate
# questions, and only the second one may reach the store.
UNKNOWN_JOB_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFD"


def _controller(storage: JobProgressStore) -> JobsController:
    return JobsController(
        admit_handler=AdmitJobHandler(storage=storage),
        ingest_handler=IngestMediaHandler(
            storage=storage,
            max_upload_bytes=1024**3,
            media_source_for=filesystem_media_source,
            extractor_for=accepting_extractor,
            now=time.time,
        ),
        cancel_handler=CancelJobHandler(storage=storage),
        get_job_handler=GetJobHandler(storage=storage),
        list_jobs_handler=ListJobsHandler(storage=storage),
    )


def test_a_malformed_id_is_refused_before_the_store_is_touched(
    tmp_path: Path,
) -> None:
    """404 with the route's own wording, and no filesystem access at all.

    The handler for this operation answers a malformed id with `JobNotFound`,
    so an `HTTPException` here can only have come from the controller — which
    is the ordering AUTH-14 wants: the id is checked before a path built from
    it can reach the store."""
    controller = _controller(FakeTranscriptStoragePort(tmp_path))

    with pytest.raises(HTTPException) as refused:
        controller.status("not-a-ulid")

    assert refused.value.status_code == 404
    assert refused.value.detail == "no such job"
    assert list(tmp_path.rglob("*")) == []


def test_an_ownership_refusal_is_generic_and_never_names_the_owner(
    tmp_path: Path,
) -> None:
    """The one translation this layer is allowed to perform (AUTH-10).

    `require_owner` raises with the job and the operator in the message —
    useful to a non-HTTP caller, wrong in a 403 body, where a stranger would
    learn who holds the job they just probed."""
    controller = _controller(FakeTranscriptStoragePort(tmp_path))
    admission = controller.admit(PRINCIPAL_A, AdmitJobRequest(engine=EngineChoice.LOCAL))

    with pytest.raises(HTTPException) as refused:
        controller.cancel(str(admission.job_id), PRINCIPAL_B)

    assert refused.value.status_code == 403
    assert refused.value.detail == "not the owner of this job"
    assert str(PRINCIPAL_B.identity) not in str(refused.value.detail)


def test_nothing_but_an_ownership_refusal_is_translated(tmp_path: Path) -> None:
    """`JobNotFound` reaches the composition root's table untouched.

    Catching it here would give one route its own mapping and leave the table
    to guess for the rest — the scattering this change exists to remove."""
    controller = _controller(FakeTranscriptStoragePort(tmp_path))

    with pytest.raises(JobNotFound):
        controller.status(UNKNOWN_JOB_ID)


def test_the_listing_projects_records_into_rows(tmp_path: Path) -> None:
    """Schema in, DTO out: the row carries the record's own fields, named as
    the API names them, and nothing the record did not have."""
    controller = _controller(FakeTranscriptStoragePort(tmp_path))
    admission = controller.admit(PRINCIPAL_A, AdmitJobRequest(engine=EngineChoice.LOCAL))

    listing = controller.listing(PRINCIPAL_A, mine=False)

    assert [row.job_id for row in listing.jobs] == [admission.job_id]
    assert listing.jobs[0].owner == PRINCIPAL_A.identity
