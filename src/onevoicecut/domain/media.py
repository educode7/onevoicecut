"""Source media and normalized audio track entities."""

from dataclasses import dataclass
from pathlib import Path

from onevoicecut.domain.ids import MediaId


@dataclass(frozen=True, slots=True)
class SourceMedia:
    media_id: MediaId
    original_filename: str
    stored_path: Path
    size_bytes: int
    container: str
    checksum: str


@dataclass(frozen=True, slots=True)
class AudioTrack:
    media_id: MediaId
    path: Path
    duration_s: float
    size_bytes: int
    sample_rate: int = 16000
    channels: int = 1
    codec: str = "flac"


@dataclass(frozen=True, slots=True)
class MediaProbe:
    duration_s: float
    container: str
    has_audio: bool
