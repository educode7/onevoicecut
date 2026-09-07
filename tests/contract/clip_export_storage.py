"""One clip-export contract, run against real files and against the fake.

The fake exists so use-case tests need no disk, and its danger is precise: a dict
keyed by clip alone round-trips beautifully and collapses two profiles into one.
A test suite written against it would then prove a profile fan-out that the
filesystem does not deliver -- the fake passing what disk fails, which is the one
thing a fake must never do.

Mutation-checked into existence: re-keying the fake by clip alone failed no test
in the suite until this body existed.
"""

from typing import Protocol

from onevoicecut.domain.ids import ClipId, JobId
from onevoicecut.domain.rendering import ClipExport, ClipState


class ClipExportStorage(Protocol):
    def save_clip_export(self, export: ClipExport) -> None: ...

    def load_clip_exports(
        self, job_id: JobId, clip_id: ClipId
    ) -> tuple[ClipExport, ...]: ...


def assert_keyed_by_clip_and_profile(
    storage: ClipExportStorage,
    job_id: JobId,
    clip_id: ClipId,
    first: ClipExport,
    second: ClipExport,
) -> None:
    """`first` and `second` differ only in their profile.

    Three properties, and the middle one is the reason the other two matter: a
    render finishing under one profile must not advance a profile whose ffmpeg
    pass is still running.
    """
    assert storage.load_clip_exports(job_id, clip_id) == ()

    storage.save_clip_export(first)
    storage.save_clip_export(second)
    loaded = storage.load_clip_exports(job_id, clip_id)

    assert {export.profile for export in loaded} == {first.profile, second.profile}

    from dataclasses import replace

    storage.save_clip_export(replace(first, state=ClipState.DONE))
    states = {
        export.profile: export.state
        for export in storage.load_clip_exports(job_id, clip_id)
    }

    assert states == {first.profile: ClipState.DONE, second.profile: second.state}
