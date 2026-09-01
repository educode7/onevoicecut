"""Shared web-adapter test wiring.

The upload route probes what it stored, so every test that uploads something now
needs an extractor. These fixtures supply a fake one that says "yes, media" —
whether ffprobe is right is proven against the real binary in the integration
tests, and against a configured fake in `test_upload_content_validation.py`.

Authentication is deny-by-default: `WebDependencies` cannot be built without an
authenticator, so every app in the suite gets this fake one, and every client
sends a default operator's token unless a test says otherwise.
"""

from pathlib import Path

from onevoicecut.adapters.web.app import WebDependencies
from onevoicecut.adapters.web.auth import build_authenticator
from onevoicecut.domain.ids import JobId, make_operator_id
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

OPERATOR_A = make_operator_id("operator-a")
OPERATOR_B = make_operator_id("operator-b")
TOKEN_A = "test-token-for-operator-a"
TOKEN_B = "test-token-for-operator-b"

fake_authenticate = build_authenticator({OPERATOR_A: TOKEN_A, OPERATOR_B: TOKEN_B})


def auth_headers(token: str = TOKEN_A) -> dict[str, str]:
    """The default operator's token; a test authenticating as somebody else
    passes their token explicitly."""
    return {"authorization": f"Bearer {token}"}


def accepting_extractor(
    _: TranscriptStoragePort, job_id: JobId
) -> AudioExtractorPort:
    return FakeAudioExtractorPort(job_id)


def unstarted(job_id: JobId) -> None:
    """A starter that does nothing, for tests about everything except starting.

    Needed explicitly because the production default refuses: an app that
    accepted uploads and never transcribed them would report success at every
    step. Tests that care about the handoff record it instead — see
    `test_job_start.py`.
    """


def web_dependencies(
    root: Path, *, max_upload_bytes: int = 1024**2
) -> tuple[WebDependencies, FakeTranscriptStoragePort]:
    storage = FakeTranscriptStoragePort(root)
    return (
        WebDependencies(
            storage=storage,
            authenticate=fake_authenticate,
            max_upload_bytes=max_upload_bytes,
            extractor_for=accepting_extractor,
            start_job=unstarted,
        ),
        storage,
    )
