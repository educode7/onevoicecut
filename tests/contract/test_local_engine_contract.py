"""The real local engine, under exactly the same contract as the fakes.

`localmodel`-marked in full: the adapter loads its CTranslate2 weights in the
constructor, so there is no reading `capabilities()` without a model on disk.
That is the deliberate trade — an absent model must fail at engine resolution,
before a multi-hour job starts, rather than on the first chunk hours in.

The `importorskip` above the adapter import is load-bearing and easy to mistake
for noise. pytest imports every test module during collection, *before* it
filters on markers, so a module-level `from faster_whisper import ...` reached
through this file would break the default run on any checkout that has not
installed the optional local-ASR dependencies — the exact suite that is supposed
to need none of them.
"""

from pathlib import Path

import pytest

pytest.importorskip(
    "faster_whisper",
    reason="local ASR extras not installed (requirements-local-asr.txt)",
)

import subprocess  # noqa: E402 - must follow the guard above

from onevoicecut.adapters.asr.local.faster_whisper_adapter import (  # noqa: E402
    FasterWhisperTranscriber,
)
from onevoicecut.domain.chunking import AudioChunk  # noqa: E402
from onevoicecut.domain.ids import make_job_id  # noqa: E402
from onevoicecut.ports.transcription import TranscriptionPort  # noqa: E402
from tests.contract.transcription import (  # noqa: E402
    CHUNK_START_S,
    TranscriptionPortContract,
)

pytestmark = pytest.mark.localmodel

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
# The smallest published model: every assertion here is about the contract,
# never about what the engine heard.
TEST_MODEL = "tiny"
TONE_SECONDS = 3.0


class TestFasterWhisperLocalEngine(TranscriptionPortContract):
    @pytest.fixture
    def port(self) -> TranscriptionPort:
        return FasterWhisperTranscriber(model_size=TEST_MODEL, device="cpu")

    @pytest.fixture
    def chunk(self, tmp_path: Path, ffmpeg_available: None) -> AudioChunk:
        """A 440 Hz sine, not speech — and that is the point.

        The engine is free to hallucinate over tonal audio, and the shared
        contract does not forbid that. What it forbids is calling the invention
        SPEECH, which is the assertion that actually protects the export and the
        LLM windows downstream — held against whatever `capabilities()` declares,
        so this body stayed correct when 7a-iii flipped that declaration.
        """
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
            start_s=CHUNK_START_S,
            end_s=CHUNK_START_S + TONE_SECONDS,
            size_bytes=path.stat().st_size,
        )

    def test_the_engine_id_names_the_model_that_produced_it(
        self, port: TranscriptionPort
    ) -> None:
        """Beyond the shared contract, which only requires a non-empty id.

        The size decides both transcript quality and hours of runtime, and it is
        persisted on every chunk result. An id that collapsed every size into
        "faster-whisper" would make a re-run's provenance unanswerable.
        """
        assert TEST_MODEL in port.capabilities().engine_id
