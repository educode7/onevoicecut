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


class EngineUnavailable(DomainError):
    """Raised when the engine a job asked for cannot be built.

    Never satisfied by substituting the other one. Engine choice is per job and
    content-dependent — private material goes local — so a silent fallback would
    ship exactly the material that was kept off a provider to a provider, and
    report success.
    """


class DiarizationUnsupported(DomainError):
    """Raised when a speaker-mode request reaches a non-diarizing adapter."""


class ClassificationUnsupported(DomainError):
    """Raised when script artifacts are asked of an engine that cannot classify.

    The second capability axis, refused for the same reason as the first. MAP
    windows are built from confirmed `SPEECH`, so an engine declaring
    `non_speech_classification=UNSUPPORTED` marks every segment `UNCERTAIN` and
    its transcripts filter to nothing — a three-hour job finishing COMPLETED
    with a blank summary and nothing anywhere saying why.

    Distinct from `DiarizationUnsupported` because the remedy is different: one
    is a request the operator can withdraw, the other is a property of the
    engine they chose.
    """


class JobNotFound(DomainError):
    """Raised when no job is stored under the requested id.

    Also raised for an id that is not a well-formed ULID: a malformed id refers to
    no job either, and answering it differently would tell a caller which ids exist.
    """


class JobNotOwned(DomainError):
    """Raised when an operator mutates a job that is not theirs.

    The one ownership rule raises it — `require_owner` — including for jobs with
    no owner at all: a legacy job is visible to everyone and mutable by nobody.
    The web adapter maps it to 403; job existence is already public under the
    shared listing, so the refusal leaks nothing new.
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


class FrameGeometryUnavailable(DomainError):
    """Raised when rendering needs the source geometry and the probe has none.

    Declared here rather than where it will be raised, because the axis it
    guards is the one `MediaProbe.frame` just opened: an audio-only source, or
    one carrying nothing but cover art, has no picture to crop toward. The
    renderer (slice 12) is what raises it — refusing the job rather than
    inventing a frame, which is the same no-silent-substitution rule the engine
    resolver and the capability axes already apply.
    """


class TrackingUnavailable(DomainError):
    """Raised when the tracker a clip needs cannot run on this build at all.

    The exception behind `DetectionSupport.REQUIRES_SETUP` and `UNSUPPORTED`.
    Declared capability lets a caller skip the clip before spending anything;
    this stops a caller who ignored the declaration from receiving an empty
    detection series, which reads exactly like a subject who never moved.

    Distinct from `DetectionFailed` because the operator's next move differs:
    install the vision extras, or render without a reframe.
    """


class DetectionFailed(DomainError):
    """Raised when a tracker that can run failed on this particular clip.

    Corrupt frames, an unreadable seek, a provider that fell over. Retryable in
    a way `TrackingUnavailable` is not, and a domain error because a caller must
    never have to catch a vision library's own exception type to survive one bad
    clip in eighty.
    """


class ContextLengthExceeded(DomainError):
    pass


class GenerationFailed(DomainError):
    pass
