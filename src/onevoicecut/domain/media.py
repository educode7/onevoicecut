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
class FrameSize:
    """The picture as a viewer sees it, not as the container codes it.

    A phone filming vertical writes a landscape frame plus a rotation matrix, so
    the coded numbers and the displayed ones disagree. This is the displayed
    pair, because everything downstream — cropping toward a subject, choosing a
    vertical target — reasons about what is on screen.
    """

    width: int
    height: int


@dataclass(frozen=True, slots=True)
class MediaProbe:
    duration_s: float
    container: str
    has_audio: bool
    # `None` rather than a zero size, and rather than absent. A renderer must be
    # able to tell "there is no picture" — an audio-only upload, a container
    # carrying only cover art — from "the picture is nothing", which is a frame
    # it would divide by. Defaulted because every caller before rev 4 predates
    # the question.
    frame: FrameSize | None = None
