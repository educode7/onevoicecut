"""The local LLM engine: Ollama behind `TextGenerationPort`.

Ollama is a system service on this machine, exactly like ffmpeg: installed by
the operator, reached over localhost, never a pip dependency. `httpx` is the
only import and it is already core, which is why this adapter needs no
requirements file of its own and no lazy-import discipline around it.

**This adapter never raises `ContextLengthExceeded`, and that is honesty, not
an omission.** Ollama does not refuse an over-context prompt — it silently
truncates it and answers from what survived, reporting the truth only in
`prompt_eval_count`. There is no status code, no error field, nothing on the
wire to translate, so any detection here would be a guess dressed as a
contract. A fake refusal would be worse than none: `run_map` halves and
retries on that exception, and retrying a prompt the server never rejected
would re-send the same text to be truncated the same way, doubling the cost
of a three-hour job to learn nothing. The windowing upstream
(`DEFAULT_MAP_WINDOW_TOKENS` against a `chars/4` estimate) is what keeps
prompts inside the context in the first place.

Every other failure arrives as `GenerationFailed` naming its own remedy,
because this message is what an operator reads in the server log after a job
whose transcription already succeeded: a down server says `ollama serve`, an
unpulled model says `ollama pull <model>`.

The timeout default is generous on purpose. A cold model load on this machine
measures ~84 s before the first token, and warm generation runs at ~5 tok/s on
CPU — a 512-token budget is another ~100 s. A default tuned to a warm server
would kill the first request after every idle stretch, which on a shared
machine is most of them.
"""

from typing import Any

import httpx

from onevoicecut.domain.errors import GenerationFailed

ENGINE_NAME = "ollama"

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
GENERATE_PATH = "/api/generate"
TAGS_PATH = "/api/tags"

# See the module docstring: cold load ~84 s plus minutes of CPU generation per
# call. Connect stays short — reaching a localhost server is instant or never,
# and a dead host should not consume the generation budget before saying so.
DEFAULT_TIMEOUT_S = 600.0
CONNECT_TIMEOUT_S = 10.0

# How much of a non-200 body is quoted in the refusal. Ollama's `error` field
# is one line; the cap exists for the proxy that answers with a whole HTML page.
_BODY_EXCERPT = 500


class OllamaTextGenerator:
    """One completion per POST, `stream:false`, over the local HTTP API."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """The model is a required value with no default — the same no-default
        rule `ONEVOICECUT_LOCAL_MODEL_SIZE` carries, because which model answers
        decides the quality of every artifact and a default would make that
        choice invisible at the one place it is made."""
        if not model.strip():
            raise GenerationFailed(
                f"the {ENGINE_NAME} generator has no model name; the model is a "
                "required configuration value and is never defaulted"
            )
        self._model = model.strip()
        self._base_url = base_url
        self._timeout_s = timeout_s
        self._client = httpx.Client(base_url=base_url, transport=transport)

    def model_id(self) -> str:
        return self._model

    def complete(
        self, prompt: str, *, max_output_tokens: int, temperature: float = 0.2
    ) -> str:
        payload = self._post(prompt, max_output_tokens, temperature)

        response = payload.get("response")
        if not isinstance(response, str):
            raise GenerationFailed(
                f"{ENGINE_NAME} answered with no usable response field for "
                f"model {self._model}: {sorted(payload)}"
            )
        # Stripped, and emptiness refused: whitespace-only is a model that said
        # nothing, and downstream a blank map answer is already a
        # `GenerationFailed` in `parse_map_response` while a blank script body
        # would ship silently. Refusing here keeps one honest answer for both.
        text = response.strip()
        if not text:
            raise GenerationFailed(
                f"{ENGINE_NAME} returned an empty response for model "
                f"{self._model}"
            )
        return text

    def close(self) -> None:
        """Release the connection pool. One generator is built per generation
        run, so a pool left open would outlive the run that opened it."""
        self._client.close()

    def _post(self, prompt: str, max_output_tokens: int, temperature: float) -> Any:
        try:
            response = self._client.post(
                GENERATE_PATH,
                json={
                    "model": self._model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_output_tokens,
                    },
                },
                timeout=httpx.Timeout(
                    self._timeout_s, connect=min(CONNECT_TIMEOUT_S, self._timeout_s)
                ),
            )
        except httpx.TimeoutException as error:
            raise GenerationFailed(
                f"{ENGINE_NAME} exceeded its {self._timeout_s:.0f}s budget "
                f"generating with model {self._model}"
            ) from error
        except httpx.HTTPError as error:
            # The common case, and its remedy is a command: the server is a
            # system service that is simply not running.
            raise GenerationFailed(
                f"could not reach the {ENGINE_NAME} server at {self._base_url}: "
                f"{error}. Is it running? Start it with `ollama serve`."
            ) from error
        except Exception as error:
            # The floor under the boundary: the clauses above name the httpx
            # shapes, and this one keeps the adapter's promise total — a raw
            # OSError escaping a transport must not reach a worker that
            # catches DomainError and has already delivered its transcript.
            # Nothing legitimate is swallowed: the try body is one HTTP call,
            # so no DomainError can originate inside it.
            raise GenerationFailed(
                f"the {ENGINE_NAME} call for model {self._model} failed below "
                f"the HTTP layer: {type(error).__name__}: {error}"
            ) from error

        if response.status_code == httpx.codes.NOT_FOUND:
            detail = _error_field(response)
            raise GenerationFailed(
                f"{ENGINE_NAME} does not know model {self._model}"
                f"{f': {detail}' if detail else ''}. Pull it with "
                f"`ollama pull {self._model}`."
            )

        if response.status_code != httpx.codes.OK:
            detail = _error_field(response) or response.text[:_BODY_EXCERPT]
            raise GenerationFailed(
                f"{ENGINE_NAME} refused model {self._model} with status "
                f"{response.status_code}: {detail}"
            )

        try:
            payload = response.json()
        except ValueError as error:
            # A 200 carrying HTML is what a proxy or a captive portal returns.
            raise GenerationFailed(
                f"{ENGINE_NAME} returned an unreadable response for model "
                f"{self._model}: {error}"
            ) from error

        if not isinstance(payload, dict):
            raise GenerationFailed(
                f"{ENGINE_NAME} answered model {self._model} with a body that "
                f"is not an object: {str(payload)[:_BODY_EXCERPT]}"
            )
        return payload


def _error_field(response: httpx.Response) -> str:
    """The server's own `error` string when the body carries one, else "".

    Ollama states why in that field and nowhere else, so the refusal quotes it
    — but reading it must never become a second failure on top of the first.
    """
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, str):
            return error[:_BODY_EXCERPT]
    return ""
