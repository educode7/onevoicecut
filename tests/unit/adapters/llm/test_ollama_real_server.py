"""The real Ollama server, answering the one question a mock cannot.

Everything about the adapter's shape is proven in `test_ollama_generator.py`
against `httpx.MockTransport`; what is left is whether the live service still
speaks the protocol those mocks were written from. `localmodel`-marked like the
real-weight ASR and vision tests — local compute, nothing billed, excluded from
the default run.

The guard lives in the fixture, never at module level: collection imports
nothing heavier than `httpx` and touches no server, so the default suite
collects this module without Ollama running. The skip is honest in both
directions the probe already unifies — an unreachable server and an unpulled
model are the same `False`, and the skip message says exactly that.

Cold model load measures ~84 s on this machine and warm generation ~5 tok/s,
so the timeout is 600 s and the request is deliberately tiny: `num_predict`
32 over a one-line prompt. A test that asked for a paragraph would spend
minutes of CPU to prove the same thing.
"""

import os
from collections.abc import Iterator

import httpx
import pytest

from onevoicecut.adapters.llm.ollama_generator import (
    DEFAULT_BASE_URL,
    OllamaTextGenerator,
)
from onevoicecut.adapters.llm.probe import model_is_pulled
from onevoicecut.domain.errors import GenerationFailed

pytestmark = pytest.mark.localmodel

FALLBACK_MODEL = "qwen2.5:7b-instruct"
GENERATION_TIMEOUT_S = 600.0
MAX_OUTPUT_TOKENS = 32


def _configured_model() -> str:
    return os.environ.get("ONEVOICECUT_LLM_MODEL", "").strip() or FALLBACK_MODEL


def _configured_base_url() -> str:
    return os.environ.get("ONEVOICECUT_OLLAMA_HOST", "").strip() or DEFAULT_BASE_URL


@pytest.fixture
def generator() -> Iterator[OllamaTextGenerator]:
    model = _configured_model()
    base_url = _configured_base_url()
    if not model_is_pulled(model, base_url=base_url):
        pytest.skip(
            f"Ollama at {base_url} is unreachable or {model} is not pulled "
            f"(remedy: `ollama serve` / `ollama pull {model}`)"
        )
    built = OllamaTextGenerator(
        model, base_url=base_url, timeout_s=GENERATION_TIMEOUT_S
    )
    try:
        yield built
    finally:
        built.close()


def test_a_real_completion_comes_back(generator: OllamaTextGenerator) -> None:
    reply = generator.complete(
        "Responde unicamente con la palabra hola.", max_output_tokens=MAX_OUTPUT_TOKENS
    )

    assert reply.strip(), "the real server returned an empty completion"
    assert generator.model_id() == _configured_model()


def _server_is_up() -> bool:
    """Liveness only, not the model probe: the 404 path needs a server, not a
    pulled model, so guarding it with `model_is_pulled` would skip a runnable
    test on a machine that pulled nothing."""
    try:
        with httpx.Client(base_url=_configured_base_url()) as client:
            return client.get("/api/tags", timeout=10.0).status_code == 200
    except httpx.HTTPError:
        return False


def test_an_unknown_model_is_refused_by_the_real_server() -> None:
    """The 404 translation, against the service that actually sends it. No
    model loads for this one — the server answers from its registry — so it
    costs one round-trip even on a cold machine."""
    if not _server_is_up():
        pytest.skip(f"Ollama at {_configured_base_url()} is unreachable")

    generator = OllamaTextGenerator(
        "no-such-model-anywhere",
        base_url=_configured_base_url(),
        timeout_s=30.0,
    )
    try:
        with pytest.raises(GenerationFailed, match="no-such-model-anywhere"):
            generator.complete("hola", max_output_tokens=8)
    finally:
        generator.close()
