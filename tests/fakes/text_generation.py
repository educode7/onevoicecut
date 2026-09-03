"""Fake conforming to `TextGenerationPort` — no provider, no bill, no network.

It records rather than merely answers, because the interesting assertions about
map-reduce are about *which prompts, in what order*: MAP makes one call per
window and REDUCE folds sequentially, so a fake that only returned text could not
prove either shape.

It can fail on purpose, and both ways it can fail are load-bearing.
`ContextLengthExceeded` is the whole subject of the halving retry, and
`GenerationFailed` is what a provider does on one call in eighty. A fake that
could only succeed would let a use case ship with no recovery path tested.

`fail_times` exists so a retry test can assert recovery rather than surrender:
fail once, then answer, and check the caller came back.
"""

from dataclasses import dataclass

MODEL_ID = "fake-llm"

_DEFAULT_REPLY = "resumen de prueba"


@dataclass(frozen=True, slots=True)
class GenerationCall:
    prompt: str
    max_output_tokens: int
    temperature: float


class FakeTextGenerationPort:
    def __init__(
        self,
        *,
        replies: tuple[str, ...] = (_DEFAULT_REPLY,),
        fail_with: Exception | None = None,
        fail_times: int | None = None,
    ) -> None:
        """`fail_times=None` with `fail_with` set means every call fails."""
        self._replies = replies or (_DEFAULT_REPLY,)
        self._fail_with = fail_with
        self._remaining_failures = fail_times
        self.calls: list[GenerationCall] = []

    @property
    def prompts(self) -> list[str]:
        return [call.prompt for call in self.calls]

    def model_id(self) -> str:
        return MODEL_ID

    def complete(
        self, prompt: str, *, max_output_tokens: int, temperature: float = 0.2
    ) -> str:
        # Recorded before the failure check: a test asserting "it retried three
        # times" reads this list, and a fake that logged only successes could not
        # tell three attempts from one.
        self.calls.append(
            GenerationCall(
                prompt=prompt,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
            )
        )

        if self._fail_with is not None and self._remaining_failures != 0:
            if self._remaining_failures is not None:
                self._remaining_failures -= 1
            raise self._fail_with

        # The last reply repeats rather than running out. A windowing test does
        # not know in advance how many windows it will produce, and should not
        # have to script a reply per window to assert something about windows.
        index = min(len(self.calls) - 1, len(self._replies) - 1)
        return self._replies[index]
