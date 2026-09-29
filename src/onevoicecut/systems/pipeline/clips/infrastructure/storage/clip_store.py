"""`ClipStore` over the shared filesystem core: the clips half of the
persistence boundary (OQ3), with a real filesystem behind it.

The interface lives in `clips/domain/interfaces/clip_store.py` and is imported by
nobody here — satisfaction is structural, so this class binds to it without the
domain ever learning that a filesystem exists. What this module does import is the
clips domain and the shared `core`, and nothing else: the layout, the atomic rename
and the JSON field reads all live in `core`, while the clips records' own codec
lives beside the methods that write it, because a codec taking `ClipExport` can
live neither in the domain-agnostic core (AB-08: no `systems.*` vocabulary crosses
into `shared`) nor in `adapters/storage`, which this facade is the start of
replacing.

The codec functions are therefore defined here and re-exported from
`adapters.storage.serialization` (`X as X`, the same seam slices 2a and 3b used) so
the monolith keeps resolving them until slice 4f retires it — a facade reaching
back into the adapter it exists to replace would break the day that adapter went.
`ClipExport` is the first record carrying a `Path`, and it is converted by hand:
`asdict` leaves a `Path` intact and `json.dumps` then refuses it, so the one field
is spelled out rather than left to a default encoder — a codec that silently
stringified any unknown object would accept the next unserialisable type too, and
a codec here exists to reject a payload at the boundary instead of far away. It
decodes back through `Path`, so a record written on one platform reads as that
platform's flavour rather than as text.

`core` is injected rather than built from `data_dir`: a composition root
constructs it once and hands the same object to every facade, so one process has
exactly one owner of where things go. `runtime/` keeps constructing the monolith,
which satisfies `ClipStore` structurally, until slice 4f.
"""

from dataclasses import asdict
from pathlib import Path

from onevoicecut.shared.domain.errors import CorruptedRecord, RenderProfileInvalid
from onevoicecut.shared.domain.ids import ClipId, JobId, make_clip_id, make_job_id
from onevoicecut.shared.infrastructure.storage.core import (
    ARTIFACTS,
    RENDER_CLAIM,
    RENDER_DIRNAME,
    Record,
    StorageCore,
    _dumps,
    _field,
    _id_field,
    _loads,
    _member,
    _number,
    _objects,
    _optional_text,
    _text,
)
from onevoicecut.systems.pipeline.clips.domain.framing import TrackingConfidence
from onevoicecut.systems.pipeline.clips.domain.generation import (
    ClipCandidate,
    GenerationResult,
    ScriptVariant,
)
from onevoicecut.systems.pipeline.clips.domain.rendering import (
    CaptionCoverage,
    ClipExport,
    ClipState,
    DurationCompliance,
    DurationComplianceKind,
    OutputQuality,
    OutputQualityKind,
    RenderedClip,
    SubtitleTimingSource,
)


