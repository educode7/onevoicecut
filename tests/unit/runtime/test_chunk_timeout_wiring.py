"""One per-chunk timeout, reaching both places that enforce it.

`settings.py` says of `chunk_timeout_s`: "the same value is passed to adapters
that can honour a timeout in-call." It was not. It reached `WatchdogConfig` and
stopped there — the worker is a separate process that reads its own environment,
and this was the one setting it never read. `transcribe_job` therefore ran on its
hardcoded `DEFAULT_CHUNK_TIMEOUT_S`, whatever the operator had configured.

The consequence is specific rather than cosmetic. An operator setting six
minutes gets a watchdog that kills a stalled worker at six minutes, and a cloud
adapter that goes on waiting thirty — so the in-call budget 8a-i built precisely
so the watchdog would stop being the only backstop never fires first. The
external kill still happens, and it is the blunter of the two: it takes down the
whole process, loses the chunk, and leaves INTERRUPTED for someone to resume.

The variable's *name* is the other half of this. `settings.py` reads it under two
aliases and the derived one is not the documented one, so a worker reading it
under a third spelling would be a setting that silently applies in one enforcement
path and not the other — exactly the failure the alias exists to prevent. Both
readers take the names from one place, and a test holds them there.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.domain.ids import JobId, make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.media import SourceMedia
from onevoicecut.domain.transcript import TranscriptSegment
from onevoicecut.ports.audio_extractor import AudioExtractorPort
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DiarizationSupport,
    TranscriptionCapabilities,
    WordTimingSupport,
)
from onevoicecut.ports.transcription import TranscriptionRequest
from onevoicecut.runtime import worker
from onevoicecut.runtime.engine_resolver import EngineResolver
from onevoicecut.runtime.settings import CHUNK_TIMEOUT_ENV_NAMES, Settings
from onevoicecut.runtime.worker import EXIT_UNUSABLE, configured_chunk_timeout_s
from onevoicecut.usecases.transcribe_job import DEFAULT_CHUNK_TIMEOUT_S
from tests.fakes.audio_extractor import FakeAudioExtractorPort

JOB_ID_TEXT = "01HQ3M8XKJ7VNPQR2ZYWB4TCFD"
JOB = make_job_id(JOB_ID_TEXT)
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
OWNER = make_operator_id("maria")

DOCUMENTED_NAME = "ONEVOICECUT_CHUNK_TIMEOUT_SECONDS"
DERIVED_NAME = "ONEVOICECUT_CHUNK_TIMEOUT_S"


class RecordingTranscriber:
    """Answers nothing, remembers the budget it was asked to work within."""

    def __init__(self) -> None:
        self.requests: list[TranscriptionRequest] = []

    def capabilities(self) -> TranscriptionCapabilities:
        return TranscriptionCapabilities(
            engine_id="recording-fake-asr",
            diarization=DiarizationSupport.UNSUPPORTED,
            non_speech_classification=ClassificationSupport.AVAILABLE,
            word_timing=WordTimingSupport.UNSUPPORTED,
            max_chunk_bytes=None,
            max_chunk_duration_s=None,
        )

    def transcribe(
        self, chunk: AudioChunk, request: TranscriptionRequest
    ) -> tuple[TranscriptSegment, ...]:
        self.requests.append(request)
        return ()


def _extractor(job_dir: Path, job_id: JobId) -> AudioExtractorPort:
    return FakeAudioExtractorPort(job_id)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neither spelling leaks in from the machine running the suite."""
    for name in (DOCUMENTED_NAME, DERIVED_NAME):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A queued cloud job on disk, ready for the worker to claim."""
    storage = FilesystemTranscriptStorage(tmp_path)
    storage.create_job(
        JobRecord(
            job_id=JOB,
            media_id=MEDIA_ID,
            state=JobState.QUEUED,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.CLOUD,
            created_at=1.0,
            updated_at=1.0,
            worker_pid=None,
            error=None,
            owner=OWNER,
        )
    )
    storage.save_media(
        JOB,
        SourceMedia(
            media_id=MEDIA_ID,
            original_filename="predicacion.mp4",
            stored_path=storage.job_dir(JOB) / "source",
            size_bytes=4096,
            container="mp4",
            checksum="deadbeef",
        ),
    )
    return tmp_path


def _budget_seen(data_dir: Path) -> float | None:
    """Run one job through `main` and report the budget the engine got.

    Through `main` rather than `run_job`, because reading the environment is
    `main`'s job — it is the entrypoint, and `run_job` takes the parsed value so
    a test can drive it with any budget it likes. Asserting against `run_job`
    would prove only that a default argument is a default argument.
    """
    transcriber = RecordingTranscriber()

    worker.main(
        ["--job-id", JOB_ID_TEXT, "--data-dir", str(data_dir)],
        resolver=EngineResolver({EngineChoice.CLOUD: lambda: transcriber}),
        extractor_factory=_extractor,
    )

    assert transcriber.requests, "the engine was never asked to transcribe"
    return transcriber.requests[0].timeout_s


class TestTheOperatorsValueReachesTheEngine:
    def test_the_configured_budget_is_what_the_adapter_is_given(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The defect, stated as a test. Before this unit the answer was always
        1800 — the hardcoded default — whatever the operator had set."""
        monkeypatch.setenv(DOCUMENTED_NAME, "600")

        assert _budget_seen(data_dir) == 600.0

    def test_an_unset_variable_still_gives_the_documented_default(
        self, data_dir: Path
    ) -> None:
        """Thirty minutes is a real default here, unlike the model size: it does
        not silently pick a quality level, it picks how long to wait."""
        assert _budget_seen(data_dir) == DEFAULT_CHUNK_TIMEOUT_S


