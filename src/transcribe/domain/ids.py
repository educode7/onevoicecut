"""Server-generated ULID identity types.

Never a client-supplied filename, never a sequence number. Validated before
the value ever touches a filesystem path.
"""

import re
from typing import NewType

_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

JobId = NewType("JobId", str)
MediaId = NewType("MediaId", str)


class InvalidIdError(ValueError):
    """Raised when a candidate string does not match the ULID pattern."""


def _validate_ulid(value: str) -> str:
    if not _ULID_PATTERN.match(value):
        raise InvalidIdError(f"{value!r} is not a valid ULID")
    return value


def make_job_id(value: str) -> JobId:
    return JobId(_validate_ulid(value))


def make_media_id(value: str) -> MediaId:
    return MediaId(_validate_ulid(value))
