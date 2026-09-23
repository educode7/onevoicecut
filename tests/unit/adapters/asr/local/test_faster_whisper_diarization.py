"""The diarizing call, end to end, against the real gated weights.

One test, `localmodel`-marked, and deliberately the only one: everything that
can be decided from data is proven in `test_diarization.py` with an injected
loader, and what is left is the single claim only the real pipeline can make —
that a speaker-mode chunk goes in and namespaced labels come back. Building the
pipeline downloads and loads gated weights, which is exactly the cost 9a-i
refused to put in `capabilities()`; here a test that asked for speakers pays it.

The fixture is two Windows SAPI voices alternating, not an ffmpeg signal. That
is a first for this suite and a forced move: every ffmpeg lavfi source is a
tone or noise, pyannote's segmentation model is trained on human speech, and a
sine comes back as an empty annotation — a fixture that found zero speakers
would prove nothing about the positive path. SAPI is the only human-voice source
on this machine with no new dependency. The test skips where it is absent.

The assertions are the contract, never the ground truth. Two synthesized voices
may cluster as two speakers or as one — the clustering threshold is not this
slice's to tune, and cross-chunk identity is slice 9b's `SpeakerResolver`. What
must hold whatever the pipeline heard: labels exist, they are namespaced with
the chunk's own index, and every decoded sentence knows whose it is.
"""

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

# Before the adapter import, and load-bearing: pytest imports every test module
# during collection, before it filters on markers.
pytest.importorskip(
    "faster_whisper",
    reason="local ASR extras not installed (requirements-local-asr.txt)",
)

from dotenv import dotenv_values  # noqa: E402 - must follow the guard above

from onevoicecut.adapters.asr.local.declarations import (  # noqa: E402
    HF_TOKEN_ENV,
    is_installed,
)
from onevoicecut.adapters.asr.local.faster_whisper_adapter import (  # noqa: E402
    FasterWhisperTranscriber,
)
from onevoicecut.domain.chunking import AudioChunk  # noqa: E402
from onevoicecut.domain.ids import JobId  # noqa: E402
from onevoicecut.domain.jobs import SpeakerMode  # noqa: E402
from onevoicecut.ports.capabilities import DiarizationSupport  # noqa: E402
from onevoicecut.ports.transcription import TranscriptionRequest  # noqa: E402

# The install probe is `find_spec`-based, so this costs no torch import on a
# checkout without the extras — it skips them instead, at collection.
if not is_installed():
    pytest.skip(
        "diarization extras not installed (requirements-diarization.txt)",
        allow_module_level=True,
    )

pytestmark = pytest.mark.localmodel

JOB_ID = JobId("00000000000000000000000000")
TEST_MODEL = "tiny"
# Non-zero on purpose, twice over: returned times must be chunk-local, and the
# labels must carry *this* index — `c03/` is a claim `c00/` cannot fake.
CHUNK_INDEX = 3
CHUNK_START_S = 120.0
# The fixture runs ~27 s; the window only has to contain it.
FIXTURE_WINDOW_S = 60.0

# tests/unit/adapters/asr/local/ -> five levels up is the repository root,
# which is where the gitignored `.env` lives.
REPO_ROOT = Path(__file__).resolve().parents[5]

