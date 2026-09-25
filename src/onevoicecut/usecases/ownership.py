"""Temporary relocation shim for the slice-2a split chain.

The ownership rule now lives at
`onevoicecut.systems.pipeline.jobs.domain.ownership` — a pure domain rule per
the FCA design. This re-export keeps every legacy import path resolving while
the importer rewire lands in small reviewable steps; the chain's final commit
deletes this file, and nothing in the final tree imports it.
"""

from onevoicecut.systems.pipeline.jobs.domain.ownership import (
    require_owner as require_owner,
)
