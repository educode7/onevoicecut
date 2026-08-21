from dataclasses import FrozenInstanceError

import pytest

from transcribe.domain.ids import make_job_id
from transcribe.domain.transcript import (
    SegmentKind,
    Transcript,
    TranscriptSegment,
    render_message_text,
    speech_segments,
    without_music,
)

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")


def _segment(
    text: str, kind: SegmentKind = SegmentKind.SPEECH, start_s: float = 0.0
) -> TranscriptSegment:
    return TranscriptSegment(
        start_s=start_s,
        end_s=start_s + 1.0,
        text=text,
        speaker=None,
        confidence=None,
        kind=kind,
    )


def test_transcript_segment_allows_null_speaker() -> None:
    segment = TranscriptSegment(
        start_s=0.0, end_s=2.5, text="hola", speaker=None, confidence=None
    )
    assert segment.speaker is None


def test_segment_kind_has_exactly_three_members() -> None:
    assert {m.value for m in SegmentKind} == {"speech", "music", "uncertain"}


def test_segment_kind_defaults_to_uncertain() -> None:
    """The default is the SAFE answer, not the common one.

    An adapter that forgets to classify must not accidentally assert that its
    output is the spoken message.
    """
    segment = TranscriptSegment(
        start_s=0.0, end_s=2.5, text="hola", speaker=None, confidence=None
    )
    assert segment.kind is SegmentKind.UNCERTAIN


def test_speech_segments_selects_only_speech() -> None:
    segments = (
        _segment("el mensaje", SegmentKind.SPEECH, 0.0),
        _segment("la la la", SegmentKind.MUSIC, 1.0),
        _segment("quizas", SegmentKind.UNCERTAIN, 2.0),
    )
    assert tuple(s.text for s in speech_segments(segments)) == ("el mensaje",)


def test_without_music_keeps_speech_and_uncertain() -> None:
    """The rule every message-facing consumer shares, before its own UNCERTAIN policy."""
    segments = (
        _segment("el mensaje", SegmentKind.SPEECH, 0.0),
        _segment("la la la", SegmentKind.MUSIC, 1.0),
        _segment("quizas", SegmentKind.UNCERTAIN, 2.0),
    )
    assert tuple(s.text for s in without_music(segments)) == ("el mensaje", "quizas")


def test_speech_segments_preserves_order_and_identity() -> None:
    first = _segment("uno", SegmentKind.SPEECH, 0.0)
    second = _segment("dos", SegmentKind.SPEECH, 5.0)
    selected = speech_segments((first, _segment("x", SegmentKind.MUSIC, 1.0), second))
    assert selected == (first, second)


def test_message_text_excludes_music() -> None:
    transcript = Transcript(
        job_id=JOB_ID,
        segments=(
            _segment("hoy quiero contarles algo", SegmentKind.SPEECH, 0.0),
            _segment("y volare sin ti", SegmentKind.MUSIC, 1.0),
            _segment("como les decia", SegmentKind.SPEECH, 2.0),
        ),
        engine_id="fake",
        diarized=False,
    )
    text = render_message_text(transcript)
    assert "volare sin ti" not in text
    assert text == "hoy quiero contarles algo\ncomo les decia"


def test_message_text_marks_uncertain_rather_than_dropping_it() -> None:
    """Excluding UNCERTAIN would make an all-uncertain transcript export empty.

    A non-classifying adapter (a raw cloud Whisper adapter, for instance) returns
    every segment as UNCERTAIN. Dropping those would turn a three-hour run into a
    zero-byte file. The text is kept, marked so it is never mistaken for confirmed
    message.
    """
    transcript = Transcript(
        job_id=JOB_ID,
        segments=(
            _segment("texto sin verificar", SegmentKind.UNCERTAIN, 0.0),
            _segment("mas texto", SegmentKind.UNCERTAIN, 1.0),
        ),
        engine_id="unclassifying-fake",
        diarized=False,
    )
    text = render_message_text(transcript)
    assert "texto sin verificar" in text
    assert text.splitlines() == [
        "[?] texto sin verificar",
        "[?] mas texto",
    ]


def test_message_text_marking_rule_is_consistent_not_per_segment() -> None:
    """Same kind always renders the same way, regardless of its neighbours."""
    mixed = Transcript(
        job_id=JOB_ID,
        segments=(
            _segment("confirmado", SegmentKind.SPEECH, 0.0),
            _segment("dudoso", SegmentKind.UNCERTAIN, 1.0),
            _segment("cancion", SegmentKind.MUSIC, 2.0),
        ),
        engine_id="fake",
        diarized=False,
    )
    assert render_message_text(mixed).splitlines() == [
        "confirmado",
        "[?] dudoso",
    ]


def test_message_text_of_music_only_transcript_is_empty() -> None:
    transcript = Transcript(
        job_id=JOB_ID,
        segments=(_segment("la la la", SegmentKind.MUSIC, 0.0),),
        engine_id="fake",
        diarized=False,
    )
    assert render_message_text(transcript) == ""


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
