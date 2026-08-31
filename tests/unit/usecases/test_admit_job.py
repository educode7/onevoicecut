"""Tests for the admit_job use case — engine/speaker-mode compatibility validation.

These tests exercise the pure `_validate_compatibility` helper and, later,
the `admit_job()` integration with a capabilities callable. The helper is
module-level in `usecases/admit_job.py` and is the single definition of
engine/speaker-mode compatibility shared by admission and port-level defense.
"""

import pytest

from onevoicecut.domain.errors import DiarizationUnsupported
from onevoicecut.domain.jobs import EngineChoice, SpeakerMode
from onevoicecut.ports.capabilities import DiarizationSupport, TranscriptionCapabilities
from onevoicecut.usecases.admit_job import _validate_compatibility


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
