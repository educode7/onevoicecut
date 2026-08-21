from dataclasses import FrozenInstanceError, fields

import pytest

from transcribe.ports.capabilities import (
    ClassificationSupport,
    DiarizationSupport,
    TranscriptionCapabilities,
)


def test_classification_support_has_exactly_two_members() -> None:
    assert {m.value for m in ClassificationSupport} == {"unsupported", "available"}


def test_capabilities_declares_non_speech_classification() -> None:
    caps = TranscriptionCapabilities(
        engine_id="fake",
        diarization=DiarizationSupport.UNSUPPORTED,
        non_speech_classification=ClassificationSupport.AVAILABLE,
        max_chunk_bytes=None,
        max_chunk_duration_s=None,
    )
    assert caps.non_speech_classification is ClassificationSupport.AVAILABLE


def test_non_speech_classification_has_no_default() -> None:
    """No adapter may omit its classification declaration.

    Unlike `TranscriptSegment.kind`, which defaults to the safe answer, this field
    is required: an adapter that never states whether it can tell speech from music
    is a gap the admission check cannot reason about.
    """
    field = next(
        f for f in fields(TranscriptionCapabilities) if f.name == "non_speech_classification"
    )
    import dataclasses

    assert field.default is dataclasses.MISSING
    assert field.default_factory is dataclasses.MISSING


def test_classification_is_independent_of_diarization() -> None:
    """The two axes do not partition adapters the same way. Never infer one from the other."""
    diarizes_but_cannot_classify = TranscriptionCapabilities(
        engine_id="cloud-diarizing",
        diarization=DiarizationSupport.AVAILABLE,
        non_speech_classification=ClassificationSupport.UNSUPPORTED,
        max_chunk_bytes=25_000_000,
        max_chunk_duration_s=None,
    )
    classifies_but_cannot_diarize = TranscriptionCapabilities(
        engine_id="local-vad",
        diarization=DiarizationSupport.UNSUPPORTED,
        non_speech_classification=ClassificationSupport.AVAILABLE,
        max_chunk_bytes=None,
        max_chunk_duration_s=None,
    )
    assert diarizes_but_cannot_classify.diarization is DiarizationSupport.AVAILABLE
    assert (
        diarizes_but_cannot_classify.non_speech_classification
        is ClassificationSupport.UNSUPPORTED
    )
    assert classifies_but_cannot_diarize.diarization is DiarizationSupport.UNSUPPORTED
    assert (
        classifies_but_cannot_diarize.non_speech_classification
        is ClassificationSupport.AVAILABLE
    )


def test_capabilities_holds_exactly_five_fields() -> None:
    caps = TranscriptionCapabilities(
        engine_id="fake",
        diarization=DiarizationSupport.UNSUPPORTED,
        non_speech_classification=ClassificationSupport.UNSUPPORTED,
        max_chunk_bytes=None,
        max_chunk_duration_s=None,
    )
    assert caps.engine_id == "fake"
    assert caps.diarization is DiarizationSupport.UNSUPPORTED
    assert caps.non_speech_classification is ClassificationSupport.UNSUPPORTED
    assert caps.max_chunk_bytes is None
    assert caps.max_chunk_duration_s is None
    assert {f.name for f in fields(TranscriptionCapabilities)} == {
        "engine_id",
        "diarization",
        "non_speech_classification",
        "max_chunk_bytes",
        "max_chunk_duration_s",
    }


def test_capabilities_with_planning_limits() -> None:
    caps = TranscriptionCapabilities(
        engine_id="cloud-fake",
        diarization=DiarizationSupport.AVAILABLE,
        non_speech_classification=ClassificationSupport.UNSUPPORTED,
        max_chunk_bytes=25_000_000,
        max_chunk_duration_s=600.0,
    )
    assert caps.max_chunk_bytes == 25_000_000
    assert caps.max_chunk_duration_s == 600.0


def test_capabilities_is_frozen() -> None:
    caps = TranscriptionCapabilities(
        engine_id="fake",
        diarization=DiarizationSupport.UNSUPPORTED,
        non_speech_classification=ClassificationSupport.UNSUPPORTED,
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
