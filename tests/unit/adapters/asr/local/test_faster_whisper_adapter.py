"""Contract test for the real local ASR adapter.

`localmodel`-marked in full, including the capability assertions: the adapter
loads its CTranslate2 weights in the constructor, so there is no way to read
`capabilities()` without the model on disk. That is the deliberate trade — an
absent or unreadable model must fail at engine resolution, before a multi-hour
job starts, rather than on the first chunk hours in, which is the same reason
the resolver reads cloud secrets at construction.

The fixture is a 440 Hz sine, not speech. That is the point at this stage: slice
7a-i ships no voice-activity filter, so the engine is free to hallucinate over
tonal audio, and the invariant under test is that whatever it invents is never
promoted to `SPEECH`. Real speech and music fixtures arrive with classification
in 7a-iii and hallucination containment in 7a-iv.
"""

from pathlib import Path

import pytest

# Before the adapter import, and load-bearing: pytest imports every test module
# during collection, *before* it filters on markers. Without this guard the
# module-level `from faster_whisper import ...` inside the adapter breaks the
# default run on any checkout that has not installed the optional local-ASR
# extras — the suite that is specifically supposed to need none of them.
pytest.importorskip(
    "faster_whisper",
    reason="local ASR extras not installed (requirements-local-asr.txt)",
)

import subprocess  # noqa: E402 - must follow the guard above

from onevoicecut.adapters.asr.local.faster_whisper_adapter import (  # noqa: E402
    FasterWhisperTranscriber,
)
from onevoicecut.domain.chunking import AudioChunk  # noqa: E402
from onevoicecut.domain.errors import DiarizationUnsupported  # noqa: E402
from onevoicecut.domain.ids import JobId  # noqa: E402
from onevoicecut.domain.jobs import SpeakerMode  # noqa: E402
from onevoicecut.domain.transcript import SegmentKind  # noqa: E402
from onevoicecut.ports.capabilities import ClassificationSupport, DiarizationSupport  # noqa: E402
from onevoicecut.ports.transcription import TranscriptionPort, TranscriptionRequest  # noqa: E402

pytestmark = pytest.mark.localmodel

# The smallest published model. Accuracy is irrelevant here — every assertion in
# this module is about the contract, never about what the engine heard.
TEST_MODEL = "tiny"
TONE_SECONDS = 3.0
JOB_ID = JobId("00000000000000000000000000")


@pytest.fixture
def adapter() -> FasterWhisperTranscriber:
    return FasterWhisperTranscriber(model_size=TEST_MODEL, device="cpu")


@pytest.fixture
def tone_chunk(tmp_path: Path, ffmpeg_available: None) -> AudioChunk:
    path = tmp_path / "chunk.wav"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={TONE_SECONDS}",
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
        start_s=120.0,  # non-zero on purpose: returned times must NOT be absolute
        end_s=120.0 + TONE_SECONDS,
        size_bytes=path.stat().st_size,
    )


def _single_speaker() -> TranscriptionRequest:
    return TranscriptionRequest(
        language="es", speaker_mode=SpeakerMode.SINGLE, timeout_s=None
    )


def test_declares_capabilities_naming_the_model_that_produced_them(
    adapter: FasterWhisperTranscriber,
) -> None:
    caps = adapter.capabilities()

    # The model size is part of the identity, not adapter trivia: `engine_id` is
    # persisted on every ChunkResult, and "which model produced this transcript"
    # is unanswerable afterwards if the id collapses every size into one name.
    assert TEST_MODEL in caps.engine_id

    # Diarization lands in slice 9a. Until then the adapter must say it cannot,
    # so a speaker-mode job is refused instead of silently unlabelled.
    assert caps.diarization is DiarizationSupport.UNSUPPORTED

    # The voice-activity filter lands in 7a-iii. Declaring AVAILABLE before it
    # exists is exactly the silent degradation the capability axis exists to stop.
    assert caps.non_speech_classification is ClassificationSupport.UNSUPPORTED

    # A local engine imposes neither cap; it is bounded only by the machine, and
    # the planner already bounds stride by `settings.target_chunk_seconds`.
    assert caps.max_chunk_bytes is None
    assert caps.max_chunk_duration_s is None


def test_returns_chunk_local_times(
    adapter: FasterWhisperTranscriber, tone_chunk: AudioChunk
) -> None:
    segments = adapter.transcribe(tone_chunk, _single_speaker())

    # The chunk starts at 120 s absolute. A segment at or past that value proves
    # the adapter leaked absolute time across the port boundary.
    for segment in segments:
        assert 0.0 <= segment.start_s <= segment.end_s <= TONE_SECONDS


def test_never_promotes_unverified_audio_to_speech(
    adapter: FasterWhisperTranscriber, tone_chunk: AudioChunk
) -> None:
    segments = adapter.transcribe(tone_chunk, _single_speaker())

    # It may hallucinate over a sine tone — with no VAD yet, that is expected.
    # What it may never do is assert the invention is the spoken message.
    assert all(s.kind is SegmentKind.UNCERTAIN for s in segments)


def test_refuses_a_speaker_mode_job(
    adapter: FasterWhisperTranscriber, tone_chunk: AudioChunk
) -> None:
    request = TranscriptionRequest(
        language="es", speaker_mode=SpeakerMode.MULTI, timeout_s=None
    )

    with pytest.raises(DiarizationUnsupported):
        adapter.transcribe(tone_chunk, request)


def test_conforms_to_the_transcription_port(
    adapter: FasterWhisperTranscriber,
) -> None:
    port: TranscriptionPort = adapter

    assert port.capabilities().engine_id
