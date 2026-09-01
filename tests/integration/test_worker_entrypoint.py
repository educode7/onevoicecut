"""The whole pipeline against a real filesystem, with fake engines.

Everything below the worker has been proven against fakes, including the storage
adapter against `tmp_path`. What has never run is the wiring: real
`FilesystemTranscriptStorage`, real paths, real JSON on disk, driven from the
command line the supervisor will actually use.

Fake engines, deliberately. This asserts that the composition holds together, not
that ffmpeg or an ASR model works — those have their own marked tests, and a
default suite that loaded model weights would stop being run.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.media import SourceMedia
from onevoicecut.domain.transcript import SegmentKind
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.runtime.engine_resolver import EngineResolver
from onevoicecut.runtime.worker import EXIT_FAILED, EXIT_OK, EXIT_UNUSABLE, main
from tests.fakes.audio_extractor import FakeAudioExtractorPort
from tests.fakes.transcription import FakeTranscriptionPort, FlakyFakeTranscriptionPort

pytestmark = pytest.mark.integration

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")


def fake_extractor(job_dir: Path, job_id: JobId) -> AudioExtractorPort:
    return FakeAudioExtractorPort(job_id)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A job admitted exactly as the web process will admit one."""
    storage = FilesystemTranscriptStorage(tmp_path)
    storage.create_job(
        JobRecord(
            job_id=JOB_ID,
            media_id=MEDIA_ID,
            state=JobState.PENDING,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=1.0,
            updated_at=1.0,
            worker_pid=None,
            error=None,
            owner=None,
        )
    )
    storage.save_media(
        JOB_ID,
        SourceMedia(
            media_id=MEDIA_ID,
            original_filename="predicación del domingo.mp4",
            stored_path=storage.job_dir(JOB_ID) / "source.mp4",
            size_bytes=4096,
            container="mp4",
            checksum="deadbeef",
        ),
    )
    return tmp_path


def run(data_dir: Path, *, engine: EngineChoice = EngineChoice.LOCAL) -> int:
    return main(
        ["--job-id", JOB_ID, "--data-dir", str(data_dir)],
        resolver=EngineResolver({engine: FakeTranscriptionPort}),
        extractor_factory=fake_extractor,
    )


def test_a_job_runs_end_to_end_from_the_command_line(data_dir: Path) -> None:
    assert run(data_dir) == EXIT_OK


def test_the_worker_claims_the_job_with_its_own_pid(data_dir: Path) -> None:
    """Startup reconciliation reads this to tell a worker that died from one still
    going. Without it every running job looks abandoned after a web restart and
    gets marked INTERRUPTED out from under a live process."""
    import os

    run(data_dir)

    assert FilesystemTranscriptStorage(data_dir).load_job(JOB_ID).worker_pid == (
        os.getpid()
    )


def test_the_artifacts_land_on_the_real_filesystem(data_dir: Path) -> None:
    run(data_dir)

    job_dir = data_dir / "jobs" / JOB_ID
    assert (job_dir / "job.json").is_file()
    assert (job_dir / "plan.json").is_file()
    assert (job_dir / "transcript.json").is_file()
    assert (job_dir / "results" / "0000.json").is_file()


def test_no_temporary_file_is_left_behind(data_dir: Path) -> None:
    """Every write goes through the atomic commit, so a finished job's directory
    holds no `.tmp` — the same property resume depends on."""
    run(data_dir)

    assert list((data_dir / "jobs" / JOB_ID).rglob("*.tmp")) == []


def test_the_transcript_survives_the_round_trip_through_real_json(
    data_dir: Path,
) -> None:
    """The codec, the adapter and the loop have each been proven separately. This
    is the first time a transcript is written and read back by different code."""
    run(data_dir)

    transcript = FilesystemTranscriptStorage(data_dir).load_transcript(JOB_ID)
    assert transcript is not None
    assert [s.text for s in transcript.segments] == ["hola mundo"]
    assert transcript.segments[0].kind is SegmentKind.SPEECH


def test_accented_spanish_survives_the_wiring(data_dir: Path) -> None:
    """The media record holds a real filename from a Spanish-speaking operator."""
    run(data_dir)

    media = FilesystemTranscriptStorage(data_dir).load_media(JOB_ID)
    assert media.original_filename == "predicación del domingo.mp4"


def test_a_crashed_job_resumes_from_the_command_line(data_dir: Path) -> None:
    """Resume proven through the real entry point, not just the use case."""
    run(data_dir)
    storage = FilesystemTranscriptStorage(data_dir)
    before = storage.load_chunk_results(JOB_ID)
    transcriber = FlakyFakeTranscriptionPort()

    exit_code = main(
        ["--job-id", JOB_ID, "--data-dir", str(data_dir)],
        resolver=EngineResolver({EngineChoice.LOCAL: lambda: transcriber}),
        extractor_factory=fake_extractor,
    )

    assert exit_code == EXIT_OK
    assert transcriber.attempts == []
    assert storage.load_chunk_results(JOB_ID) == before


def test_a_job_asking_for_an_unconfigured_engine_fails_before_working(
    data_dir: Path,
) -> None:
    """The privacy rule, end to end: the job asked for local and local is not
    configured, so nothing runs rather than something else running."""
    exit_code = run(data_dir, engine=EngineChoice.CLOUD)

    assert exit_code == EXIT_FAILED
    assert FilesystemTranscriptStorage(data_dir).load_chunk_results(JOB_ID) == ()


def test_a_job_id_that_is_not_a_ulid_is_refused(data_dir: Path) -> None:
    exit_code = main(
        ["--job-id", "../../etc/passwd", "--data-dir", str(data_dir)],
        resolver=EngineResolver({EngineChoice.LOCAL: FakeTranscriptionPort}),
        extractor_factory=fake_extractor,
    )

    assert exit_code == EXIT_UNUSABLE


def test_an_unknown_job_is_reported_not_crashed(data_dir: Path) -> None:
    exit_code = main(
        ["--job-id", "01HQ3M8XKJ7VNPQR2ZYWB4TCFF", "--data-dir", str(data_dir)],
        resolver=EngineResolver({EngineChoice.LOCAL: FakeTranscriptionPort}),
        extractor_factory=fake_extractor,
    )

    assert exit_code == EXIT_FAILED


def test_a_build_with_no_engine_configured_says_so(data_dir: Path) -> None:
    """Real engines land in 7a/8a. Until then, saying so beats failing later."""
    assert main(["--job-id", JOB_ID, "--data-dir", str(data_dir)]) == EXIT_UNUSABLE
