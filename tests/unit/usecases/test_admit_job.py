"""Tests for the admit_job use case — engine/speaker-mode compatibility validation.

These tests exercise the pure `_validate_compatibility` helper and, later,
the `admit_job()` integration with a capabilities callable. The helper is
module-level in `usecases/admit_job.py` and is the single definition of
engine/speaker-mode compatibility shared by admission and port-level defense.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.domain.errors import DiarizationUnsupported
from onevoicecut.domain.ids import JobId
from onevoicecut.domain.jobs import EngineChoice, JobState, SpeakerMode
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DiarizationSupport,
    TranscriptionCapabilities,
)
from onevoicecut.ports.transcription import TranscriptionRequest
from onevoicecut.usecases.admit_job import _validate_compatibility, admit_job
from tests.fakes.transcription import (
    FakeTranscriptionPort,
    NonClassifyingFakeTranscriptionPort,
)
from tests.fakes.transcript_storage import FakeTranscriptStoragePort


def _caps(diarization: DiarizationSupport) -> TranscriptionCapabilities:
    return TranscriptionCapabilities(
        engine_id="test-engine",
        diarization=diarization,
        non_speech_classification=ClassificationSupport.UNSUPPORTED,
        max_chunk_bytes=None,
        max_chunk_duration_s=None,
    )


class TestValidateCompatibility:
    """The pure helper rejects MULTI when diarization is not AVAILABLE."""

    @pytest.mark.parametrize(
        "diarization",
        [DiarizationSupport.UNSUPPORTED, DiarizationSupport.REQUIRES_SETUP],
        ids=["unsupported", "requires-setup"],
    )
    def test_multi_raises_when_diarization_unavailable(
        self, diarization: DiarizationSupport
    ) -> None:
        with pytest.raises(DiarizationUnsupported, match="diarization"):
            _validate_compatibility(diarization, SpeakerMode.MULTI)

    def test_multi_succeeds_when_diarization_available(self) -> None:
        _validate_compatibility(DiarizationSupport.AVAILABLE, SpeakerMode.MULTI)

    @pytest.mark.parametrize(
        "diarization",
        list(DiarizationSupport),
        ids=[v.value for v in DiarizationSupport],
    )
    def test_single_always_succeeds(
        self, diarization: DiarizationSupport
    ) -> None:
        _validate_compatibility(diarization, SpeakerMode.SINGLE)


class TestAdmitJobCapabilities:
    """admit_job() validates engine/speaker-mode compatibility before storage."""

    def test_incompatible_combination_rejects_before_storage(
        self, tmp_path: Path
    ) -> None:
        """6.3: MULTI + UNSUPPORTED raises and never calls create_job."""
        storage = FakeTranscriptStoragePort(tmp_path)

        with pytest.raises(DiarizationUnsupported, match="diarization"):
            admit_job(
                engine=EngineChoice.LOCAL,
                speaker_mode=SpeakerMode.MULTI,
                storage=storage,
                capabilities=lambda _e: _caps(DiarizationSupport.UNSUPPORTED),
            )

        assert storage.list_jobs() == ()

    def test_requires_setup_also_rejects_before_storage(
        self, tmp_path: Path
    ) -> None:
        """6.3 (triangulation): REQUIRES_SETUP treated identically to UNSUPPORTED."""
        storage = FakeTranscriptStoragePort(tmp_path)

        with pytest.raises(DiarizationUnsupported, match="diarization"):
            admit_job(
                engine=EngineChoice.LOCAL,
                speaker_mode=SpeakerMode.MULTI,
                storage=storage,
                capabilities=lambda _e: _caps(DiarizationSupport.REQUIRES_SETUP),
            )

        assert storage.list_jobs() == ()

    def test_compatible_combination_admitted(self, tmp_path: Path) -> None:
        """6.5: MULTI + AVAILABLE succeeds, job stored."""
        storage = FakeTranscriptStoragePort(tmp_path)

        job = admit_job(
            engine=EngineChoice.LOCAL,
            speaker_mode=SpeakerMode.MULTI,
            storage=storage,
            capabilities=lambda _e: _caps(DiarizationSupport.AVAILABLE),
        )

        assert job.speaker_mode is SpeakerMode.MULTI
        assert job.state is JobState.PENDING
        assert len(storage.list_jobs()) == 1

    def test_no_capabilities_skips_validation(self, tmp_path: Path) -> None:
        """6.4 backward compat: capabilities=None skips the check entirely."""
        storage = FakeTranscriptStoragePort(tmp_path)

        job = admit_job(
            engine=EngineChoice.LOCAL,
            speaker_mode=SpeakerMode.MULTI,
            storage=storage,
        )

        assert job.speaker_mode is SpeakerMode.MULTI
        assert len(storage.list_jobs()) == 1

    @pytest.mark.parametrize(
        "diarization",
        list(DiarizationSupport),
        ids=[v.value for v in DiarizationSupport],
    )
    def test_single_always_accepted_with_capabilities(
        self, tmp_path: Path, diarization: DiarizationSupport
    ) -> None:
        """6.7: SINGLE mode always passes regardless of diarization."""
        storage = FakeTranscriptStoragePort(tmp_path)

        job = admit_job(
            engine=EngineChoice.LOCAL,
            speaker_mode=SpeakerMode.SINGLE,
            storage=storage,
            capabilities=lambda _e: _caps(diarization),
        )

        assert job.speaker_mode is SpeakerMode.SINGLE
        assert len(storage.list_jobs()) == 1


def _fake_chunk() -> AudioChunk:
    return AudioChunk(
        job_id=JobId("00000000000000000000000000"),
        index=0,
        path=Path("/tmp/chunk.flac"),
        start_s=0.0,
        end_s=10.0,
        size_bytes=1024,
    )


def _multi_request() -> TranscriptionRequest:
    return TranscriptionRequest(language="es", speaker_mode=SpeakerMode.MULTI, timeout_s=60.0)


def _single_request() -> TranscriptionRequest:
    return TranscriptionRequest(language="es", speaker_mode=SpeakerMode.SINGLE, timeout_s=60.0)


class TestPortLevelDefense:
    """Port-level defense-in-depth: fakes refuse MULTI when diarization != AVAILABLE.

    This is the second line of defence — admission should have caught it first —
    but a job that somehow reaches dispatch with an incompatible combination
    must still fail at the port, not produce unlabeled output.
    """

    def test_fake_transcription_port_refuses_multi(self) -> None:
        """6.13: FakeTranscriptionPort (UNSUPPORTED) raises on MULTI."""
        port = FakeTranscriptionPort()

        with pytest.raises(DiarizationUnsupported, match="diarization"):
            port.transcribe(_fake_chunk(), _multi_request())

    def test_non_classifying_fake_refuses_multi(self) -> None:
        """6.13: NonClassifyingFakeTranscriptionPort (UNSUPPORTED) raises on MULTI."""
        port = NonClassifyingFakeTranscriptionPort()

        with pytest.raises(DiarizationUnsupported, match="diarization"):
            port.transcribe(_fake_chunk(), _multi_request())

    def test_fake_transcription_port_accepts_single(self) -> None:
        """6.15: SINGLE mode always accepted at port level."""
        port = FakeTranscriptionPort()
        result = port.transcribe(_fake_chunk(), _single_request())
        assert len(result) > 0

    def test_non_classifying_fake_accepts_single(self) -> None:
        """6.15: SINGLE mode always accepted at port level."""
        port = NonClassifyingFakeTranscriptionPort()
        result = port.transcribe(_fake_chunk(), _single_request())
        assert len(result) > 0
