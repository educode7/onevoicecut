"""A generic completion port — knows nothing about summaries, clips, or chunking."""

from typing import Protocol


class TextGenerationPort(Protocol):
    def model_id(self) -> str: ...

    def complete(
        self,
        prompt: str,
        *,
        max_output_tokens: int,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> str:
        """Raises ContextLengthExceeded, GenerationFailed.

        `json_mode` is per call, never per adapter: the port serves one caller
        whose answer must be JSON and two that are prose, and a JSON grammar
        would make the prose impossible to answer.
        """
        ...
