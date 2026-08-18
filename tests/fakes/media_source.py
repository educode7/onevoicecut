"""In-memory fake conforming to MediaSourcePort — no real disk I/O."""

from pathlib import Path
from typing import AsyncIterator

from transcribe.domain.ids import MediaId
from transcribe.domain.media import SourceMedia


class FakeMediaSourcePort:
    async def store(
        self,
        media_id: MediaId,
        filename: str,
        stream: AsyncIterator[bytes],
        max_bytes: int,
    ) -> SourceMedia:
        data = bytearray()
        async for part in stream:
            data.extend(part)
        return SourceMedia(
            media_id=media_id,
            original_filename=filename,
            stored_path=Path(filename),
            size_bytes=len(data),
            container="mp4",
            checksum="fake-checksum",
        )
