"""Transitional re-export: this vocabulary now lives in `onevoicecut.shared.domain.media`.

Kept so existing importers stay green while they are rewired area by area;
deleted once every importer names the kernel module directly.
"""

from onevoicecut.shared.domain.media import (
    AudioTrack as AudioTrack,
    FrameSize as FrameSize,
    MediaProbe as MediaProbe,
    SourceMedia as SourceMedia,
)
