"""Fake conforming to TranscriptionPort — fixed segments, fail-closed diarization."""

from transcribe.domain.chunking import AudioChunk
from transcribe.domain.errors import DiarizationUnsupported
from transcribe.domain.jobs import SpeakerMode
from transcribe.domain.transcript import SegmentKind, TranscriptSegment
from transcribe.ports.capabilities import (
    ClassificationSupport,
    DiarizationSupport,
    TranscriptionCapabilities,
)
from transcribe.ports.transcription import TranscriptionRequest


class FakeTranscriptionPort:
    def capabilities(self) -> TranscriptionCapabilities:
        return TranscriptionCapabilities(
            engine_id="fake-asr",
            diarization=DiarizationSupport.UNSUPPORTED,
            non_speech_classification=ClassificationSupport.AVAILABLE,
            max_chunk_bytes=None,
            max_chunk_duration_s=None,
        )

    def transcribe(
        self, chunk: AudioChunk, request: TranscriptionRequest
    ) -> tuple[TranscriptSegment, ...]:
        if request.speaker_mode is SpeakerMode.MULTI:
            raise DiarizationUnsupported("fake-asr cannot satisfy speaker_mode=multi")
        return (
            TranscriptSegment(
                start_s=0.0,
                end_s=chunk.end_s - chunk.start_s,
                text="hola mundo",
                speaker=None,
                confidence=0.99,
                kind=SegmentKind.SPEECH,
            ),
        )
