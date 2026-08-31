"""Domain-level error types raised across port boundaries."""


class DomainError(Exception):
    """Base class for all transcribe domain errors."""


class UploadTooLarge(DomainError):
    pass


class UnsupportedContainer(DomainError):
    pass


class ExtractionFailed(DomainError):
    pass


class FfmpegUnavailable(DomainError):
    pass


class TranscriptionFailed(DomainError):
    pass


class ChunkTimeout(TranscriptionFailed):
    """A chunk did not return within its per-chunk budget.

    A kind of `TranscriptionFailed`, so an adapter that raises it satisfies every
    caller already handling transcription failure. Distinct because it is the one
    failure worth *not* retrying: a retry mostly spends the timeout again, and
    three attempts at a 30-minute budget is an hour and a half spent to learn the
    same thing.
    """


class ChunkTooLarge(DomainError):
    pass


class DiarizationUnsupported(DomainError):
    """Raised when a speaker-mode request reaches a non-diarizing adapter."""


class JobNotFound(DomainError):
    """Raised when no job is stored under the requested id.

    Also raised for an id that is not a well-formed ULID: a malformed id refers to
    no job either, and answering it differently would tell a caller which ids exist.
    """


class JobAlreadyExists(DomainError):
    """Raised when creating a job whose id is already stored.

    `create_job` and `update_job` are separate methods on the port; if create also
    overwrote, a reused id would silently discard a running job's state.
    """


class CorruptedRecord(DomainError):
    """Raised when persisted state cannot be read back as the entity it claims to be.

    Resume reads files a previous process wrote, so this is a routine failure mode
    after a crash, not an impossible one. It is a domain error because a caller must
    never have to catch `json.JSONDecodeError` to survive a half-written file.
    """


class ContextLengthExceeded(DomainError):
    pass


class GenerationFailed(DomainError):
    pass
