"""Temporary relocation shim for the slice-2a split chain.

The jobs domain now lives at `onevoicecut.systems.pipeline.jobs.domain.jobs`.
This re-export keeps every legacy import path resolving while the importer
rewire lands in small reviewable steps; the chain's final commit deletes this
file, and nothing in the final tree imports it.
"""

from onevoicecut.systems.pipeline.jobs.domain.jobs import (
    TERMINAL_STATES as TERMINAL_STATES,
    WORKER_BOUND_STATES as WORKER_BOUND_STATES,
    EngineChoice as EngineChoice,
    JobProgress as JobProgress,
    JobRecord as JobRecord,
    JobState as JobState,
    SpeakerMode as SpeakerMode,
    derive_progress as derive_progress,
)
