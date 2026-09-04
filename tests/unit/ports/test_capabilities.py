from dataclasses import MISSING, FrozenInstanceError, fields

import pytest

from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DetectionSupport,
    DiarizationSupport,
    TrackerCapabilities,
    TranscriptionCapabilities,
    WordTimingSupport,
)


def test_classification_support_has_exactly_two_members() -> None:
    assert {m.value for m in ClassificationSupport} == {"unsupported", "available"}


def test_capabilities_declares_non_speech_classification() -> None:
    caps = TranscriptionCapabilities(
        engine_id="fake",
        diarization=DiarizationSupport.UNSUPPORTED,
        non_speech_classification=ClassificationSupport.AVAILABLE,
        word_timing=WordTimingSupport.UNSUPPORTED,
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
        word_timing=WordTimingSupport.UNSUPPORTED,
        max_chunk_bytes=25_000_000,
        max_chunk_duration_s=None,
    )
    classifies_but_cannot_diarize = TranscriptionCapabilities(
        engine_id="local-vad",
        diarization=DiarizationSupport.UNSUPPORTED,
        non_speech_classification=ClassificationSupport.AVAILABLE,
        word_timing=WordTimingSupport.UNSUPPORTED,
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


def test_capabilities_holds_exactly_six_fields() -> None:
    """A field here is a question every adapter must answer, so growing this set
    is a decision rather than a convenience. Slice 11b-i added the third
    capability axis, `word_timing`; this failing is how that stayed deliberate."""
    caps = TranscriptionCapabilities(
        engine_id="fake",
        diarization=DiarizationSupport.UNSUPPORTED,
        non_speech_classification=ClassificationSupport.UNSUPPORTED,
        word_timing=WordTimingSupport.UNSUPPORTED,
        max_chunk_bytes=None,
        max_chunk_duration_s=None,
    )
    assert caps.engine_id == "fake"
    assert caps.diarization is DiarizationSupport.UNSUPPORTED
    assert caps.non_speech_classification is ClassificationSupport.UNSUPPORTED
    assert caps.word_timing is WordTimingSupport.UNSUPPORTED
    assert caps.max_chunk_bytes is None
    assert caps.max_chunk_duration_s is None
    assert {f.name for f in fields(TranscriptionCapabilities)} == {
        "engine_id",
        "diarization",
        "non_speech_classification",
        "word_timing",
        "max_chunk_bytes",
        "max_chunk_duration_s",
    }


def test_capabilities_with_planning_limits() -> None:
    caps = TranscriptionCapabilities(
        engine_id="cloud-fake",
        diarization=DiarizationSupport.AVAILABLE,
        non_speech_classification=ClassificationSupport.UNSUPPORTED,
        word_timing=WordTimingSupport.UNSUPPORTED,
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
        word_timing=WordTimingSupport.UNSUPPORTED,
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


def test_detection_support_has_exactly_three_members() -> None:
    """Three and not two, because the operator remediation genuinely differs:
    "choose another tracker" versus "install the vision extras and let the
    weights download". The same argument `DiarizationSupport` made."""
    assert {m.value for m in DetectionSupport} == {
        "unsupported",
        "requires_setup",
        "available",
    }


def test_detection_support_duplicates_diarizations_vocabulary_deliberately() -> None:
    """Not a shared `SupportLevel`. One vocabulary across independent axes is the
    first step toward inferring one from another, which every axis in this system
    forbids, so the duplication is intentional and this pins it.

    **The separation is type-level, not runtime**, and that is worth knowing:
    both are `StrEnum`, so `DetectionSupport.AVAILABLE == DiarizationSupport
    .AVAILABLE` is genuinely `True` — they compare by value. mypy is what
    rejects passing one where the other is expected, which is exactly the
    guarantee design.md claims for keeping them separate. A runtime assertion
    here would be asserting the opposite of the truth.
    """
    assert {m.value for m in DetectionSupport} == {m.value for m in DiarizationSupport}


def test_tracker_capabilities_declares_an_id_and_a_detection_level() -> None:
    caps = TrackerCapabilities(
        tracker_id="fake-tracker", detection=DetectionSupport.AVAILABLE
    )

    assert caps.tracker_id == "fake-tracker"
    assert caps.detection is DetectionSupport.AVAILABLE


def test_tracker_capabilities_holds_exactly_two_fields() -> None:
    """A field here is a question every tracker adapter must answer, so growing
    the set is a decision rather than a convenience."""
    assert {f.name for f in fields(TrackerCapabilities)} == {"tracker_id", "detection"}


def test_tracker_capabilities_has_no_defaults() -> None:
    """Same rule the transcription axes follow: an adapter that never states
    whether it can detect at all is a gap the dispatch check cannot reason
    about."""
    for field in fields(TrackerCapabilities):
        assert field.default is MISSING
        assert field.default_factory is MISSING


def test_tracker_capabilities_is_frozen() -> None:
    caps = TrackerCapabilities(
        tracker_id="fake-tracker", detection=DetectionSupport.AVAILABLE
    )

    with pytest.raises(FrozenInstanceError):
        caps.tracker_id = "other"  # type: ignore[misc]
