"""ffmpeg lives behind this port and nowhere else."""

from pathlib import Path
from typing import Protocol

from onevoicecut.domain.chunking import AudioChunk, PlannedChunk
from onevoicecut.domain.media import AudioTrack, MediaProbe, SourceMedia


class AudioExtractorPort(Protocol):
    def probe(self, media: SourceMedia) -> MediaProbe: ...

    def extract(self, media: SourceMedia, dest: Path) -> AudioTrack:
        """Normalizes to 16 kHz mono FLAC."""
        ...

    def slice(self, track: AudioTrack, planned: PlannedChunk, dest: Path) -> AudioChunk:
        """Raises ExtractionFailed, FfmpegUnavailable."""
        ...
