from dataclasses import FrozenInstanceError

import pytest

from transcribe.domain.ids import make_job_id
from transcribe.domain.transcript import Transcript, TranscriptSegment

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")


def test_transcript_segment_allows_null_speaker() -> None:
    segment = TranscriptSegment(
        start_s=0.0, end_s=2.5, text="hola", speaker=None, confidence=None
    )
    assert segment.speaker is None


def test_transcript_segment_is_frozen() -> None:
    segment = TranscriptSegment(
        start_s=0.0, end_s=2.5, text="hola", speaker=None, confidence=None
    )
    with pytest.raises(FrozenInstanceError):
        segment.text = "adios"  # type: ignore[misc]


def test_transcript_default_language_is_spanish() -> None:
    transcript = Transcript(
        job_id=JOB_ID, segments=(), engine_id="fake", diarized=False
    )
    assert transcript.language == "es"


def test_transcript_is_frozen() -> None:
    transcript = Transcript(
        job_id=JOB_ID, segments=(), engine_id="fake", diarized=False
    )
    with pytest.raises(FrozenInstanceError):
        transcript.diarized = True  # type: ignore[misc]
