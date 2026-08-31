"""Shared web-adapter test wiring.

The upload route probes what it stored, so every test that uploads something now
needs an extractor. These fixtures supply a fake one that says "yes, media" —
whether ffprobe is right is proven against the real binary in the integration
tests, and against a configured fake in `test_upload_content_validation.py`.
"""

from pathlib import Path

from onevoicecut.adapters.web.app import WebDependencies
from onevoicecut.domain.ids import JobId
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort


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
            max_upload_bytes=max_upload_bytes,
            extractor_for=accepting_extractor,
            start_job=unstarted,
        ),
        storage,
    )
