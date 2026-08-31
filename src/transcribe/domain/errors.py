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


class ChunkTooLarge(DomainError):
    pass


class DiarizationUnsupported(DomainError):
    """Raised when a speaker-mode request reaches a non-diarizing adapter."""


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
