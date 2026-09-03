"""The real cloud engine, under exactly the same contract as the fakes.

`paid`-marked in full: every test in this module bills a real transcription
request. It is excluded from the default run, which is a success criterion of
this project rather than a preference — so the adapter's behaviour is proven
without a network next door, in `tests/unit/adapters/asr/cloud/`, and this
module exists to answer the one question a mock cannot: does the provider still
behave the way the adapter believes it does.

That is why the assertions here are the shared contract body and almost nothing
else. A test that also checked *what* the engine heard would fail on a change of
model rather than on a change of contract, and cost money to tell us so.

The key is read from the environment and the module skips without it. A `paid`
run on a checkout with no credentials should say "not configured", not fail.
"""

import os
import subprocess
from pathlib import Path

import pytest

from onevoicecut.adapters.asr.cloud.openai_whisper_adapter import (
    CLOUD_API_KEY_ENV,
    DEFAULT_MODEL,
    OpenAiWhisperTranscriber,
)
from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.domain.ids import make_job_id
from onevoicecut.ports.transcription import TranscriptionPort
from tests.contract.transcription import CHUNK_START_S, TranscriptionPortContract

pytestmark = pytest.mark.paid

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
TONE_SECONDS = 3.0


class TestOpenAiWhisperCloudEngine(TranscriptionPortContract):
    @pytest.fixture
    def port(self) -> TranscriptionPort:
        api_key = os.environ.get(CLOUD_API_KEY_ENV)
        if not api_key:
            pytest.skip(f"{CLOUD_API_KEY_ENV} is not set")
        return OpenAiWhisperTranscriber(api_key)

    @pytest.fixture
    def chunk(self, tmp_path: Path, ffmpeg_available: None) -> AudioChunk:
        """Three seconds of sine, the same fixture the local engine gets.

        Not speech, and deliberately so: the contract asserts the relationship
        between what an adapter declares and what it does, and tonal audio is
        where a non-classifying engine is most likely to invent something and
        call it speech. Keeping the fixture identical to the local one also
        keeps "identical assertions on the single-speaker path" honest — the
        two adapters are answering the same question, not two similar ones.
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

        The provider ships several transcription models with different
        capabilities and prices, and the id is persisted on every chunk result.
        One that collapsed them all into "openai" would make a re-run's
        provenance unanswerable.
        """
        assert DEFAULT_MODEL in port.capabilities().engine_id
