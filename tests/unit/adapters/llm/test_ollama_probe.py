"""The preflight probe, driven by facts instead of by a server.

The probe answers one question — is the configured model pulled? — and the
discipline under test is that it can only ever answer, never crash. A probe
that raised on a down server would repeat the `declarations.py` `find_spec`
lesson: the check whose whole purpose is to describe an incomplete machine is
the one that must survive that machine. Every absence — server down, non-200,
broken body — is the same honest `False`.
"""

import httpx

from onevoicecut.adapters.llm.probe import model_is_pulled

MODEL = "qwen2.5:7b-instruct"


def _tags(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": name} for name in names]})


class TestThePositiveAnswer:
    def test_a_pulled_model_is_found(self) -> None:
        transport = httpx.MockTransport(
            lambda request: _tags("llama3.2:latest", MODEL)
        )

        assert model_is_pulled(MODEL, transport=transport) is True

    def test_it_asks_the_tags_endpoint(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return _tags(MODEL)

        model_is_pulled(MODEL, transport=httpx.MockTransport(handler))

        assert seen == ["/api/tags"]


class TestTheHonestNegatives:
    def test_an_unpulled_model_is_not_found(self) -> None:
        transport = httpx.MockTransport(lambda request: _tags("llama3.2:latest"))

        assert model_is_pulled(MODEL, transport=transport) is False

    def test_the_match_is_exact(self) -> None:
        """No `:latest` normalization, deliberately: Ollama lists fully
        qualified names, and a probe that quietly widened `qwen2.5` to
        `qwen2.5:latest` would answer a question the operator did not ask —
        the same silent substitution the engine resolver refuses."""
        transport = httpx.MockTransport(lambda request: _tags("qwen2.5:latest"))

        assert model_is_pulled("qwen2.5", transport=transport) is False

    def test_a_down_server_is_false_rather_than_a_crash(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        assert model_is_pulled(MODEL, transport=httpx.MockTransport(handler)) is False

    def test_a_non_200_is_false(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(500, text="boom")
        )

        assert model_is_pulled(MODEL, transport=transport) is False

    def test_a_broken_body_is_false(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>")
        )

        assert model_is_pulled(MODEL, transport=transport) is False

    def test_a_body_without_a_model_list_is_false(self) -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json={"unrelated": 1})
        )

        assert model_is_pulled(MODEL, transport=transport) is False

    def test_an_empty_registry_is_false(self) -> None:
        transport = httpx.MockTransport(lambda request: _tags())

        assert model_is_pulled(MODEL, transport=transport) is False