class TestBothSpellingsAreRead:
    def test_the_documented_name_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DOCUMENTED_NAME, "45")

        assert configured_chunk_timeout_s() == 45.0

    def test_the_derived_name_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DERIVED_NAME, "45")

        assert configured_chunk_timeout_s() == 45.0

    def test_the_documented_name_wins_when_both_are_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same precedence as `AliasChoices` gives the web process. Two
        enforcement paths disagreeing about which spelling wins would be worse
        than either default."""
        monkeypatch.setenv(DOCUMENTED_NAME, "45")
        monkeypatch.setenv(DERIVED_NAME, "90")

        assert configured_chunk_timeout_s() == 45.0

    def test_a_blank_value_is_an_unset_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(DOCUMENTED_NAME, "   ")

        assert configured_chunk_timeout_s() == DEFAULT_CHUNK_TIMEOUT_S


class TestABadValueRefusesRatherThanDefaulting:
    """The web process already refuses to boot on these, via `gt=0`. A worker
    that silently substituted thirty minutes instead would enforce a timeout the
    operator did not ask for, in the process where it actually applies."""

    @pytest.mark.parametrize("value", ["not-a-number", "0", "-60", "1e"])
    def test_it_exits_unusable_naming_the_variable(
        self,
        value: str,
        data_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """An engine is configured on purpose. The realistic case is an operator
        whose build works and whose timeout has a typo in it — with no engine
        the build is refused for that first, and this would prove nothing."""
        monkeypatch.setenv(DOCUMENTED_NAME, value)

        code = worker.main(
            ["--job-id", JOB_ID_TEXT, "--data-dir", str(data_dir)],
            resolver=EngineResolver({}),
        )

        assert code == EXIT_UNUSABLE
        assert DOCUMENTED_NAME in capsys.readouterr().err

    def test_it_refuses_before_touching_the_job(
        self, data_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same rule as the missing-engine refusal: a worker that claimed the
        record and then exited would leave the drain counting a slot as busy for
        a process that is already gone."""
        monkeypatch.setenv(DOCUMENTED_NAME, "nonsense")
        monkeypatch.setattr(
            worker,
            "run_job",
            lambda *a, **k: pytest.fail("the job was started with a bad timeout"),
        )

        worker.main(
            ["--job-id", JOB_ID_TEXT, "--data-dir", str(data_dir)],
            resolver=EngineResolver({}),
        )


def test_both_readers_take_the_variable_names_from_one_place(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The unification 8.8 was actually for.

    Two processes enforce this timeout — the web process's watchdog from the
    outside, the worker's in-call budget from the inside — and they are separate
    programs reading separate environments. A name that drifted between them
    would be a setting that silently applies to one and not the other, which is
    the exact failure `settings.py` added the alias to prevent.
    """
    monkeypatch.setenv("ONEVOICECUT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(CHUNK_TIMEOUT_ENV_NAMES[0], "123")

    assert Settings().chunk_timeout_s == configured_chunk_timeout_s()  # type: ignore[call-arg]
