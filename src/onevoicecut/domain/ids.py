"""Identity types: server-generated ULIDs and validated operator names.

Job and media ids are never a client-supplied filename, never a sequence
number. Validated before the value ever touches a filesystem path.

Generated here rather than pulled from a library. The encoding is forty lines,
the dependency would be permanent, and the property the rest of the system
actually leans on — that these sort by creation time, which is what lets
`list_jobs` return jobs in order by reading directory names — is worth owning a
test for rather than trusting.
"""

import os
import re
import time
from typing import NewType

_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

# Crockford base32: no I, L, O or U, so an id read off a screen cannot be
# transcribed into a different valid one. Matches `_ULID_PATTERN` exactly.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_TIMESTAMP_CHARS = 10  # 48 bits
_RANDOM_CHARS = 16  # 80 bits
_RANDOM_BYTES = 10
_MAX_TIMESTAMP_MS = 2**48 - 1

JobId = NewType("JobId", str)
MediaId = NewType("MediaId", str)
# Validated against the same pattern rather than a looser one: a clip id becomes
# a path component under the job directory and a route parameter, and a second
# weaker regex for the same class of value is how one of them eventually
# accepts a `..`.
ClipId = NewType("ClipId", str)

# Operator names come from configuration — the token map — not from a minting
# function: lowercase letters, digits, `-` and `_`, one to sixty-four
# characters. `:` and `;` delimit the token map, so a name containing either
# would parse as structure instead of identity, and the grammar forbids them
# outright. Paired with `fullmatch` below, never `match` + `$`.
_OPERATOR_NAME_PATTERN = re.compile(r"[a-z0-9_-]{1,64}")

OperatorId = NewType("OperatorId", str)


class InvalidIdError(ValueError):
    """Raised when a candidate string does not match an identity pattern."""


def _validate_ulid(value: str) -> str:
    if not _ULID_PATTERN.match(value):
        raise InvalidIdError(f"{value!r} is not a valid ULID")
    return value


def make_job_id(value: str) -> JobId:
    return JobId(_validate_ulid(value))


def make_media_id(value: str) -> MediaId:
    return MediaId(_validate_ulid(value))


def make_clip_id(value: str) -> ClipId:
    return ClipId(_validate_ulid(value))


def make_operator_id(value: str) -> OperatorId:
    # `fullmatch`, not `match` with `$`: a `$` anchor accepts a trailing
    # newline, and "maria\n" must not be the identity "maria".
    if not _OPERATOR_NAME_PATTERN.fullmatch(value):
        raise InvalidIdError(f"{value!r} is not a valid operator id")
    return OperatorId(value)


def _encode(value: int, chars: int) -> str:
    return "".join(
        _ALPHABET[(value >> (5 * shift)) & 0x1F] for shift in reversed(range(chars))
    )


def new_ulid(*, now_ms: int, randomness: bytes) -> str:
    """48 bits of millisecond timestamp, then 80 bits of randomness.

    The timestamp comes first and is big-endian, which is the whole point: ids
    generated later sort after ids generated earlier, so a directory listing is
    already in creation order and `list_jobs` needs no timestamps to sort by.

    Both inputs are rejected rather than coerced. Truncating an out-of-range
    timestamp would silently break that ordering, and padding short randomness
    would narrow the collision space without anyone asking — and a collision here
    means one job overwriting another's directory.
    """
    if not 0 <= now_ms <= _MAX_TIMESTAMP_MS:
        raise ValueError(f"timestamp {now_ms} does not fit in 48 bits")
    if len(randomness) != _RANDOM_BYTES:
        raise ValueError(
            f"expected {_RANDOM_BYTES} bytes of randomness, got {len(randomness)}"
        )

    return _encode(now_ms, _TIMESTAMP_CHARS) + _encode(
        int.from_bytes(randomness, "big"), _RANDOM_CHARS
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


def generate_job_id() -> JobId:
    return make_job_id(new_ulid(now_ms=_now_ms(), randomness=os.urandom(_RANDOM_BYTES)))


def generate_media_id() -> MediaId:
    return make_media_id(
        new_ulid(now_ms=_now_ms(), randomness=os.urandom(_RANDOM_BYTES))
    )


def generate_clip_id() -> ClipId:
    return make_clip_id(new_ulid(now_ms=_now_ms(), randomness=os.urandom(_RANDOM_BYTES)))
