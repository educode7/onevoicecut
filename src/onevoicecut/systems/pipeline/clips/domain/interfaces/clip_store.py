"""The clips half of the persistence boundary (design decision OQ3).

Narrow by construction: every method here takes or returns only types `clips`
owns — the generated artifacts, the per-profile export records and the render
claims. Job records and the transcript are declared on the interfaces of the
modules that own them, so no single Protocol has to import all three domains'
types; that single Protocol is what `TranscriptStoragePort` was, and it is
deleted rather than narrowed once every half has a home.

The facade implementing this over the shared filesystem core lands with slice
4b. Nothing here knows how a record is written — only what one looks like from
the side that reads and writes clip-production state.
"""

from typing import Protocol

from onevoicecut.shared.domain.ids import ClipId, JobId
from onevoicecut.systems.pipeline.clips.domain.generation import GenerationResult
from onevoicecut.systems.pipeline.clips.domain.rendering import ClipExport


class ClipStore(Protocol):
    def save_artifacts(self, job_id: JobId, artifacts: GenerationResult) -> None: ...

    def load_artifacts(self, job_id: JobId) -> GenerationResult | None:
        """Absent is `None`, matching `load_chunk_plan` and `load_transcript`:
        a job awaiting generation is a normal mid-run state, not a refusal.

        `save_artifacts` shipped alone in slice 10 — every other saved record
        already has a reader, and the asymmetry was an oversight rather than a
        decision. Its first production consumer resolves a `candidate_index`
        from an HTTP request back to the `ClipCandidate` it names.
        """
        ...

    def save_clip_export(self, export: ClipExport) -> None:
        """Committed by rename, like every other record a worker leaves behind.

        Takes the export alone: it carries its own job id through `clip.job_id`,
        so there is no pair a caller can get wrong — the rule
        `save_chunk_result` already follows.
        """
        ...

    def load_clip_exports(self, job_id: JobId, clip_id: ClipId) -> tuple[ClipExport, ...]:
        """Every profile's export for one clip, never just one.

        One candidate yields one export per distinct render profile, so "the
        export for this clip" names a set. Returning a single record would force
        the caller to say which profile it meant at the point it is least able to
        know, which is the same reason `export_key` takes both halves.

        An absent clip is an empty tuple rather than a refusal: a clip that has
        not been rendered yet is a normal mid-run state, the way an absent chunk
        plan is.
        """
        ...

    def list_clip_exports(self) -> tuple[ClipExport, ...]:
        """Every recorded export, across every job — discovery, not lookup.

        `load_clip_exports` needs both ids already known; it answers "the
        export(s) for this clip", never "which clips exist at all". The render
        drain sweep has to find every pending or abandoned render on the
        machine without being told where to look, the same way `list_jobs`
        lets the job drain find every queued job without being told a job id.
        Unfiltered, like `list_jobs`: the caller decides what is eligible.
        """
        ...

    def write_render_claim(self, job_id: JobId, clip_id: ClipId, *, at_s: float) -> None:
        """A render worker saying it has started work on this clip, and by
        nothing else — the render side of `write_heartbeat`.

        One claim per clip, not per profile: `render_pending_exports` claims a
        whole clip's pending profiles in one process, the same fan-out
        `render_worker`'s own module docstring already describes. A clip is
        bounded by `max_clip_seconds`, minutes rather than hours, so a single
        "claimed since T" timestamp is enough to detect an abandoned claim —
        the render's own ffmpeg timeout already proves a bounded, single-call
        render cannot hang unobserved the way a multi-hour transcription can,
        so there is nothing a periodic refresh would prove that the one-shot
        claim does not.
        """
        ...

    def render_claim_is_fresh(
        self, job_id: JobId, clip_id: ClipId, *, now_s: float, stale_after_s: float
    ) -> bool:
        """MUST fail closed: absent or unreadable is not fresh.

        Mirrors `heartbeat_is_fresh`'s asymmetry exactly. Believing an
        abandoned render is still live strands the clip in `RENDERING`
        forever, because nothing else ever re-picks it up; believing a live
        render is abandoned costs a duplicate worker for a few minutes at
        worst, bounded by the render's own timeout.
        """
        ...
