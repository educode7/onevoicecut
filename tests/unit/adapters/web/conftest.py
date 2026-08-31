"""Shared web-adapter test wiring.

The upload route probes what it stored, so every test that uploads something now
needs an extractor. These fixtures supply a fake one that says "yes, media" —
whether ffprobe is right is proven against the real binary in the integration
tests, and against a configured fake in `test_upload_content_validation.py`.
"""

from pathlib import Path

from transcribe.adapters.web.app import WebDependencies
from transcribe.domain.ids import JobId
from transcribe.ports.audio_extractor import AudioExtractorPort
from transcribe.ports.transcript_storage import TranscriptStoragePort
from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort


def accepting_extractor(
    _: TranscriptStoragePort, job_id: JobId
) -> AudioExtractorPort:
    return FakeAudioExtractorPort(job_id)


def web_dependencies(
    root: Path, *, max_upload_bytes: int = 1024**2
) -> tuple[WebDependencies, FakeTranscriptStoragePort]:
    storage = FakeTranscriptStoragePort(root)
    return (
        WebDependencies(
            storage=storage,
            max_upload_bytes=max_upload_bytes,
            extractor_for=accepting_extractor,
        ),
        storage,
    )
