"""The one async port — used only by the web adapter, never by the worker."""

from typing import AsyncIterator, Protocol

from onevoicecut.domain.ids import MediaId
from onevoicecut.domain.media import SourceMedia


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

    def discard(self, media: SourceMedia) -> None:
        """Remove a stored upload that turned out to be unusable.

        On the port because whatever owns writing the file owns removing it. A
        caller that unlinked the path itself would be a second place that knows
        how uploads live on disk.
        """
        ...
