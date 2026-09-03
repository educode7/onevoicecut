"""Web-layer rejection of incompatible engine/speaker-mode combinations.

When the operator submits a job with speaker_mode=MULTI against an engine
that cannot diarize, the web route must return HTTP 422 with a response
body naming the missing capability and providing remediation text.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.domain.jobs import EngineChoice, SpeakerMode
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DiarizationSupport,
)
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import auth_headers, fake_authenticate

FIXED_NOW = 1723501234.5


def _unsupported_caps(_engine: EngineChoice) -> DiarizationSupport:
    return DiarizationSupport.UNSUPPORTED


def _available_caps(_engine: EngineChoice) -> DiarizationSupport:
    return DiarizationSupport.AVAILABLE


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    return FakeTranscriptStoragePort(tmp_path)


@pytest.fixture
async def unsupported_client(
    storage: FakeTranscriptStoragePort,
) -> AsyncIterator[AsyncClient]:
    deps = WebDependencies(
        storage=storage,
        authenticate=fake_authenticate,
        now=lambda: FIXED_NOW,
        capabilities=_unsupported_caps,
    )
    app = create_app(deps)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers()
    ) as http:
        yield http


@pytest.fixture
async def supported_client(
    storage: FakeTranscriptStoragePort,
) -> AsyncIterator[AsyncClient]:
    deps = WebDependencies(
        storage=storage,
        authenticate=fake_authenticate,
        now=lambda: FIXED_NOW,
        capabilities=_available_caps,
    )
    app = create_app(deps)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=auth_headers()
    ) as http:
        yield http


async def test_diarization_unsupported_returns_422(
    unsupported_client: AsyncClient,
    storage: FakeTranscriptStoragePort,
) -> None:
    """6.9: MULTI + UNSUPPORTED → 422 with remediation."""
    response = await unsupported_client.post(
        "/api/jobs", json={"engine": "local", "speaker_mode": "multi"}
    )

    assert response.status_code == 422
    body = response.json()["detail"]
    assert "diarization" in body.lower()
    assert storage.list_jobs() == ()


async def test_diarization_unsupported_names_remediation(
    unsupported_client: AsyncClient,
) -> None:
    """6.9 (triangulation): error body suggests engine switch or mode drop."""
    response = await unsupported_client.post(
        "/api/jobs", json={"engine": "local", "speaker_mode": "multi"}
    )

    body = response.json()["detail"]
    assert "engine" in body.lower() or "single" in body.lower()


async def test_compatible_combination_admitted(
    supported_client: AsyncClient,
    storage: FakeTranscriptStoragePort,
) -> None:
    """6.9 (triangulation): MULTI + AVAILABLE succeeds over HTTP."""
    response = await supported_client.post(
        "/api/jobs", json={"engine": "local", "speaker_mode": "multi"}
    )

    assert response.status_code == 201
    assert len(storage.list_jobs()) == 1


async def test_single_mode_always_accepted(
    unsupported_client: AsyncClient,
    storage: FakeTranscriptStoragePort,
) -> None:
    """6.9 (triangulation): SINGLE mode never rejected."""
    response = await unsupported_client.post(
        "/api/jobs", json={"engine": "local", "speaker_mode": "single"}
    )

    assert response.status_code == 201
    assert len(storage.list_jobs()) == 1
