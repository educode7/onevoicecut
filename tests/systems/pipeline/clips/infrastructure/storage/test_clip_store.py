"""The render half of the persistence boundary: one export file per clip *and* profile.

A dict-backed fake cannot show whether a second profile's write lands beside the
first or on top of it, so these read real files under `tmp_path` rather than a
stubbed `open` — a mock would prove nothing about the thing that breaks. The job
record lifecycle these tests used to sit beside lives with the jobs facade (OQ3);
what belongs here is what `FilesystemClipStore` implements, plus the structural
proof that it satisfies `ClipStore`.

The job record these writes are strict about is admission's to create, so the
fixtures build the jobs facade and this one over a single `StorageCore`, the way
a composition root builds them.
"""

from pathlib import Path

import pytest

from onevoicecut.shared.domain.errors import (
    JobNotFound,
    RenderProfileInvalid,
)
from onevoicecut.shared.domain.ids import ClipId, JobId, make_clip_id, make_job_id, make_media_id
from onevoicecut.shared.domain.speaker import SpeakerMode
from onevoicecut.shared.infrastructure.storage.core import StorageCore
from onevoicecut.systems.pipeline.clips.domain.framing import TrackingConfidence
from onevoicecut.systems.pipeline.clips.domain.generation import ScriptVariant
from onevoicecut.systems.pipeline.clips.domain.interfaces.clip_store import ClipStore
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
from onevoicecut.systems.pipeline.clips.infrastructure.storage.clip_store import (
    FilesystemClipStore,
)
from onevoicecut.systems.pipeline.jobs.domain.jobs import EngineChoice, JobRecord, JobState
from onevoicecut.systems.pipeline.jobs.infrastructure.storage.job_store import (
    FilesystemJobStore,
)
from tests.contract.clip_export_storage import assert_keyed_by_clip_and_profile

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
CLIP_ID = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFG")


def a_job(job_id: JobId = JOB_ID, state: JobState = JobState.PENDING) -> JobRecord:
    return JobRecord(
        job_id=job_id,
        media_id=MEDIA_ID,
        state=state,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1723501234.5,
        updated_at=1723501234.5,
        worker_pid=None,
        error=None,
        owner=None,
    )


@pytest.fixture
def core(tmp_path: Path) -> StorageCore:
    return StorageCore(tmp_path)


@pytest.fixture
def jobs(core: StorageCore) -> FilesystemJobStore:
    """Admission's half: the job record every export write is strict about."""
    store = FilesystemJobStore(core)
    store.create_job(a_job(JOB_ID))
    return store


@pytest.fixture
def storage(core: StorageCore, jobs: FilesystemJobStore) -> FilesystemClipStore:
    return FilesystemClipStore(core)


def test_the_facade_satisfies_the_clip_store(
    storage: FilesystemClipStore,
) -> None:
    """Structural conformance, proven by mypy on this assignment rather than at
    runtime — the point of `Protocol` interfaces is that no implementation imports
    the interface to declare that it implements one."""
    store: ClipStore = storage

    store.save_clip_export(an_export())

    assert store.load_clip_exports(JOB_ID, CLIP_ID) == (an_export(),)


def an_export(
    profile: str = "vertical",
    state: ClipState = ClipState.PENDING,
    clip_id: ClipId = CLIP_ID,
) -> ClipExport:
    return ClipExport(
        job_id=JOB_ID,
        clip_id=clip_id,
        failure=None,
        clip=RenderedClip(
            clip_id=clip_id,
            job_id=JOB_ID,
            path=Path("render") / f"{clip_id}-{profile}.mp4",
            source_start_s=120.0,
            source_end_s=150.0,
            quality=OutputQuality(kind=OutputQualityKind.NATIVE, factor=0.89),
            subtitle_timing=SubtitleTimingSource.WORD_LEVEL,
            captions=CaptionCoverage.CONFIRMED_SPEECH,
            tracking=TrackingConfidence.WELL_TRACKED,
            duration=DurationCompliance(
                kind=DurationComplianceKind.WITHIN_CEILING, overrun_s=0.0
            ),
        ),
        profile=profile,
        source_start_s=120.0,
        source_end_s=150.0,
        title="Hermanos, escuchen",
        description="Un momento del sermon",
        variants=(
            ScriptVariant(
                target="tiktok", format="plain", body="Hola", duration_target_s=45.0
            ),
        ),
        state=state,
    )


