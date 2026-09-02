"""Token values never leave the composition root — the logs/argv halves of AUTH-09.

The record half is proven where the record is written (`test_ownership.py`);
this file proves the two channels that survive it: nothing the request cycle
emits may carry a token, and the worker the upload starts may carry nothing
identity-related at all — only the job identifier and the data directory,
exactly as before this change.
"""

import logging
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.adapters.web.auth import build_authenticator
from onevoicecut.domain.ids import make_job_id, make_operator_id
from onevoicecut.runtime.app import WORKER_MODULE, spawn_worker
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import accepting_extractor

# Distinctive values: a leak assertion is only as good as the string it hunts.
OPERATOR_A = make_operator_id("maria")
TOKEN_A = "t-a-sekrit-value-9f3c"
TOKEN_B = "t-b-sekrit-value-71ab"


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


async def _authenticated_admit_and_upload(client: AsyncClient) -> str:
    """The full authenticated cycle: admission, then a completed upload.

    Returns the job id. Every step asserts success, so a leak check below can
    never pass because the cycle silently failed to run.
    """
    admit = await client.post(
        "/api/jobs",
        json={"engine": "local"},
        headers={"authorization": f"Bearer {TOKEN_A}"},
    )
    assert admit.status_code == 201
    job_id = str(admit.json()["job_id"])

    upload = await client.put(
        f"/api/jobs/{job_id}/media",
        content=b"hola mundo",
        headers={"authorization": f"Bearer {TOKEN_A}"},
    )
    assert upload.status_code == 204
    return job_id


async def test_no_emitted_line_carries_a_token_value(
    tmp_path: Path,
    storage: FakeTranscriptStoragePort,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AUTH-09 log half: stdout, stderr, and every captured log record of an
    authenticated admit+upload cycle are searched for both operators' token
    values and contain neither."""
    launched: list[list[str]] = []
    app = create_app(
        WebDependencies(
            storage=storage,
            authenticate=build_authenticator(
                {OPERATOR_A: TOKEN_A, make_operator_id("jose"): TOKEN_B}
            ),
            extractor_for=accepting_extractor,
            start_job=spawn_worker(tmp_path, launch=launched.append),
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        with caplog.at_level(logging.DEBUG):
            job_id = await _authenticated_admit_and_upload(client)
            # Upload queues rather than spawns, so the launcher is invoked the
            # way the supervisor will invoke it — argv still has to be produced
            # inside the captured region for this to prove anything about it.
            spawn_worker(tmp_path, launch=launched.append)(make_job_id(job_id))

    # The cycle really ran: a record exists, owned by the caller.
    job = storage.load_job(make_job_id(job_id))
    assert job.owner == OPERATOR_A
    assert launched, "argv was produced"

    emitted = capsys.readouterr()
    channels = [emitted.out, emitted.err, *(r.getMessage() for r in caplog.records)]
    for channel in channels:
        assert TOKEN_A not in channel
        assert TOKEN_B not in channel


async def test_the_worker_argv_carries_no_token_and_no_identity(
    tmp_path: Path, storage: FakeTranscriptStoragePort
) -> None:
    """AUTH-09 argv half: the launcher the composition root wires receives only
    the job identifier and the data directory — no token value, no operator
    name, nothing this change added."""
    launched: list[list[str]] = []
    app = create_app(
        WebDependencies(
            storage=storage,
            authenticate=build_authenticator(
                {OPERATOR_A: TOKEN_A, make_operator_id("jose"): TOKEN_B}
            ),
            extractor_for=accepting_extractor,
            start_job=spawn_worker(tmp_path, launch=launched.append),
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        job_id = await _authenticated_admit_and_upload(client)

    # The launcher is the supervisor's, not the upload's. Calling it directly
    # is what the supervisor does with a queued id, and argv is what this test
    # is about.
    spawn_worker(tmp_path, launch=launched.append)(make_job_id(job_id))

    assert len(launched) == 1
    argv = launched[0]
    assert argv == [
        sys.executable,
        "-m",
        WORKER_MODULE,
        "--job-id",
        job_id,
        "--data-dir",
        str(tmp_path),
    ]
    assert all(TOKEN_A not in token for token in argv)
    assert all(TOKEN_B not in token for token in argv)
    assert all(str(OPERATOR_A) not in token for token in argv)
