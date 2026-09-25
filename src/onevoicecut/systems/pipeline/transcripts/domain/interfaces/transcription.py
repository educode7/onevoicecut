"""The provider-neutral ASR contract."""

from dataclasses import dataclass
from typing import Protocol

from onevoicecut.systems.pipeline.transcripts.domain.chunking import AudioChunk
from onevoicecut.shared.domain.speaker import SpeakerMode
from onevoicecut.systems.pipeline.transcripts.domain.transcript import TranscriptSegment
from onevoicecut.shared.domain.capabilities import TranscriptionCapabilities


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    language: str
    speaker_mode: SpeakerMode
    timeout_s: float | None  # honoured in-call where possible; watchdog otherwise


class TranscriptionPort(Protocol):
    def capabilities(self) -> TranscriptionCapabilities: ...

    def transcribe(
        self, chunk: AudioChunk, request: TranscriptionRequest
    ) -> tuple[TranscriptSegment, ...]:
        """INVARIANT: returned times are CHUNK-LOCAL.

        Raises TranscriptionFailed, ChunkTooLarge, DiarizationUnsupported.
        """
        ...
