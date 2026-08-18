from dataclasses import FrozenInstanceError

import pytest

from transcribe.ports.capabilities import DiarizationSupport, TranscriptionCapabilities


def test_capabilities_holds_exactly_four_fields() -> None:
    caps = TranscriptionCapabilities(
        engine_id="fake",
        diarization=DiarizationSupport.UNSUPPORTED,
        max_chunk_bytes=None,
        max_chunk_duration_s=None,
    )
    assert caps.engine_id == "fake"
    assert caps.diarization is DiarizationSupport.UNSUPPORTED
    assert caps.max_chunk_bytes is None
    assert caps.max_chunk_duration_s is None


def test_capabilities_with_planning_limits() -> None:
    caps = TranscriptionCapabilities(
        engine_id="cloud-fake",
        diarization=DiarizationSupport.AVAILABLE,
        max_chunk_bytes=25_000_000,
        max_chunk_duration_s=600.0,
    )
    assert caps.max_chunk_bytes == 25_000_000
    assert caps.max_chunk_duration_s == 600.0


def test_capabilities_is_frozen() -> None:
    caps = TranscriptionCapabilities(
        engine_id="fake",
        diarization=DiarizationSupport.UNSUPPORTED,
        max_chunk_bytes=None,
        max_chunk_duration_s=None,
    )
    with pytest.raises(FrozenInstanceError):
        caps.engine_id = "other"  # type: ignore[misc]


def test_diarization_support_has_exactly_three_members() -> None:
    assert {m.value for m in DiarizationSupport} == {
        "unsupported",
        "requires_setup",
        "available",
    }
