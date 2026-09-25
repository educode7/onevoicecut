"""The web adapter's injectable surface: `WebDependencies` and its factories.

The application factory moved to `main.py` when the composition root landed
(slice 1d): deciding handlers and startup wiring belongs with the root that
reads configuration, while this module decides what a route closure may be
handed. Nothing here reads configuration at import time — that is what keeps
the adapter testable without a real data directory.
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from onevoicecut.adapters.ffmpeg.extractor import FfmpegAudioExtractor
from onevoicecut.adapters.storage.media_source import FilesystemMediaSource
from onevoicecut.shared.domain.ids import (
    ClipId,
    JobId,
    MediaId,
    OperatorId,
    generate_clip_id,
    generate_job_id,
    generate_media_id,
)
from onevoicecut.shared.infrastructure.settings import DEFAULT_MAX_UPLOAD_BYTES
from onevoicecut.domain.jobs import EngineChoice
from onevoicecut.domain.rendering import RenderProfile
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.shared.domain.capabilities import DeclaredSupport
from onevoicecut.ports.media_source import MediaSourcePort
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.usecases.generate_artifacts import SCRIPT_TARGETS, ScriptTarget
from onevoicecut.usecases.render_profiles import RENDER_PROFILES


MediaSourceFactory = Callable[[TranscriptStoragePort, JobId], MediaSourcePort]
ExtractorFactory = Callable[[TranscriptStoragePort, JobId], AudioExtractorPort]
Authenticator = Callable[[str | None], OperatorId]


def filesystem_media_source(
    storage: TranscriptStoragePort, job_id: JobId
) -> MediaSourcePort:
    """One writer per upload, aimed where storage says the source belongs."""
    return FilesystemMediaSource(storage.source_path(job_id))


def ffmpeg_extractor(
    storage: TranscriptStoragePort, job_id: JobId
) -> AudioExtractorPort:
    """The web process only ever calls `probe` on this.

    Extraction and slicing are the worker's, and they happen in a different
    process hours later. Sharing the adapter is not sharing the work.
    """
    return FfmpegAudioExtractor(storage.job_dir(job_id), job_id=job_id)


@dataclass(frozen=True, slots=True)
class WebDependencies:
    storage: TranscriptStoragePort
    # Required, deliberately, with no default: an app cannot be constructed
    # without deciding who authenticates it. Deny-by-default is structural —
    # the absence of auth is a build error, not a server that runs open.
    authenticate: Authenticator
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    now: Callable[[], float] = time.time
    new_job_id: Callable[[], JobId] = field(default=generate_job_id)
    new_media_id: Callable[[], MediaId] = field(default=generate_media_id)
    new_clip_id: Callable[[], ClipId] = field(default=generate_clip_id)
    # The same two registries `runtime/settings.py` cross-checks at boot,
    # injectable here for the same reason `capabilities` is: the shipped
    # profile's safe area is a measurement against the live 2026 destination
    # interfaces and moves whenever an operator re-measures, so a test proving
    # a clip request succeeds pins the registry it asserts against instead of
    # inheriting whatever the shipped one currently holds.
    render_profiles: Mapping[str, RenderProfile] = field(
        default_factory=lambda: RENDER_PROFILES
    )
    script_targets: Mapping[str, ScriptTarget] = field(
        default_factory=lambda: SCRIPT_TARGETS
    )
    media_source_for: MediaSourceFactory = field(default=filesystem_media_source)
    extractor_for: ExtractorFactory = field(default=ffmpeg_extractor)
    # No launcher, deliberately. Upload queues; the drain supervisor is the only
    # code that starts a worker. A launcher reachable from a route handler is one
    # refactor away from a second spawn decision point and the race it brings.
    capabilities: Callable[[EngineChoice], DeclaredSupport] | None = None
