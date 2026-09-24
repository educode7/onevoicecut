"""Is the configured model pulled, answered without generating anything.

Split from the adapter the way `adapters/asr/local/declarations.py` was split:
this is a question *about* the machine, asked before a three-hour transcript is
spent, and it must be answerable — and testable — without the thing it asks
about being present. The worker reads it after `transcribe_job` completes and
before any generation call: a model nobody pulled turns into one logged line
naming `ollama pull`, not into a `GenerationFailed` per window.

**It can only ever answer, never crash.** A down server, a non-200, a broken
body and an absent model are all the same honest `False` — the `find_spec`
lesson from `declarations.py`, where a probe that raised on the machine it
exists to describe took `capabilities()` down with it. The caller's decision
after `False` is to skip generation and leave the job COMPLETED with its
transcript, which is the right outcome for every one of those absences.

The match is exact, with no `:latest` normalization: Ollama lists fully
qualified names, and silently widening `qwen2.5` to `qwen2.5:latest` would
answer a question the operator did not ask — the same silent substitution the
engine resolver refuses between engines.
"""

import httpx

from onevoicecut.adapters.llm.ollama_generator import DEFAULT_BASE_URL, TAGS_PATH

# The tags listing is a few kilobytes from a server on localhost. Ten seconds
# is already far past "starting up", and a probe that hangs would hold a
# finished job's worker open for nothing.
DEFAULT_PROBE_TIMEOUT_S = 10.0


def model_is_pulled(
    model: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout_s: float = DEFAULT_PROBE_TIMEOUT_S,
    transport: httpx.BaseTransport | None = None,
) -> bool:
    """One `GET /api/tags`, read as a yes/no about this model."""
    try:
        with httpx.Client(base_url=base_url, transport=transport) as client:
            response = client.get(TAGS_PATH, timeout=timeout_s)
    except Exception:
        # Any exception, not only httpx's own types: a raw OSError escaping a
        # transport is the same absence as a down server, and this function's
        # promise is to answer, never crash — the worker calls it outside its
        # DomainError guard. A false negative costs one skipped generation; a
        # crash costs the job that already finished transcribing.
        return False

    if response.status_code != httpx.codes.OK:
        return False

    try:
        payload = response.json()
    except ValueError:
        return False

    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return False

    return any(
        isinstance(entry, dict) and entry.get("name") == model
        for entry in payload["models"]
    )
