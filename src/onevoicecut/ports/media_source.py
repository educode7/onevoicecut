"""Temporary relocation shim for the slice-2a split chain.

`MediaSourcePort` now lives at
`onevoicecut.systems.pipeline.jobs.domain.interfaces.media_source` — the jobs
module owns the async upload boundary. This re-export keeps every legacy import
path resolving while the importer rewire lands in small reviewable steps; the
chain's final commit deletes this file, and nothing in the final tree imports
it.
"""

from onevoicecut.systems.pipeline.jobs.domain.interfaces.media_source import (
    MediaSourcePort as MediaSourcePort,
)
