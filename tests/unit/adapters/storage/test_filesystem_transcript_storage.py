"""The render half of the monolith's layout: one export file per clip *and* profile.

A dict-backed fake cannot show whether a second profile's write lands beside the
first or on top of it, so these read real files under `tmp_path` rather than a
stubbed `open` — a mock would prove nothing about the thing that breaks. The job
record lifecycle these tests used to sit beside now lives with the jobs facade
(OQ3); what remains here is what `FilesystemTranscriptStorage` is still the only
implementer of, plus the structural proof that it still satisfies its port.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.shared.domain.errors import (
    JobNotFound,
    RenderProfileInvalid,
)
from onevoicecut.domain.framing import TrackingConfidence
from onevoicecut.domain.generation import ScriptVariant
from onevoicecut.shared.domain.ids import ClipId, JobId, make_clip_id, make_job_id, make_media_id
from onevoicecut.ports.transcript_storage import TranscriptStoragePort
from onevoicecut.shared.domain.speaker import SpeakerMode
from onevoicecut.systems.pipeline.jobs.domain.jobs import EngineChoice, JobRecord, JobState
from tests.contract.clip_export_storage import assert_keyed_by_clip_and_profile
from onevoicecut.domain.rendering import (
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

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")


@pytest.fixture
def storage(tmp_path: Path) -> FilesystemTranscriptStorage:
    return FilesystemTranscriptStorage(tmp_path)


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


def test_the_adapter_satisfies_the_port(
    storage: FilesystemTranscriptStorage,
) -> None:
    """Structural conformance, proven by mypy on this assignment rather than at
    runtime — the point of `Protocol` ports is that no adapter imports the core to
    declare it implements one. All twelve methods now exist, so this binds."""
    storage.create_job(a_job())

    port: TranscriptStoragePort = storage

    assert len(port.list_jobs()) == 1


CLIP_ID = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFG")


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

    def test_an_export_round_trips(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        storage.create_job(a_job())
        export = an_export()

        storage.save_clip_export(export)

        assert storage.load_clip_exports(JOB_ID, CLIP_ID) == (export,)

    def test_it_lands_at_the_clip_and_profile_path(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """The path 13a-vi's `export_key` was written to agree with, so the key
        and the file are one derivation of the identity rather than two."""
        storage.create_job(a_job())

        storage.save_clip_export(an_export())

        expected = (
            storage.job_dir(JOB_ID) / "render" / CLIP_ID / "vertical.json"
        )
        assert expected.is_file()

    def test_two_profiles_yield_two_exports(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """The read side of the fact 13a-vi pinned on the type. A clip rendered
        under two profiles is two files, and losing one would silently deliver a
        destination nothing was ever written for."""
        storage.create_job(a_job())
        storage.save_clip_export(an_export(profile="vertical"))
        storage.save_clip_export(an_export(profile="square"))

        loaded = storage.load_clip_exports(JOB_ID, CLIP_ID)

        assert {export.profile for export in loaded} == {"vertical", "square"}

    def test_a_state_transition_persists_for_one_profile_only(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """Renders finish independently, so one profile reaching DONE must not
        advance a profile whose ffmpeg pass is still running."""
        storage.create_job(a_job())
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
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """A clip awaiting its render is a normal mid-run state, the way an
        absent chunk plan is."""
        storage.create_job(a_job())

        assert storage.load_clip_exports(JOB_ID, CLIP_ID) == ()

    def test_exports_do_not_leak_between_clips(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        other = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFH")
        storage.create_job(a_job())
        storage.save_clip_export(an_export())
        storage.save_clip_export(an_export(clip_id=other))

        assert len(storage.load_clip_exports(JOB_ID, CLIP_ID)) == 1

    def test_a_profile_name_that_escapes_the_job_directory_is_refused(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """The profile is a path component. It originates in configuration, and
        configuration is not a trust boundary -- the same check every other
        client-influenced name in this system gets."""
        storage.create_job(a_job())

        with pytest.raises(RenderProfileInvalid):
            storage.save_clip_export(an_export(profile="../../escape"))

    def test_saving_against_a_job_that_was_never_created_is_refused(
        self, storage: FilesystemTranscriptStorage
    ) -> None:
        """Writes are strict where reads are tolerant, so an export cannot create
        a directory holding no `job.json` -- the orphan `list_jobs` skips."""
        with pytest.raises(JobNotFound):
            storage.save_clip_export(an_export())


def test_the_filesystem_meets_the_shared_clip_export_contract(
    storage: FilesystemTranscriptStorage,
) -> None:
    """The same body the fake is held to, so neither can drift into passing what
    the other fails."""
    storage.create_job(a_job())

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

    from onevoicecut.adapters.storage import filesystem_transcript_storage

    tree = ast.parse(Path(filesystem_transcript_storage.__file__).read_text("utf-8"))
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
