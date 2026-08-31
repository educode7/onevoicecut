"""The no-silent-degradation invariant, on the classification axis.

An adapter that cannot tell speech from music must say so — by returning
UNCERTAIN — rather than asserting SPEECH on the basis of never having checked.
Asserting "this is the message" without checking produces a transcript
indistinguishable from a correct one, which is the failure this invariant exists
to prevent.
"""

from pathlib import Path

from tests.fakes.transcription import (
    FakeTranscriptionPort,
    NonClassifyingFakeTranscriptionPort,
)
from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.domain.ids import make_job_id
from onevoicecut.domain.jobs import SpeakerMode
from onevoicecut.domain.transcript import SegmentKind
from onevoicecut.ports.capabilities import ClassificationSupport
from onevoicecut.ports.transcription import TranscriptionRequest

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
CHUNK = AudioChunk(
    job_id=JOB_ID,
    index=0,
    path=Path("chunks/0000.flac"),
    start_s=0.0,
    end_s=10.0,
    size_bytes=1024,
)
REQUEST = TranscriptionRequest(
    language="es", speaker_mode=SpeakerMode.SINGLE, timeout_s=None
)


def test_non_classifying_adapter_declares_unsupported() -> None:
    caps = NonClassifyingFakeTranscriptionPort().capabilities()
    assert caps.non_speech_classification is ClassificationSupport.UNSUPPORTED


def test_non_classifying_adapter_never_returns_speech() -> None:
    segments = NonClassifyingFakeTranscriptionPort().transcribe(CHUNK, REQUEST)
    assert segments
    assert all(s.kind is SegmentKind.UNCERTAIN for s in segments)
    assert not any(s.kind is SegmentKind.SPEECH for s in segments)


def test_classifying_adapter_declares_available_and_marks_speech() -> None:
    port = FakeTranscriptionPort()
    assert (
        port.capabilities().non_speech_classification is ClassificationSupport.AVAILABLE
    )
    assert all(s.kind is SegmentKind.SPEECH for s in port.transcribe(CHUNK, REQUEST))


def test_classifying_adapter_marks_music_without_dropping_it() -> None:
    """Classification never discards audio — the musical range stays addressable."""
    port = FakeTranscriptionPort(
        script=(
            ("el mensaje", SegmentKind.SPEECH),
            ("la la la", SegmentKind.MUSIC),
        )
    )
    segments = port.transcribe(CHUNK, REQUEST)
    assert [s.kind for s in segments] == [SegmentKind.SPEECH, SegmentKind.MUSIC]
    assert all(s.end_s > s.start_s for s in segments)