class TestClipExportsOnDisk:
    """The clip id became a directory, and that is the whole point.

    One candidate now yields one export per distinct profile, so a flat
    `{clip_id}.json` cannot hold two of them. What a dict-backed fake cannot show
    is whether the second profile's write lands beside the first or on top of it.
    """

    def test_an_export_round_trips(self, storage: FilesystemClipStore) -> None:
        export = an_export()

        storage.save_clip_export(export)

        assert storage.load_clip_exports(JOB_ID, CLIP_ID) == (export,)

    def test_it_lands_at_the_clip_and_profile_path(
        self, storage: FilesystemClipStore
    ) -> None:
        """The path 13a-vi's `export_key` was written to agree with, so the key
        and the file are one derivation of the identity rather than two."""
        storage.save_clip_export(an_export())

        expected = (
            storage.job_dir(JOB_ID) / "render" / CLIP_ID / "vertical.json"
        )
        assert expected.is_file()

    def test_two_profiles_yield_two_exports(
        self, storage: FilesystemClipStore
    ) -> None:
        """The read side of the fact 13a-vi pinned on the type. A clip rendered
        under two profiles is two files, and losing one would silently deliver a
        destination nothing was ever written for."""
        storage.save_clip_export(an_export(profile="vertical"))
        storage.save_clip_export(an_export(profile="square"))

        loaded = storage.load_clip_exports(JOB_ID, CLIP_ID)

        assert {export.profile for export in loaded} == {"vertical", "square"}

    def test_a_state_transition_persists_for_one_profile_only(
        self, storage: FilesystemClipStore
    ) -> None:
        """Renders finish independently, so one profile reaching DONE must not
        advance a profile whose ffmpeg pass is still running."""
        storage.save_clip_export(an_export(profile="vertical"))
        storage.save_clip_export(an_export(profile="square"))

        storage.save_clip_export(an_export(profile="vertical", state=ClipState.DONE))

        states = {
            export.profile: export.state
            for export in storage.load_clip_exports(JOB_ID, CLIP_ID)
        }
        assert states == {
            "vertical": ClipState.DONE,
            "square": ClipState.PENDING,
        }

    def test_a_clip_nobody_rendered_is_empty_not_an_error(
        self, storage: FilesystemClipStore
    ) -> None:
        """A clip awaiting its render is a normal mid-run state, the way an
        absent chunk plan is."""
        assert storage.load_clip_exports(JOB_ID, CLIP_ID) == ()

    def test_exports_do_not_leak_between_clips(
        self, storage: FilesystemClipStore
    ) -> None:
        other = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFH")
        storage.save_clip_export(an_export())
        storage.save_clip_export(an_export(clip_id=other))

        assert len(storage.load_clip_exports(JOB_ID, CLIP_ID)) == 1

    def test_a_profile_name_that_escapes_the_job_directory_is_refused(
        self, storage: FilesystemClipStore
    ) -> None:
        """The profile is a path component. It originates in configuration, and
        configuration is not a trust boundary -- the same check every other
        client-influenced name in this system gets."""
        with pytest.raises(RenderProfileInvalid):
            storage.save_clip_export(an_export(profile="../../escape"))

    def test_saving_against_a_job_that_was_never_created_is_refused(
        self, tmp_path: Path
    ) -> None:
        """Writes are strict where reads are tolerant, so an export cannot create
        a directory holding no `job.json` -- the orphan `list_jobs` skips."""
        storage = FilesystemClipStore(StorageCore(tmp_path))

        with pytest.raises(JobNotFound):
            storage.save_clip_export(an_export())


def test_the_filesystem_meets_the_shared_clip_export_contract(
    storage: FilesystemClipStore,
) -> None:
    """The same body the fake is held to, so neither can drift into passing what
    the other fails."""
    assert_keyed_by_clip_and_profile(
        storage,
        JOB_ID,
        CLIP_ID,
        an_export(profile="vertical"),
        an_export(profile="square"),
    )


def test_the_export_path_reaches_no_network() -> None:
    """Structural, because an absence cannot be proven by calling something. The
    rendered clip and its metadata land in the job directory and no external
    service is written to -- a stated success criterion, not a preference."""
    import ast

    from onevoicecut.systems.pipeline.clips.infrastructure.storage import clip_store

    tree = ast.parse(Path(clip_store.__file__).read_text("utf-8"))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    assert not imported & {"httpx", "socket", "urllib", "requests", "http"}
