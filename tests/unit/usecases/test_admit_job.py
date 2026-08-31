"""Tests for the admit_job use case — engine/speaker-mode compatibility validation.

These tests exercise the pure `_validate_compatibility` helper and, later,
the `admit_job()` integration with a capabilities callable. The helper is
module-level in `usecases/admit_job.py` and is the single definition of
engine/speaker-mode compatibility shared by admission and port-level defense.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.errors import DiarizationUnsupported
from onevoicecut.domain.jobs import EngineChoice, JobState, SpeakerMode
from onevoicecut.ports.capabilities import DiarizationSupport, TranscriptionCapabilities
from onevoicecut.usecases.admit_job import _validate_compatibility, admit_job
from tests.fakes.transcript_storage import FakeTranscriptStoragePort


def _caps(diarization: DiarizationSupport) -> TranscriptionCapabilities:
    return TranscriptionCapabilities(
        engine_id="test-engine",
        diarization=diarization,
        non_speech_classification="unsupported",
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
