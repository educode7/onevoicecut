"""The provider-neutral ASR contract."""

from dataclasses import dataclass
from typing import Protocol

from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.systems.pipeline.jobs.domain.jobs import SpeakerMode
from onevoicecut.domain.transcript import TranscriptSegment
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
