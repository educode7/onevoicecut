"""Shared fake-construction helpers, reused across use-case tests."""

from dataclasses import dataclass
from pathlib import Path

from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.media_source import FakeMediaSourcePort
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.fakes.transcription import FakeTranscriptionPort
from transcribe.domain.ids import JobId, MediaId, make_job_id, make_media_id

DEFAULT_JOB_ID: JobId = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
DEFAULT_MEDIA_ID: MediaId = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")


@dataclass(frozen=True, slots=True)
class FakePorts:
    media_source: FakeMediaSourcePort
    audio_extractor: FakeAudioExtractorPort
    transcription: FakeTranscriptionPort
    storage: FakeTranscriptStoragePort


def build_fake_ports(root: Path, job_id: JobId = DEFAULT_JOB_ID) -> FakePorts:
    """Construct one fake per port, wired to the same job id and storage root."""
    return FakePorts(
        media_source=FakeMediaSourcePort(),
        audio_extractor=FakeAudioExtractorPort(job_id=job_id),
        transcription=FakeTranscriptionPort(),
        storage=FakeTranscriptStoragePort(root=root),
    )
