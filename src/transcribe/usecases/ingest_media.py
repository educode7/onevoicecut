"""Walking-skeleton use case: extract, slice one chunk, transcribe, export.

Orchestrates the three synchronous worker-side ports. `MediaSourcePort` is
async and lives at the web-adapter boundary (design decision); this use case
receives an already-stored `SourceMedia`, not the upload stream itself.
"""

from pathlib import Path

from transcribe.domain.chunking import PlannedChunk
from transcribe.domain.ids import JobId
from transcribe.domain.jobs import EngineChoice, SpeakerMode
from transcribe.domain.media import SourceMedia
from transcribe.domain.transcript import Transcript
from transcribe.ports.audio_extractor import AudioExtractorPort
from transcribe.ports.transcript_storage import TranscriptStoragePort
from transcribe.ports.transcription import TranscriptionPort, TranscriptionRequest


class IngestMedia:
    def __init__(
        self,
        audio_extractor: AudioExtractorPort,
        transcription: TranscriptionPort,
        storage: TranscriptStoragePort,
    ) -> None:
        self._audio_extractor = audio_extractor
        self._transcription = transcription
        self._storage = storage

    def run(
        self,
        job_id: JobId,
        source: SourceMedia,
        job_dir: Path,
        engine: EngineChoice,
        speaker_mode: SpeakerMode,
    ) -> Transcript:
        track = self._audio_extractor.extract(source, dest=job_dir / "audio.flac")
        planned = PlannedChunk(index=0, start_s=0.0, end_s=track.duration_s)
        chunk = self._audio_extractor.slice(
            track, planned, dest=job_dir / "chunks" / "0000.flac"
        )

        request = TranscriptionRequest(
            language="es", speaker_mode=speaker_mode, timeout_s=None
        )
        segments = self._transcription.transcribe(chunk, request)
        caps = self._transcription.capabilities()

        transcript = Transcript(
            job_id=job_id,
            segments=segments,
            engine_id=caps.engine_id,
            diarized=False,
        )
        self._storage.save_transcript(transcript)

        text = "\n".join(segment.text for segment in transcript.segments)
        self._storage.export_text(job_id, text)

        return transcript
