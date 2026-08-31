"""Walking-skeleton use case: extract, slice one chunk, transcribe, export.

Orchestrates the three synchronous worker-side ports. `MediaSourcePort` is
async and lives at the web-adapter boundary (design decision); this use case
receives an already-stored `SourceMedia`, not the upload stream itself.
"""

from pathlib import Path

from onevoicecut.domain.chunking import PlannedChunk
from onevoicecut.domain.ids import JobId
from onevoicecut.domain.jobs import EngineChoice, SpeakerMode
from onevoicecut.domain.media import SourceMedia
from onevoicecut.domain.transcript import Transcript, render_message_text
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.ports.transcription import TranscriptionPort, TranscriptionRequest


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
        # The transcript keeps every segment; only the export is narrowed to the
        # spoken message, so a musical range stays addressable for a clip.
        self._storage.save_transcript(transcript)
        self._storage.export_text(job_id, render_message_text(transcript))

        return transcript
