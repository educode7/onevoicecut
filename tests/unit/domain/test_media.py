from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from transcribe.domain.ids import make_media_id
from transcribe.domain.media import AudioTrack, MediaProbe, SourceMedia

MEDIA_ID = make_media_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")


def test_source_media_holds_fields() -> None:
    media = SourceMedia(
        media_id=MEDIA_ID,
        original_filename="clip.mp4",
        stored_path=Path("jobs/x/source.mp4"),
        size_bytes=1024,
        container="mp4",
        checksum="deadbeef",
    )
    assert media.original_filename == "clip.mp4"
    assert media.size_bytes == 1024


def test_source_media_is_frozen() -> None:
    media = SourceMedia(
        media_id=MEDIA_ID,
        original_filename="clip.mp4",
        stored_path=Path("jobs/x/source.mp4"),
        size_bytes=1024,
        container="mp4",
        checksum="deadbeef",
    )
    with pytest.raises(FrozenInstanceError):
        media.size_bytes = 2048  # type: ignore[misc]


def test_audio_track_defaults() -> None:
    track = AudioTrack(
        media_id=MEDIA_ID,
        path=Path("jobs/x/audio.flac"),
        duration_s=120.5,
        size_bytes=2048,
    )
    assert track.sample_rate == 16000
    assert track.channels == 1
    assert track.codec == "flac"


def test_audio_track_is_frozen() -> None:
    track = AudioTrack(
        media_id=MEDIA_ID,
        path=Path("jobs/x/audio.flac"),
        duration_s=120.5,
        size_bytes=2048,
    )
    with pytest.raises(FrozenInstanceError):
        track.duration_s = 1.0  # type: ignore[misc]


def test_media_probe_holds_fields() -> None:
    probe = MediaProbe(duration_s=90.0, container="mp4", has_audio=True)
    assert probe.duration_s == 90.0
    assert probe.has_audio is True


def test_media_probe_is_frozen() -> None:
    probe = MediaProbe(duration_s=90.0, container="mp4", has_audio=True)
    with pytest.raises(FrozenInstanceError):
        probe.has_audio = False  # type: ignore[misc]
