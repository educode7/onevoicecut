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
from onevoicecut.domain.transcript import SegmentKind
from onevoicecut.ports.capabilities import ClassificationSupport
from onevoicecut.ports.transcription import TranscriptionPort
from tests.contract.transcription import (
    CHUNK_START_S,
    TranscriptionPortContract,
    single_speaker,
)

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


class TestTheClassificationDeclarationIsEarned:
    """Why the declaration is UNSUPPORTED, not merely that it is.

    Deliberately not a subclass of the contract case above: inheriting it would
    re-run the whole contract body against this fixture and bill every one of
    those calls a second time to re-prove what the sine already proved.

    8a-i had to declare it and honour it in one unit — the shared contract body
    asserts the *relationship* between the two, so it could not pass otherwise.
    What a mock could never supply is the evidence that the declaration is the
    right one, and this is the only place that can ask the provider directly.

    The strict assertion is the invariant: whatever comes back is UNCERTAIN. The
    provider's own behaviour is *recorded* rather than asserted, deliberately —
    a test that demanded the API hallucinate over music would be pinning a
    provider defect, and would go red the day they fixed it. What must never
    change is our answer to it.
    """

    @pytest.fixture
    def port(self) -> TranscriptionPort:
        api_key = os.environ.get(CLOUD_API_KEY_ENV)
        if not api_key:
            pytest.skip(f"{CLOUD_API_KEY_ENV} is not set")
        return OpenAiWhisperTranscriber(api_key)

    @pytest.fixture
    def chunk(self, tmp_path: Path, ffmpeg_available: None) -> AudioChunk:
        """A chord over noise: the closest ffmpeg gets to the worship band.

        Every ASR fixture in this suite is synthesised, and no synthetic signal
        is a human singing — that gap is recorded in CLAUDE.md and is exactly
        what `scripts/try_local_asr.py` exists for. This is still the most
        provoking input available without committing media: harmonically dense,
        speech-free, and the shape that makes a Whisper-family decoder invent.
        """
        path = tmp_path / "chunk.wav"
        subprocess.run(
            [
                "ffmpeg", "-nostdin", "-y",
                "-f", "lavfi",
                "-i", f"sine=frequency=220:duration={TONE_SECONDS}",
                "-f", "lavfi",
                "-i", f"sine=frequency=277:duration={TONE_SECONDS}",
                "-f", "lavfi",
                "-i", f"anoisesrc=d={TONE_SECONDS}:c=pink:a=0.2",
                "-filter_complex", "amix=inputs=3:duration=shortest",
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

    def test_music_never_comes_back_marked_as_speech(
        self, port: TranscriptionPort, chunk: AudioChunk
    ) -> None:
        """The whole axis, against the live engine.

        `speech_segments` selects the LLM's window on this field, and
        `without_music` drops on it. A provider that returns confident text over
        a chord — which is the documented Whisper failure mode and this
        project's stated normal input — must not reach either of them wearing
        the label it did not earn.
        """
        segments = port.transcribe(chunk, single_speaker())

        assert all(s.kind is SegmentKind.UNCERTAIN for s in segments)

    def test_the_declaration_still_matches_what_the_provider_offers(
        self, port: TranscriptionPort
    ) -> None:
        """The claim that can go stale without anyone noticing.

        `UNSUPPORTED` is a statement about the provider, not about our code: it
        says this API exposes no voice-activity control, so we have established
        nothing about whether we heard the preacher or the band. If that ever
        becomes false — a VAD parameter, a segment-level content class — this
        adapter should be reclassifying rather than blanket-marking, and the
        declaration is where that decision gets made.

        Pinned here so flipping it is a deliberate act with a paid test behind
        it, rather than something inherited from 8a-i's constraints.
        """
        assert (
            port.capabilities().non_speech_classification
            is ClassificationSupport.UNSUPPORTED
        )
