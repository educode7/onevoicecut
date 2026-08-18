"""The one async port — used only by the web adapter, never by the worker."""

from typing import AsyncIterator, Protocol

from transcribe.domain.ids import MediaId
from transcribe.domain.media import SourceMedia


class MediaSourcePort(Protocol):
    async def store(
        self,
        media_id: MediaId,
        filename: str,
        stream: AsyncIterator[bytes],
        max_bytes: int,
    ) -> SourceMedia:
        """Raises UploadTooLarge, UnsupportedContainer."""
        ...