# Two voices alternating, Spanish first: the source language of every real job.
# Which two voices is the machine's choice — it takes any enabled pair, prefers
# a Spanish one for the preacher, and falls back to one voice twice, which the
# contract assertions below all still honour.
_SAPI_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voices = @($synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo })
if ($voices.Count -eq 0) { exit 2 }
$spanish = @($voices | Where-Object { $_.Culture.Name -like 'es*' })
$first = if ($spanish.Count -gt 0) { $spanish[0].Name } else { $voices[0].Name }
$others = @($voices | Where-Object { $_.Name -ne $first })
$second = if ($others.Count -gt 0) { $others[0].Name } else { $first }
$synth.SetOutputToWaveFile("__DEST__")
$synth.SelectVoice($first)
$synth.Speak("Buenos dias a todos los hermanos presentes en este lugar. Es un privilegio poder compartir con ustedes esta manana las palabras de vida y de esperanza que encontramos en las escrituras sagradas.")
$synth.SelectVoice($second)
$synth.Speak("Good morning to everyone joining us today. We are very happy to have you here for this special time of learning and reflection together with our community of friends and family.")
$synth.SelectVoice($first)
$synth.Speak("El mensaje de hoy nos habla sobre la importancia de la fe en los momentos dificiles de nuestra vida cotidiana.")
$synth.Dispose()
"""


def _synthesize_two_voices(dest: Path) -> None:
    """A real two-voice WAV via Windows SAPI, or an honest skip."""
    if sys.platform != "win32":
        pytest.skip("the two-voice fixture needs Windows SAPI; ffmpeg has no human voice")
    completed = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            _SAPI_SCRIPT.replace("__DEST__", str(dest)),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    if completed.returncode != 0 or not dest.exists():
        pytest.skip(
            "this machine cannot synthesize the two-voice fixture: "
            f"{completed.stderr.strip()[:200]}"
        )


def _normalize_to_chunk(source: Path, dest: Path) -> AudioChunk:
    """Through the same 16 kHz mono normalization a real chunk gets."""
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y",
            "-i", str(source),
            "-ar", "16000", "-ac", "1",
            str(dest),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    return AudioChunk(
        job_id=JOB_ID,
        index=CHUNK_INDEX,
        path=dest,
        start_s=CHUNK_START_S,
        end_s=CHUNK_START_S + FIXTURE_WINDOW_S,
        size_bytes=dest.stat().st_size,
    )


def _token() -> str:
    """The operator's licence token, from the environment or the gitignored
    `.env` — never logged, never asserted on, never committed."""
    token = os.environ.get(HF_TOKEN_ENV)
    if token is None:
        token = dotenv_values(REPO_ROOT / ".env").get(HF_TOKEN_ENV)
    if token is None or not token.strip():
        pytest.skip(f"{HF_TOKEN_ENV} is not configured; the gated weights refuse without it")
    return token


def test_a_multi_speaker_job_comes_back_labelled(
    tmp_path: Path, ffmpeg_available: None
) -> None:
    raw = tmp_path / "two_voice_raw.wav"
    _synthesize_two_voices(raw)
    chunk = _normalize_to_chunk(raw, tmp_path / "chunk.wav")

    adapter = FasterWhisperTranscriber(
        model_size=TEST_MODEL, device="cpu", hf_token=_token()
    )

    # The AVAILABLE branch 9a-i could only prove as arithmetic: package
    # installed and token present, read off the real adapter.
    assert adapter.capabilities().diarization is DiarizationSupport.AVAILABLE

    segments = adapter.transcribe(
        chunk,
        TranscriptionRequest(
            language="es", speaker_mode=SpeakerMode.MULTI, timeout_s=None
        ),
    )

    labelled = tuple(s for s in segments if s.speaker is not None)
    # The diarization really ran: an empty label set would mean the pipeline
    # heard no speech at all in a fixture that is nothing but speech.
    assert labelled, "no segment came back with a speaker label"

    namespaced = re.compile(rf"c{CHUNK_INDEX:02d}/S\d{{2}}")
    assert all(namespaced.fullmatch(s.speaker or "") for s in labelled)

    # Every decoded sentence knows whose it is. This is the spec's "a speaker
    # label per segment", read honestly: a segment is the unit a reader
    # attributes, and the unlabelled remainder is filtered non-speech — a
    # musical range with a preacher's name on it would be fabrication.
    decoded = tuple(s for s in segments if s.text)
    assert decoded, "the engine decoded no text at all from a speech-only fixture"
    assert all(s.speaker is not None for s in decoded), [
        (s.start_s, s.end_s, s.kind) for s in decoded if s.speaker is None
    ]
