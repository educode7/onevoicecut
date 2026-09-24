"""The Ollama adapter, proven without a server next door.

Every test drives `OllamaTextGenerator` through `httpx.MockTransport`, so the
default suite never touches a live Ollama — the one question a mock cannot
answer (does the real server still behave this way) is the `localmodel` e2e's,
in `test_ollama_real_server.py`.

The failure translations are the subject, not the happy path. The port promises
callers only `GenerationFailed`/`ContextLengthExceeded`, and this adapter can
never honestly raise the second one — Ollama truncates over-context prompts
silently — so every way a local server can fail (down, unknown model, broken
body, empty answer) has to arrive as the first, naming its own remedy.
"""

import json
from typing import Any

import httpx
import pytest

from onevoicecut.adapters.llm.ollama_generator import (
    DEFAULT_BASE_URL,
    OllamaTextGenerator,
)
from onevoicecut.domain.errors import GenerationFailed

MODEL = "qwen2.5:7b-instruct"


def _generator(handler: httpx.MockTransport) -> OllamaTextGenerator:
    return OllamaTextGenerator(MODEL, transport=handler)


def _reply(text: str, **extra: Any) -> httpx.Response:
    return httpx.Response(200, json={"response": text, "done": True, **extra})


class TestTheRequest:
    def test_the_payload_carries_the_port_arguments(self) -> None:
        """`max_output_tokens` becomes `num_predict` and `temperature` becomes
        `options.temperature` — the mapping is the adapter's whole job here, and
        a silently dropped option is a budget nobody enforced."""
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return _reply("hola")

        generator = _generator(httpx.MockTransport(handler))
        generator.complete("prompt", max_output_tokens=64, temperature=0.3)

        assert seen == [
            {
                "model": MODEL,
                "prompt": "prompt",
                "stream": False,
                "options": {"temperature": 0.3, "num_predict": 64},
            }
        ]

    def test_it_posts_to_the_generate_endpoint(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return _reply("hola")

        _generator(httpx.MockTransport(handler)).complete(
            "p", max_output_tokens=8
        )

        assert seen == ["/api/generate"]

    def test_the_default_temperature_reaches_options(self) -> None:
        seen: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(json.loads(request.content))
            return _reply("hola")

        _generator(httpx.MockTransport(handler)).complete("p", max_output_tokens=8)

        assert seen[0]["options"]["temperature"] == 0.2


class TestTheAnswer:
    def test_model_id_is_the_configured_model(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _reply("hola")

        assert _generator(httpx.MockTransport(handler)).model_id() == MODEL

    def test_the_response_text_comes_back_stripped(self) -> None:
        """Stripped, and the decision is load-bearing both ways: a JSON map
        answer never needs its surrounding newlines, and an answer that is
        *nothing but* whitespace is a model that said nothing — refused below
        rather than shipped as an empty script body."""

        def handler(request: httpx.Request) -> httpx.Response:
            return _reply("\n  hola mundo \n")

        assert _generator(httpx.MockTransport(handler)).complete(
            "p", max_output_tokens=8
        ) == "hola mundo"

    def test_an_empty_response_is_a_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return _reply("   ")

        with pytest.raises(GenerationFailed, match="empty"):
            _generator(httpx.MockTransport(handler)).complete(
                "p", max_output_tokens=8
            )


class TestTheTranslations:
    def test_a_blank_model_is_refused_at_construction(self) -> None:
        """The worker's `_configured` already reads blank as absent; this is the
        floor underneath that guard, so a blank model never reaches a request
        that would fail about the wrong thing."""
        with pytest.raises(GenerationFailed, match="model"):
            OllamaTextGenerator("   ")

    def test_a_down_server_names_the_host_and_the_remedy(self) -> None:
        """Ollama not running is the common failure, and its remedy is
        `ollama serve` — the message has to say so, because the operator sees
        this line and nothing else."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(GenerationFailed) as refusal:
            _generator(httpx.MockTransport(handler)).complete(
                "p", max_output_tokens=8
            )

        message = str(refusal.value)
        assert "127.0.0.1:11434" in message
        assert "ollama serve" in message

    def test_a_timeout_is_a_failure_naming_the_budget(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        generator = OllamaTextGenerator(
            MODEL, transport=httpx.MockTransport(handler), timeout_s=42.0
        )

        with pytest.raises(GenerationFailed, match="42"):
            generator.complete("p", max_output_tokens=8)

    def test_an_unknown_model_names_the_model_and_how_to_pull_it(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404, json={"error": f"model '{MODEL}' not found"}
            )

        with pytest.raises(GenerationFailed) as refusal:
            _generator(httpx.MockTransport(handler)).complete(
                "p", max_output_tokens=8
            )

        message = str(refusal.value)
        assert MODEL in message
        assert f"ollama pull {MODEL}" in message

    def test_a_server_error_names_the_model_status_and_error_field(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "boom"})

        with pytest.raises(GenerationFailed) as refusal:
            _generator(httpx.MockTransport(handler)).complete(
                "p", max_output_tokens=8
            )

        message = str(refusal.value)
        assert MODEL in message
        assert "500" in message
        assert "boom" in message

    def test_a_non_json_body_is_a_failure(self) -> None:
        """A 200 carrying HTML is what a proxy returns; `response.json()`
        raising must not cross the port boundary as a `ValueError`."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>not json</html>")

        with pytest.raises(GenerationFailed):
            _generator(httpx.MockTransport(handler)).complete(
                "p", max_output_tokens=8
            )

    def test_a_body_without_a_response_field_is_a_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"done": True})

        with pytest.raises(GenerationFailed):
            _generator(httpx.MockTransport(handler)).complete(
                "p", max_output_tokens=8
            )

    def test_a_non_string_response_field_is_a_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"response": 42})

        with pytest.raises(GenerationFailed):
            _generator(httpx.MockTransport(handler)).complete(
                "p", max_output_tokens=8
            )


class TestTheConfiguration:
    def test_the_base_url_is_injectable(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return _reply("hola")

        generator = OllamaTextGenerator(
            MODEL, base_url="http://gpu-box:11434", transport=httpx.MockTransport(handler)
        )
        generator.complete("p", max_output_tokens=8)

        assert seen == [f"http://gpu-box:11434/api/generate"]

    def test_the_default_base_url_is_the_local_server(self) -> None:
        assert DEFAULT_BASE_URL == "http://127.0.0.1:11434"