class FilesystemClipStore:
    def __init__(self, core: StorageCore) -> None:
        self._core = core

    def job_dir(self, job_id: JobId) -> Path:
        """The directory that holds everything belonging to one job.

        Public because the job directory is not private to persistence: the
        artifact set and the render tree are both located through it, and it is
        `core` that decides where it is. Not part of the `ClipStore` table —
        `jobs` owns admission, which is what creates the directory — but no export
        can be located without it, so it is answered from the same `core` rather
        than recomposed here.
        """
        return self._core.job_dir(job_id)

    def save_artifacts(self, job_id: JobId, artifacts: GenerationResult) -> None:
        self._core.write_atomic(self._core.writable(job_id) / ARTIFACTS, encode_artifacts(artifacts))

    def load_artifacts(self, job_id: JobId) -> GenerationResult | None:
        payload = self._core.read_optional(self.job_dir(job_id) / ARTIFACTS)
        return None if payload is None else decode_artifacts(payload)

    def save_clip_export(self, export: ClipExport) -> None:
        """One file per clip *and* profile, committed by rename like every other
        record a worker leaves behind.

        The clip id is a directory rather than a filename because one candidate
        now yields one export per distinct profile, and a flat `{clip_id}.json`
        could hold only the last one written -- silently, since a render that
        finished would leave no trace of the render it overwrote.
        """
        directory = self._core.writable(export.job_id) / RENDER_DIRNAME
        path = self._export_path(directory, export.clip_id, export.profile)
        self._core.write_atomic(path, encode_clip_export(export))

    def load_clip_exports(
        self, job_id: JobId, clip_id: ClipId
    ) -> tuple[ClipExport, ...]:
        """Every profile's export for one clip, sorted by profile so two reads of
        an unchanged directory agree -- `glob` does not promise an order, and a
        caller comparing two listings would otherwise see a difference the disk
        does not have.

        A stale `.tmp` is skipped by the glob rather than by a check, the way
        `load_chunk_results` already does it, so there is no path that forgets to.
        """
        directory = self.job_dir(job_id) / RENDER_DIRNAME / clip_id
        if not directory.is_dir():
            return ()
        exports = [
            decode_clip_export(path.read_text(encoding="utf-8"))
            for path in directory.glob("*.json")
        ]
        return tuple(sorted(exports, key=lambda export: export.profile))

    def list_clip_exports(self) -> tuple[ClipExport, ...]:
        """Every export on the machine, discovered by directory glob -- the
        render drain's `list_jobs`.

        Scoped to directories that are real job ids, the way `list_jobs` scopes
        its own listing, so a scratch folder under the jobs root cannot be
        mistaken for one. Sorted by `(job_id, clip_id, profile)` so two reads
        of an unchanged store agree, the same reason `load_clip_exports` sorts.
        """
        if not self._core.jobs_root.is_dir():
            return ()
        exports = [
            decode_clip_export(path.read_text(encoding="utf-8"))
            for directory in self._core.jobs_root.iterdir()
            if directory.is_dir() and self._core.is_job_id(directory.name)
            for path in (directory / RENDER_DIRNAME).glob("*/*.json")
            if path.is_file()
        ]
        return tuple(
            sorted(exports, key=lambda export: (export.job_id, export.clip_id, export.profile))
        )

    def write_render_claim(self, job_id: JobId, clip_id: ClipId, *, at_s: float) -> None:
        """The render side of `write_heartbeat`: one timestamp per clip, not
        per profile -- a whole clip's pending profiles are claimed by one
        process in one call, so one file records it."""
        directory = self._core.writable(job_id) / RENDER_DIRNAME / clip_id
        self._core.write_atomic(directory / RENDER_CLAIM, repr(float(at_s)))

    def render_claim_is_fresh(
        self, job_id: JobId, clip_id: ClipId, *, now_s: float, stale_after_s: float
    ) -> bool:
        """The render side of `heartbeat_is_fresh`, same fail-closed asymmetry
        and the same reading of a future timestamp as fresh under clock skew."""
        raw = self._core.read_optional(
            self.job_dir(job_id) / RENDER_DIRNAME / clip_id / RENDER_CLAIM
        )
        if raw is None:
            return False
        try:
            written_at = float(raw)
        except ValueError:
            return False
        return now_s - written_at <= stale_after_s

    @staticmethod
    def _export_path(render_dir: Path, clip_id: ClipId, profile: str) -> Path:
        """The profile is a path component, so it is checked like every other
        client-influenced name here.

        It originates in configuration rather than in a request, but
        configuration is not a trust boundary: the same operator file that names
        a profile is edited by hand, and a name carrying `..` would put an
        export outside the job it belongs to. `RenderProfileInvalid` because a
        bad profile name fails identically on every retry -- the distinction that
        type was created for.
        """
        root = render_dir.resolve()
        candidate = (render_dir / clip_id / f"{profile}.json").resolve()
        if not candidate.is_relative_to(root):
            raise RenderProfileInvalid(
                f"render profile {profile!r} does not name a file inside the "
                f"job's render directory; a profile name is a path component "
                f"and cannot escape the job it belongs to"
            )
        return candidate


