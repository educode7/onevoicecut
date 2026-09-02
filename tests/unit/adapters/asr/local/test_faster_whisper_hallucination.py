"""What the local engine invents over music, and what is allowed to survive it.

`localmodel`-marked in full. Every number in this module was measured against
`tiny` on this fixture rather than assumed, because the whole question is what a
real decoder does when there is no speech to condition on.

The fixture matters more than usual. A plain tone, a chord, pink noise and pure
silence all fail to provoke anything — the voice-activity pass rejects them and
the decoder never runs, so a containment test built on one of those would pass
while proving nothing. This one is harmonically rich, echoed and formant-shaped:
it gets *past* the voice-activity pass, the decoder does run on it, and it
reliably produces the degenerate repetition loop this slice exists to contain.

Two separate promises are asserted below, and only the first is about SPEECH:

1. The invention is never classified `SPEECH`. Measured `no_speech_prob` across
   30 variants of this fixture ran 0.68–0.86, comfortably above the 0.6 the
   adapter maps on, so this holds by the engine's own admission.
2. The invention never reaches the transcript as text. This one does *not* hold
   by itself — `compression_ratio` measured 1.00–2.33 on those same loops, never
   once crossing Whisper's 2.4 threshold, so the guard nominally responsible for
   breaking repetition loops catches none of them.
"""

from pathlib import Path

import pytest

# Before the adapter import, and load-bearing for the same reason as its
# siblings: pytest imports every test module during collection, before it filters
# on markers, so a module-level `faster_whisper` import reached through here
# would break the default run on a checkout without the local-ASR extras.
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
from onevoicecut.domain.transcript import SegmentKind  # noqa: E402
from onevoicecut.ports.transcription import TranscriptionRequest  # noqa: E402

pytestmark = pytest.mark.localmodel

TEST_MODEL = "tiny"
MUSIC_SECONDS = 10.0
JOB_ID = JobId("00000000000000000000000000")

# Ten harmonics of a vibratoed 110 Hz fundamental, echoed and shaped around a
# 700 Hz formant. Measured, not guessed: this is the variant out of the sweep
# that most reliably drives `tiny` into the loop.
_FUNDAMENTAL_HZ = 110
_HARMONICS = "+".join(
    f"{0.5 / (k + 1):.3f}*sin(2*PI*{k + 1}*({_FUNDAMENTAL_HZ}+8*sin(2*PI*5.5*t))*t)"
    for k in range(10)
)


@pytest.fixture
def adapter() -> FasterWhisperTranscriber:
    return FasterWhisperTranscriber(model_size=TEST_MODEL, device="cpu")


@pytest.fixture
def provocative_music_chunk(tmp_path: Path, ffmpeg_available: None) -> AudioChunk:
    """Instrumental audio the decoder mistakes for something worth writing down."""
    path = tmp_path / "music.wav"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y",
            "-f", "lavfi",
            "-i", f"aevalsrc=0.35*({_HARMONICS}):d={MUSIC_SECONDS}:s=16000",
            "-af", "aecho=0.8:0.9:60:0.4,equalizer=f=700:width_type=h:width=400:g=10",
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
        start_s=120.0,
        end_s=120.0 + MUSIC_SECONDS,
        size_bytes=path.stat().st_size,
    )


def _single_speaker() -> TranscriptionRequest:
    return TranscriptionRequest(
        language="es", speaker_mode=SpeakerMode.SINGLE, timeout_s=None
    )


def test_the_fixture_still_provokes_the_decoder(
    adapter: FasterWhisperTranscriber, provocative_music_chunk: AudioChunk
) -> None:
    """Guards the two tests below against passing for the wrong reason.

    A containment assertion over audio that produces nothing is vacuous, and it
    would stay green forever after a model or VAD upgrade quietly stopped
    provoking the failure. If this test starts failing, the fixture has gone
    inert and the ones below have stopped proving anything — retune it rather
    than deleting it.
    """
    segments = adapter.transcribe(provocative_music_chunk, _single_speaker())

    # Voice activity was found here, which is exactly why the decoder ran at all.
    # Ranges the VAD rejects come back MUSIC; this one must not.
    assert any(s.kind is SegmentKind.UNCERTAIN for s in segments)


def test_music_never_comes_back_as_confirmed_message(
    adapter: FasterWhisperTranscriber, provocative_music_chunk: AudioChunk
) -> None:
    """`speech-transcription`: "Music-only audio produces no invented message text"."""
    segments = adapter.transcribe(provocative_music_chunk, _single_speaker())

    assert all(s.kind is not SegmentKind.SPEECH for s in segments)


def test_the_repetition_loop_never_reaches_the_transcript(
    adapter: FasterWhisperTranscriber, provocative_music_chunk: AudioChunk
) -> None:
    """The half `no_speech_prob` alone does not cover.

    Classifying the invention UNCERTAIN keeps it out of the LLM windows, but the
    `.txt` export deliberately keeps UNCERTAIN text and marks it — so "No, no,
    no, no, no, no, no, no." still lands in the delivered artifact as
    `[?] No, no, no...`. Over three hours of worship music that is pages of
    fabricated text with the operator's transcript underneath it.

    The range itself must survive: it is still addressable footage. Only the
    invented words go.
    """
    segments = adapter.transcribe(provocative_music_chunk, _single_speaker())

    assert segments, "the musical range must still be reported"
    for segment in segments:
        words = segment.text.lower().replace(",", " ").replace(".", " ").split()
        assert not (
            len(words) >= 4 and len(set(words)) <= 2
        ), f"a degenerate decoder loop reached the transcript: {segment.text!r}"
