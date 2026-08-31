"""`MediaSourcePort` over the filesystem. The upload writer.

The only async adapter in the system, because it is the only one on the HTTP side
of the boundary. Everything the worker touches is synchronous.

It writes each chunk as it arrives and never holds the file. That is not an
optimisation: multi-hour video is the normal input here, so a writer that
accumulated before flushing would fail at the sizes the operator actually
uploads, and would fail late.

Two things this adapter deliberately does *not* decide. It does not choose where
the bytes go — the destination is handed to it, so the on-disk layout stays in
storage. And it does not claim to know what the file is; `ffprobe` decides that,
and until it has run the container is recorded as unverified rather than guessed
from a suffix a client chose.
"""

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

from transcribe.domain.errors import UploadTooLarge
from transcribe.domain.ids import MediaId
from transcribe.domain.media import SourceMedia

# What a container is before anything has looked inside it. A literal rather than
# an empty string, so nothing downstream can read it as a missing value and fill
# it in; it says "not established", which is a fact.
UNVERIFIED_CONTAINER = "unverified"


class FilesystemMediaSource:
    def __init__(self, destination: Path) -> None:
        self._destination = destination

    async def store(
        self,
        media_id: MediaId,
        filename: str,
        stream: AsyncIterator[bytes],
        max_bytes: int,
    ) -> SourceMedia:
        """Stream to disk, counting and hashing on the way past.

        The size limit is a running counter rather than a check on the finished
        file: waiting until the end means having already written whatever was
        sent, which is the whole thing the limit exists to prevent.

        `filename` is recorded and never consulted. It is the client's, so it is
        metadata — the destination was decided before this call.
        """
        self._destination.parent.mkdir(parents=True, exist_ok=True)

        digest = hashlib.sha256()
        written = 0

        with open(self._destination, "wb") as handle:
            async for part in stream:
                written += len(part)
                if written > max_bytes:
                    raise UploadTooLarge(
                        f"upload exceeded {max_bytes} bytes and was stopped"
                    )
                digest.update(part)
                handle.write(part)

        return SourceMedia(
            media_id=media_id,
            original_filename=filename,
            stored_path=self._destination,
            size_bytes=written,
            container=UNVERIFIED_CONTAINER,
            checksum=digest.hexdigest(),
        )
