"""Central error mapping, installed once at the composition root.

The handler replaces the per-route `try/except` the routes used to carry: a
route raises a domain error and the edge decides the status, so the mapping
table exists in exactly one place. The statuses are the ones the route-local
translations already produced — the existing integration tests are the
equivalence proof, and this file pins the table itself, including a novel
error no route has ever heard of (the "any other `DomainError` → 422" row).

The response half of AUTH-12 lives here too: an *unexpected* exception answers
500 with no detail in the body or headers — what happened goes to the server
log, never to the caller.
"""

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.main import WebDependencies
from onevoicecut.shared.domain.speaker import SpeakerMode
from onevoicecut.systems.pipeline.jobs.domain.jobs import EngineChoice, JobRecord, JobState
from onevoicecut.main import create_app, get_app
from onevoicecut.shared.domain.errors import (
    ArtifactsNotAvailable,
    ClipCandidateNotFound,
    DomainError,
    JobAlreadyExists,
    JobNotOwned,
    JobNotFound,
    UnsupportedContainer,
    UploadTooLarge,
)
from onevoicecut.shared.domain.ids import make_job_id, make_media_id, make_operator_id
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import fake_authenticate


def client_raising(
    tmp_path: Path,
    error: Exception,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    """An app whose only route raises `error`, with no route-local translation
    anywhere on the path — the composition root's handler is the only thing
    between the raise and the response."""
    app = create_app(
        WebDependencies(
            storage=FakeTranscriptStoragePort(tmp_path),
            authenticate=fake_authenticate,
        )
    )

    def boom() -> None:
        raise error

    app.add_api_route("/boom", boom, methods=["GET"])
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        pytest.param(
            JobNotFound("job 01ABC is not known"), 404, id="job-not-found"
        ),
        pytest.param(
            JobNotOwned("job 01ABC is not owned by operator maria"),
            403,
            id="job-not-owned",
        ),
        pytest.param(
            JobAlreadyExists("job 01ABC already exists"), 409, id="job-already-exists"
        ),
        pytest.param(
            ArtifactsNotAvailable("no artifacts yet"), 409, id="artifacts-not-available"
        ),
        pytest.param(
            ClipCandidateNotFound("no candidate 7"), 404, id="clip-candidate-not-found"
        ),
        pytest.param(UploadTooLarge("too large"), 413, id="upload-too-large"),
        pytest.param(
            UnsupportedContainer("not a media file"), 415, id="unsupported-container"
        ),
        pytest.param(
            DomainError("nothing has classified this yet"),
            422,
            id="novel-domain-error",
        ),
    ],
)
def test_the_composition_root_maps_domain_errors(
    tmp_path: Path, error: DomainError, expected_status: int
) -> None:
    response = client_raising(tmp_path, error).get("/boom")

    assert response.status_code == expected_status


def test_the_error_detail_travels_verbatim(tmp_path: Path) -> None:
    """Equivalence with the route-local translations this replaces: those
    answered `{"detail": str(error)}` and the central handler must not change
    a single body the existing integration tests pin."""
    message = "job 01HQ3M8XKJ7VNPQR2ZYWB4TCFD is not known"

    response = client_raising(tmp_path, JobNotFound(message)).get("/boom")

    assert response.content == b'{"detail":"' + message.encode() + b'"}'


def test_a_refused_owner_is_not_named(tmp_path: Path) -> None:
    """The one detail the table does not take from the error.

    `JobNotOwned`'s own message names the job and the operator, and a 403 on
    the shared board must not: foreign job existence is public, but the
    refusal is not where a stranger learns who holds it."""
    response = client_raising(
        tmp_path,
        JobNotOwned("job 01ABC is not owned by operator maria"),
    ).get("/boom")

    assert response.status_code == 403
    assert response.content == b'{"detail":"not the owner of this job"}'
    assert b"maria" not in response.content


def test_an_unexpected_exception_is_a_silent_500(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """AUTH-12: the caller learns only that something broke; the caller's
    credentials and the exception detail stay server-side. The traceback is
    logged (with the method and path — never headers, never a body), and
    Starlette's server-error middleware re-raises after this response so the
    server's own log gets it too."""
    marker = "detail-that-must-not-reach-the-caller"
    token = "t-secret-that-must-not-be-logged"

    with caplog.at_level(logging.ERROR):
        response = client_raising(
            tmp_path, RuntimeError(marker), raise_server_exceptions=False
        ).get("/boom", headers={"authorization": f"Bearer {token}"})

    assert response.status_code == 500
    assert marker.encode() not in response.content
    assert all(
        marker.encode() not in value.encode() for value in response.headers.values()
    )
    assert "unhandled exception on GET /boom" in caplog.text
    assert token not in caplog.text


def test_the_real_entrypoint_keeps_401_before_404_before_403(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The precedence AUTH-13/14/15 pin, driven through `main.get_app` itself.

    Every other precedence test builds its app from `create_app` over a fake
    store. This one goes through the entrypoint an operator actually runs, so
    it is the one that catches a divergence in the real wiring: a token map
    parsed from `Settings`, a real filesystem store, and the central handler
    installed by the same code path uvicorn takes. The 403 half in particular
    reaches the composition root's `JobNotOwned` row — the route no longer
    translates it — so a table that lost that row answers 500 here.
    """
    owner = make_operator_id("maria")
    stranger = make_operator_id("rita")
    job_id = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCA1")
    monkeypatch.setenv("ONEVOICECUT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ONEVOICECUT_OPERATOR_TOKENS", "maria:tok-maria;rita:tok-rita")

    storage = FilesystemTranscriptStorage(tmp_path)
    storage.create_job(
        JobRecord(
            job_id=job_id,
            media_id=make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE"),
            state=JobState.PENDING,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=1.0,
            updated_at=1.0,
            worker_pid=None,
            error=None,
            owner=owner,
        )
    )

    # No `with`: entering the lifespan would run `require_binaries` and start
    # the three supervisors, none of which this test is about — and none of
    # which any route test in this suite has ever started either.
    client = TestClient(get_app())
    unauthenticated = client.post(f"/api/jobs/{job_id}/cancel")
    unknown_id = client.post(
        "/api/jobs/not-a-ulid/cancel",
        headers={"authorization": "Bearer tok-maria"},
    )
    foreign_job = client.post(
        f"/api/jobs/{job_id}/cancel",
        headers={"authorization": "Bearer tok-rita"},
    )

    # An unauthenticated caller never learns whether the id exists.
    assert unauthenticated.status_code == 401
    # An authenticated one reaches the id check before any ownership question.
    assert unknown_id.status_code == 404
    # Only after both does the refusal come back — from the central table, and
    # generic: the detail must not name the stranger who was turned away.
    assert foreign_job.status_code == 403
    assert foreign_job.content == b'{"detail":"not the owner of this job"}'
    assert b"rita" not in foreign_job.content
