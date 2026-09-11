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


def route_request_body(method: str, path: str) -> tuple[bytes | None, dict[str, object] | None]:
    """`(content, json)`, shaped enough to satisfy whichever pydantic body
    model (if any) a route declares, for the generated 401/403 checks in
    `test_auth_gate.py` and `test_mutation_ownership_matrix.py`.

    FastAPI resolves a declared body model before the handler's first line
    ever runs, so a request whose body fails that model's validation never
    reaches `_authorized`/`_owned` at all -- it is refused 422 first. A
    single body shared across every POST route only ever worked by
    coincidence: `{"engine": "local"}` happens to satisfy `AdmitJobRequest`,
    the one POST route that existed before `POST .../clips` needed a
    different shape. Keying on the route rather than the method is what
    keeps a future route with its own body model from silently reintroducing
    the same gap.
    """
    if method == "PUT":
        return b"x", None
    if method == "POST" and path.endswith("/clips"):
        return None, {"candidate_index": 0, "targets": ["tiktok"]}
    if method == "POST":
        return None, {"engine": "local"}
    return None, None



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
        ),
        storage,
    )