def _job_id(record: Record) -> JobId:
    """Validated here because the value is about to become a path component."""
    return _id_field(record, "job_id", make_job_id)


def encode_artifacts(artifacts: GenerationResult) -> str:
    return _dumps(asdict(artifacts))


def decode_artifacts(payload: str) -> GenerationResult:
    record = _loads(payload)
    return GenerationResult(
        job_id=_job_id(record),
        summary=_text(record, "summary"),
        clip_candidates=tuple(
            ClipCandidate(
                start_s=_number(item, "start_s"),
                end_s=_number(item, "end_s"),
                hook=_text(item, "hook"),
                quote=_text(item, "quote"),
                rationale=_text(item, "rationale"),
                score=_number(item, "score"),
                variants=tuple(
                    ScriptVariant(
                        target=_text(variant, "target"),
                        format=_text(variant, "format"),
                        body=_text(variant, "body"),
                        duration_target_s=_number(variant, "duration_target_s"),
                    )
                    for variant in _objects(item, "variants")
                ),
            )
            for item in _objects(record, "clip_candidates")
        ),
    )


def _clip_id(record: Record) -> ClipId:
    """Validated here for the reason `_job_id` is: it is about to become a path
    component, and a stored record is not a trusted one."""
    return _id_field(record, "clip_id", make_clip_id)


def encode_clip_export(export: ClipExport) -> str:
    record = asdict(export)
    if export.clip is not None:
        record["clip"]["path"] = str(export.clip.path)
    return _dumps(record)


def decode_clip_export(payload: str) -> ClipExport:
    record = _loads(payload)
    return ClipExport(
        job_id=_job_id(record),
        clip_id=_clip_id(record),
        clip=_rendered_clip(record),
        failure=_optional_text(record, "failure"),
        profile=_text(record, "profile"),
        source_start_s=_number(record, "source_start_s"),
        source_end_s=_number(record, "source_end_s"),
        title=_text(record, "title"),
        description=_text(record, "description"),
        variants=tuple(
            ScriptVariant(
                target=_text(variant, "target"),
                format=_text(variant, "format"),
                body=_text(variant, "body"),
                duration_target_s=_number(variant, "duration_target_s"),
            )
            for variant in _objects(record, "variants")
        ),
        state=_member(record, "state", ClipState),
    )


def _rendered_clip(record: Record) -> RenderedClip | None:
    """`None` is a render that was refused, not a corrupted record.

    A refusal has no file and no honest value for any of the four declarations,
    so the absence is the fact being stored rather than a field that went
    missing -- which is why this is the codec's second key-tolerant read, and
    why it still validates every field once a clip is actually present.
    """
    clip = record.get("clip")
    if clip is None:
        return None
    if not isinstance(clip, dict):
        raise CorruptedRecord("field 'clip' is not an object")
    quality = _field(clip, "quality")
    if not isinstance(quality, dict):
        raise CorruptedRecord("field 'quality' is not an object")
    duration = _field(clip, "duration")
    if not isinstance(duration, dict):
        raise CorruptedRecord("field 'duration' is not an object")
    return RenderedClip(
        clip_id=_clip_id(clip),
        job_id=_job_id(clip),
        path=Path(_text(clip, "path")),
        source_start_s=_number(clip, "source_start_s"),
        source_end_s=_number(clip, "source_end_s"),
        quality=OutputQuality(
            kind=_member(quality, "kind", OutputQualityKind),
            factor=_number(quality, "factor"),
        ),
        subtitle_timing=_member(clip, "subtitle_timing", SubtitleTimingSource),
        captions=_member(clip, "captions", CaptionCoverage),
        tracking=_member(clip, "tracking", TrackingConfidence),
        duration=DurationCompliance(
            kind=_member(duration, "kind", DurationComplianceKind),
            overrun_s=_number(duration, "overrun_s"),
        ),
    )
