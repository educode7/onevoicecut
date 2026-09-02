"""The local engine's second capability axis, held against real samples.

`localmodel`-marked in full. The claim under test is what the real Silero VAD and
the real decoder do with real audio; faking either would test the fake.

The fixture is a sustained three-note chord — instrumental audio with no speech
anywhere in it. The interesting assertion is not that the engine stays quiet over
it, but that the musical range still comes back carrying its timestamps. An
adapter that merely dropped non-speech audio would satisfy "no fabricated SPEECH"
and still destroy every musical range an operator might cut a clip from, which is
why `speech-transcription` states classification and discarding as two separate
requirements rather than one.
"""

from pathlib import Path

import pytest

# Before the adapter import, and load-bearing for the same reason it is in the
# sibling modules: pytest imports every test module during collection, *before*
# it filters on markers, so a module-level `faster_whisper` import reached
# through this file would break the default run on a checkout without the
# optional local-ASR extras.
pytest.importorskip(
    "faster_whisper",
    reason="local ASR extras not installed (requirements-local-asr.txt)",
)

import subprocess  # noqa: E402 - must follow the guard above

from onevoicecut.adapters.asr.local.faster_whisper_adapter import (  # noqa: E402
    FasterWhisperTranscriber,
)
from onevoicecut.domain.chunking import AudioChunk  # noqa: E402
from onevoicecut.domain.ids import JobId  # noqa: E402
from onevoicecut.domain.jobs import SpeakerMode  # noqa: E402
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment  # noqa: E402
from onevoicecut.ports.capabilities import ClassificationSupport  # noqa: E402
from onevoicecut.ports.transcription import TranscriptionRequest  # noqa: E402

pytestmark = pytest.mark.localmodel

TEST_MODEL = "tiny"
CHORD_SECONDS = 4.0
JOB_ID = JobId("00000000000000000000000000")
# A 400 ms slack absorbs the VAD's own padding and the decoder's segment
# rounding. Anything larger than that is a range the adapter actually lost.
COVERAGE_SLACK_S = 0.4


@pytest.fixture
def adapter() -> FasterWhisperTranscriber:
    return FasterWhisperTranscriber(model_size=TEST_MODEL, device="cpu")


@pytest.fixture
def music_chunk(tmp_path: Path, ffmpeg_available: None) -> AudioChunk:
    """A sustained A-minor triad: instrumental, and unambiguously not speech."""
    path = tmp_path / "music.wav"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y",
            "-f", "lavfi",
            "-i",
            "aevalsrc="
            "0.3*sin(2*PI*220*t)+0.3*sin(2*PI*261.6*t)+0.3*sin(2*PI*329.6*t)"
            f":d={CHORD_SECONDS}",
            "-ar", "16000", "-ac", "1",
            str(path),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return AudioChunk(
        job_id=JOB_ID,
        index=0,
        path=path,
        start_s=120.0,  # non-zero on purpose: returned times must stay chunk-local
        end_s=120.0 + CHORD_SECONDS,
        size_bytes=path.stat().st_size,
    )


def _single_speaker() -> TranscriptionRequest:
    return TranscriptionRequest(
        language="es", speaker_mode=SpeakerMode.SINGLE, timeout_s=None
    )


def _covered_seconds(segments: tuple[TranscriptSegment, ...]) -> float:
    """Total audio the returned segments account for, overlaps counted once."""
    covered = 0.0
    cursor = 0.0
    for segment in sorted(segments, key=lambda s: s.start_s):
        start = max(segment.start_s, cursor)
        if segment.end_s > start:
            covered += segment.end_s - start
            cursor = segment.end_s
    return covered


def test_declares_it_can_tell_the_message_from_the_music(
    adapter: FasterWhisperTranscriber,
) -> None:
    """The declaration is what admission and every consumer downstream read.

    Slices 7a-i and 7a-ii shipped UNSUPPORTED deliberately, because no
    voice-activity filter was wired yet. Now that one is, the honest answer flips
    — and it must flip in `capabilities()`, not merely in behaviour, or the
    contract test keeps holding the adapter to the weaker promise.
    """
    caps = adapter.capabilities()

    assert caps.non_speech_classification is ClassificationSupport.AVAILABLE


def test_music_only_audio_never_comes_back_as_the_spoken_message(
    adapter: FasterWhisperTranscriber, music_chunk: AudioChunk
) -> None:
    """The failure this axis exists to stop, on the audio that provokes it.

    A Whisper decoder given music has no speech to condition on and falls back on
    its training prior — for Spanish, subtitle boilerplate nobody said. Emitting
    that is survivable; classifying it SPEECH is not, because the `.txt` export
    and the LLM windows both select on exactly that field.
    """
    segments = adapter.transcribe(music_chunk, _single_speaker())

    assert all(s.kind is not SegmentKind.SPEECH for s in segments)


def test_the_musical_passage_is_classified_as_music(
    adapter: FasterWhisperTranscriber, music_chunk: AudioChunk
) -> None:
    """`speech-transcription`: "Classifying adapter marks a musical passage"."""
    segments = adapter.transcribe(music_chunk, _single_speaker())

    assert segments, "a musical range must be reported, not silently swallowed"
    assert any(s.kind is SegmentKind.MUSIC for s in segments)


def test_classification_never_discards_the_musical_range(
    adapter: FasterWhisperTranscriber, music_chunk: AudioChunk
) -> None:
    """`speech-transcription`: "Classification never discards audio".

    The one a plain `vad_filter=True` would quietly fail. Filtering non-speech out
    of the decode is correct; letting it vanish from the result is not, because a
    musical range has to stay addressable in the source footage for slice 11's
    clip rendering to aim at it.
    """
    segments = adapter.transcribe(music_chunk, _single_speaker())

    assert min(s.start_s for s in segments) <= COVERAGE_SLACK_S
    assert max(s.end_s for s in segments) >= CHORD_SECONDS - COVERAGE_SLACK_S
    assert _covered_seconds(segments) >= CHORD_SECONDS - COVERAGE_SLACK_S
