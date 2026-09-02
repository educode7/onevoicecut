"""Loading the weights is not proof the engine can run.

The adapter's constructor exists to make a missing resource an error *before* a
multi-hour job starts. It did not deliver that. `WhisperModel(...)` allocates the
model on the selected device and returns happily; the compute libraries it needs
are resolved lazily, on the first `encode()`. So on a machine with a GPU but no
usable cuBLAS — a stock Windows install with `nvidia-smi` working and the CUDA
toolkit absent, which is the ordinary state of a machine nobody set up for this —
`device="auto"` picks CUDA, construction succeeds, and the job dies on the first
chunk that contains speech.

That failure is worse than it sounds, because it is content-dependent. A chunk
the voice-activity filter finds nothing in never reaches the encoder, so it
"succeeds". A job over music completes; the same job over a sermon dies. Which
one an operator sees first is luck.

No weights are loaded here. `WhisperModel` is replaced with a stub, so this runs
in the default suite: the invariant is about what the constructor *does*, not
about what any particular engine says.
"""

import pytest

pytest.importorskip(
    "faster_whisper",
    reason="local ASR extras not installed (requirements-local-asr.txt)",
)

from typing import Any  # noqa: E402

from onevoicecut.adapters.asr.local import faster_whisper_adapter as adapter  # noqa: E402
from onevoicecut.adapters.asr.local.faster_whisper_adapter import (  # noqa: E402
    FasterWhisperTranscriber,
)
from onevoicecut.domain.errors import EngineUnavailable  # noqa: E402

CUBLAS_FAILURE = "Library cublas64_12.dll is not found or cannot be loaded"


class StubModel:
    """Constructs like the real thing. Whether it can compute is a second question."""

    def __init__(self, *args: Any, computes: bool = True, **kwargs: Any) -> None:
        self.transcribe_calls = 0
        self._computes = computes

    def transcribe(self, audio: Any, **kwargs: Any) -> tuple[Any, Any]:
        self.transcribe_calls += 1
        if not self._computes:
            # Exactly where a broken CUDA install surfaces: not at load, but when
            # the encoder first asks the device to do arithmetic.
            raise RuntimeError(CUBLAS_FAILURE)
        return iter(()), None


def _install(monkeypatch: pytest.MonkeyPatch, *, computes: bool) -> list[StubModel]:
    built: list[StubModel] = []

    def factory(*args: Any, **kwargs: Any) -> StubModel:
        model = StubModel(computes=computes)
        built.append(model)
        return model

    monkeypatch.setattr(adapter, "WhisperModel", factory)
    return built


def test_construction_proves_the_device_can_compute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One tiny decode, at construction, so the failure lands where it is cheap."""
    built = _install(monkeypatch, computes=True)

    FasterWhisperTranscriber(model_size="small", device="cuda")

    assert built[0].transcribe_calls == 1


def test_a_device_that_cannot_compute_fails_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`EngineUnavailable`, which is the resolver's error, not the chunk's.

    Raised here it reaches the operator before the job is claimed. Raised on the
    first chunk it becomes `TranscriptionFailed`, gets retried three times,
    fails the chunk, and reads as a problem with the audio.
    """
    _install(monkeypatch, computes=False)

    with pytest.raises(EngineUnavailable):
        FasterWhisperTranscriber(model_size="small", device="cuda")


def test_the_refusal_names_the_device_and_the_way_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An operator reading this needs to know it is the device, not the model,
    and that there is a setting that changes it. Falling back to CPU silently
    would be the wrong kindness: it is the same job twenty times slower, and
    nobody chose it."""
    _install(monkeypatch, computes=False)

    with pytest.raises(EngineUnavailable) as refusal:
        FasterWhisperTranscriber(model_size="small", device="cuda")

    message = str(refusal.value)
    assert "cuda" in message
    assert adapter.LOCAL_DEVICE_ENV in message
    assert CUBLAS_FAILURE in message


def test_the_proof_never_becomes_a_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The warm-up decodes silence the caller never asked for. Its output must
    not reach anybody — it is an assertion about the device, not audio."""
    built = _install(monkeypatch, computes=True)

    transcriber = FasterWhisperTranscriber(model_size="small", device="cpu")

    assert built[0].transcribe_calls == 1
    assert transcriber.capabilities().engine_id
