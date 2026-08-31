"""A generic completion port — knows nothing about summaries, clips, or chunking."""

from typing import Protocol


class TextGenerationPort(Protocol):
    def model_id(self) -> str: ...

    def complete(
        self, prompt: str, *, max_output_tokens: int, temperature: float = 0.2
    ) -> str:
        """Raises ContextLengthExceeded, GenerationFailed."""
        ...
