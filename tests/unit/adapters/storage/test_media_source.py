"""The upload writer: bytes to disk without ever holding the file.

Multi-hour video is the normal input, so "read it and write it" is not an option
at any size the operator actually uploads. The load-bearing test here measures
peak heap while eight megabytes go past — the only way to tell a writer that
streams from one that accumulates and happens to finish. Watching the file grow
on disk would not do it: Python's buffered writer holds the first chunks in
memory anyway, and durability of a half-finished upload has no consumer.
"""

import hashlib
import tracemalloc
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from transcribe.adapters.storage.media_source import FilesystemMediaSource
from transcribe.domain.errors import UploadTooLarge
from transcribe.domain.ids import make_media_id

MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
GENEROUS = 1024**3


async def chunks(*parts: bytes) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    return tmp_path / "jobs" / "01HQ3M8XKJ7VNPQR2ZYWB4TCFD" / "source"


async def test_the_uploaded_bytes_land_exactly(destination: Path) -> None:
    source = FilesystemMediaSource(destination)

    await source.store(MEDIA_ID, "sermon.mp4", chunks(b"hola ", b"mundo"), GENEROUS)

    assert destination.read_bytes() == b"hola mundo"


async def test_memory_does_not_grow_with_the_upload(destination: Path) -> None:
    """The property that makes a multi-hour upload possible at all.

    Measured rather than asserted structurally, because the failure it guards
    against — accumulating the body and writing once at the end — passes every
    other test in this file and only shows up at a size nobody puts in a fixture.
    Eight megabytes streamed in 64 KiB chunks must not cost eight megabytes of
    Python heap.
    """
    payload_bytes = 8 * 1024**2
    chunk = b"x" * (64 * 1024)

    async def many_chunks() -> AsyncIterator[bytes]:
        for _ in range(payload_bytes // len(chunk)):
            yield chunk

    tracemalloc.start()
    try:
        await FilesystemMediaSource(destination).store(
            MEDIA_ID, "sermon.mp4", many_chunks(), GENEROUS
        )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert destination.stat().st_size == payload_bytes
    assert peak < payload_bytes // 4


async def test_the_recorded_size_is_what_was_written(destination: Path) -> None:
    media = await FilesystemMediaSource(destination).store(
        MEDIA_ID, "sermon.mp4", chunks(b"x" * 100, b"y" * 50), GENEROUS
    )

    assert media.size_bytes == 150
    assert media.size_bytes == destination.stat().st_size


async def test_the_checksum_is_computed_over_the_stream(destination: Path) -> None:
    """Computed while writing rather than by re-reading the file afterwards —
    re-reading a multi-hour upload to hash it would double the I/O."""
    payload = b"hola mundo"

    media = await FilesystemMediaSource(destination).store(
        MEDIA_ID, "sermon.mp4", chunks(payload), GENEROUS
    )

    assert media.checksum == hashlib.sha256(payload).hexdigest()


async def test_the_client_filename_is_metadata_and_never_the_path(
    destination: Path,
) -> None:
    """The one rule that makes a hostile filename harmless: it is recorded, and
    it is not consulted when deciding where anything goes."""
    media = await FilesystemMediaSource(destination).store(
        MEDIA_ID, "../../etc/passwd", chunks(b"x"), GENEROUS
    )

    assert media.original_filename == "../../etc/passwd"
    assert media.stored_path == destination
    assert destination.read_bytes() == b"x"


async def test_the_container_is_not_claimed_before_it_is_probed(
    destination: Path,
) -> None:
    """`ffprobe` decides content type, and it has not run yet. Recording the
    filename's suffix here would put a client's claim somewhere later code could
    mistake for a fact."""
    media = await FilesystemMediaSource(destination).store(
        MEDIA_ID, "sermon.mp4", chunks(b"x"), GENEROUS
    )

    assert media.container == "unverified"


async def test_an_upload_over_the_limit_is_refused(destination: Path) -> None:
    with pytest.raises(UploadTooLarge):
        await FilesystemMediaSource(destination).store(
            MEDIA_ID, "sermon.mp4", chunks(b"x" * 10, b"x" * 10), max_bytes=15
        )


async def test_the_limit_is_enforced_while_streaming_not_afterwards(
    destination: Path,
) -> None:
    """A running counter, not a check on the finished file. Waiting until the end
    means having already written whatever was sent."""
    consumed: list[int] = []

    async def endless() -> AsyncIterator[bytes]:
        for i in range(1000):
            consumed.append(i)
            yield b"x" * 1024

    with pytest.raises(UploadTooLarge):
        await FilesystemMediaSource(destination).store(
            MEDIA_ID, "sermon.mp4", endless(), max_bytes=4096
        )

    assert len(consumed) < 10


async def test_the_directory_is_created_if_it_does_not_exist(
    destination: Path,
) -> None:
    assert not destination.parent.exists()

    await FilesystemMediaSource(destination).store(
        MEDIA_ID, "sermon.mp4", chunks(b"x"), GENEROUS
    )

    assert destination.is_file()


async def test_an_empty_upload_is_still_written(destination: Path) -> None:
    """Rejecting it belongs to the probe, which will find no audio stream. The
    writer's job is to report what arrived, not to judge it."""
    media = await FilesystemMediaSource(destination).store(
        MEDIA_ID, "sermon.mp4", chunks(), GENEROUS
    )

    assert media.size_bytes == 0
    assert destination.is_file()
