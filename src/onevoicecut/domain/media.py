"""Temporary relocation shim for the slice-2a split chain.

The media entities now live at `onevoicecut.systems.pipeline.jobs.domain.media`.
This re-export keeps every legacy import path resolving while the importer
rewire lands in small reviewable steps; the chain's final commit deletes this
file, and nothing in the final tree imports it.
"""

from onevoicecut.systems.pipeline.jobs.domain.media import (
    AudioTrack as AudioTrack,
    FrameSize as FrameSize,
    MediaProbe as MediaProbe,
    SourceMedia as SourceMedia,
)
