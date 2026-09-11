"""The render worker against a real filesystem, with fake heavy adapters.

The mirror of `test_worker_entrypoint.py`: `render_pending_exports` has been
proven against fakes with no disk at all, and the wiring tests in
`tests/unit/runtime/test_render_worker_entrypoint.py` prove `main` and
`run_render` against a monkeypatched orchestration function. What neither
proves is the composition itself -- real `FilesystemTranscriptStorage`, real
paths, real JSON on disk, a job admitted and a clip requested exactly as the
web process is specified to do it, driven from the command line the future
spawn call will actually use.

Fake tracker, renderer and extractor, deliberately: this asserts the wiring
holds together, not that ffmpeg or a vision model works -- those have their
own markers, and a default suite that loaded either would stop being run.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.ids import JobId, make_clip_id, make_job_id, make_media_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.media import SourceMedia
from onevoicecut.domain.rendering import ClipState
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.runtime.render_worker import EXIT_FAILED, EXIT_OK, EXIT_UNUSABLE, main
from tests.unit.runtime.test_render_worker import (
    RENDER_PROFILES_TWO,
    RecordingRenderer,
    a_pending_export,
)
from tests.fakes.subject_tracker import FakeSubjectTrackerPort

pytestmark = pytest.mark.integration

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
CLIP_ID = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFG")


class _FakeExtractor:
    """`probe` only -- this worker never extracts or slices audio."""

    def __init__(self, job_id: JobId) -> None:
        self._job_id = job_id

    def probe(self, media: SourceMedia) -> object:
        from onevoicecut.domain.media import FrameSize, MediaProbe

        return MediaProbe(
            duration_s=7200.0, container="mp4", has_audio=True, frame=FrameSize(1920, 1080)
        )

    def extract(self, media: SourceMedia, dest: Path) -> object:
        raise AssertionError("the render worker does not extract audio")

    def slice(self, track: object, planned: object, dest: Path) -> object:
        raise AssertionError("the render worker does not slice audio")


def fake_extractor(job_dir: Path, job_id: JobId) -> AudioExtractorPort:
    return _FakeExtractor(job_id)  # type: ignore[return-value]


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A job admitted and a clip requested exactly as the web process is
    specified to do it: a real `COMPLETED` job, real media, one `PENDING`
    export per distinct profile, all before this worker is ever spawned."""
    storage = FilesystemTranscriptStorage(tmp_path)
    storage.create_job(
        JobRecord(
            job_id=JOB_ID,
            media_id=MEDIA_ID,
            state=JobState.COMPLETED,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=1.0,
            updated_at=1.0,
            worker_pid=None,
            error=None,
            owner=None,
        )
    )
    source = storage.job_dir(JOB_ID) / "source"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"not really a video")
    storage.save_media(
        JOB_ID,
        SourceMedia(
            media_id=MEDIA_ID,
            original_filename="predicación del domingo.mp4",
            stored_path=source,
            size_bytes=source.stat().st_size,
            container="mp4",
            checksum="deadbeef",
        ),
    )
    return tmp_path


def run(data_dir: Path, *, clip_id: str = CLIP_ID, **kwargs: object) -> int:
    return main(
        ["--job-id", JOB_ID, "--clip-id", clip_id, "--data-dir", str(data_dir)],
        tracker=kwargs.get("tracker", FakeSubjectTrackerPort()),  # type: ignore[arg-type]
        renderer=kwargs.get("renderer", RecordingRenderer()),  # type: ignore[arg-type]
        extractor_factory=fake_extractor,
        render_profiles=kwargs.get("render_profiles", RENDER_PROFILES_TWO),  # type: ignore[arg-type]
    )


def test_a_clip_renders_end_to_end_from_the_command_line(data_dir: Path) -> None:
    storage = FilesystemTranscriptStorage(data_dir)
    storage.save_clip_export(a_pending_export(profile="vertical"))

    assert run(data_dir) == EXIT_OK

    loaded = FilesystemTranscriptStorage(data_dir).load_clip_exports(JOB_ID, CLIP_ID)
    assert [e.state for e in loaded] == [ClipState.DONE]


def test_two_pending_profiles_both_land_on_the_real_filesystem(
    data_dir: Path,
) -> None:
    storage = FilesystemTranscriptStorage(data_dir)
    storage.save_clip_export(a_pending_export(profile="vertical"))
    storage.save_clip_export(a_pending_export(profile="square"))

    assert run(data_dir) == EXIT_OK

    loaded = FilesystemTranscriptStorage(data_dir).load_clip_exports(JOB_ID, CLIP_ID)
    assert {e.profile for e in loaded} == {"vertical", "square"}
    assert all(e.state is ClipState.DONE for e in loaded)


def test_no_pending_export_for_the_clip_refuses_without_writing(
    data_dir: Path,
) -> None:
    """No `PENDING` record was ever written for this clip id -- nothing was
    requested, so nothing is rendered, and the exit code says so."""
    unrequested = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFH")

    assert run(data_dir, clip_id=unrequested) == EXIT_UNUSABLE
    assert (
        FilesystemTranscriptStorage(data_dir).load_clip_exports(JOB_ID, unrequested)
        == ()
    )


def test_a_malformed_clip_id_is_refused_before_storage_is_touched(
    data_dir: Path,
) -> None:
    exit_code = main(
        ["--job-id", JOB_ID, "--clip-id", "not-a-ulid", "--data-dir", str(data_dir)],
        tracker=FakeSubjectTrackerPort(),
        renderer=RecordingRenderer(),
        extractor_factory=fake_extractor,
    )

    assert exit_code == EXIT_UNUSABLE


def test_the_profile_rendered_is_read_off_the_export_not_the_variants(
    data_dir: Path,
) -> None:
    """The sharpest proof available end to end: a variant naming a network no
    script-target registry maps to anything still renders, because this
    worker never consults one -- it reads `export.profile` directly."""
    from onevoicecut.domain.generation import ScriptVariant

    storage = FilesystemTranscriptStorage(data_dir)
    storage.save_clip_export(
        a_pending_export(
            profile="vertical",
            variants=(
                ScriptVariant(
                    target="a-network-no-registry-maps",
                    format="plain",
                    body="Hola",
                    duration_target_s=45.0,
                ),
            ),
        )
    )

    assert run(data_dir) == EXIT_OK


def test_a_tracker_declaring_no_support_fails_the_clip_not_the_process(
    data_dir: Path,
) -> None:
    from tests.fakes.subject_tracker import UnavailableSubjectTrackerPort

    storage = FilesystemTranscriptStorage(data_dir)
    storage.save_clip_export(a_pending_export(profile="vertical"))

    exit_code = run(data_dir, tracker=UnavailableSubjectTrackerPort())

    assert exit_code == EXIT_FAILED
    loaded = FilesystemTranscriptStorage(data_dir).load_clip_exports(JOB_ID, CLIP_ID)
    assert [e.state for e in loaded] == [ClipState.FAILED]
    assert "TrackingUnavailable" in (loaded[0].failure or "")
