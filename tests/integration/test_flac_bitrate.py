"""What the pipeline's own normalization actually weighs, per second.

Every byte-cap decision in this system is arithmetic over one number: how many
bytes a second of normalized audio takes. Slice 2a wrote that number down as
`FLAC_BYTES_PER_SECOND = 16_000` with the comment "sits near this rate", and
nothing ever measured it. It is the input to the cloud engine's 25 MB cap, so a
guess that is wrong by a factor of five is a job that fails per chunk, three
hours in.

So this encodes through `adapters/ffmpeg/argv.py`'s own encoding flags — not a
copy of them — and measures. Using the real argv is what makes the measurement
about the pipeline rather than about ffmpeg: if normalization ever moves to a
different codec or sample rate, this fails here, at planning-time arithmetic,
rather than as a `ChunkTooLarge` on a live job.

Free and fast, so it runs in the default suite and skips cleanly without ffmpeg.
Noise is deliberate: FLAC is lossless, so incompressible input is the worst case
this pipeline can be handed, and a bound that holds there holds for speech.
"""

import subprocess
from pathlib import Path

import pytest

from onevoicecut.adapters.ffmpeg.argv import SAMPLE_RATE_HZ, _audio_encoding
from tests.unit.usecases.test_cloud_byte_cap import FLAC_CEILING_BYTES_PER_S

pytestmark = pytest.mark.integration

CLIP_SECONDS = 20

# Speech is far more compressible than either of these. Both are encoded so the
# assertion rests on the worst case rather than on a favourable one.
SOURCES = {
    "white noise": f"anoisesrc=d={CLIP_SECONDS}:c=white:a=0.9",
    "loud tone": f"sine=frequency=440:duration={CLIP_SECONDS}",
}


def _encode(dest: Path, source: str) -> int:
    """Normalize through the pipeline's own encoding flags and return the size."""
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", source,
            *_audio_encoding(),
            "-y", str(dest),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    return dest.stat().st_size


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_normalized_audio_stays_under_the_planners_assumed_rate(
    name: str, tmp_path: Path, ffmpeg_available: None
) -> None:
    """The measurement the byte-cap maths has been resting on since slice 2a."""
    size = _encode(tmp_path / "clip.flac", SOURCES[name])

    assert size / CLIP_SECONDS < FLAC_CEILING_BYTES_PER_S


def test_normalization_still_produces_the_format_the_rate_was_measured_for(
    tmp_path: Path, ffmpeg_available: None
) -> None:
    """The rate above is only meaningful for the format it was measured on.

    A change of codec or sample rate invalidates every byte-cap conclusion in
    the project, and would otherwise do so silently — the plan would still be
    produced, just against a bitrate that no longer describes anything.
    """
    _encode(dest := tmp_path / "clip.flac", SOURCES["loud tone"])

    probed = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_name,sample_rate,channels",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(dest),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout.split()

    assert probed == ["flac", str(SAMPLE_RATE_HZ), "1"]
