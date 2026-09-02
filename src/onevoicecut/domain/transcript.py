"""Structured transcript entities — the canonical internal representation.

The delivered `.txt` artifact is NEVER the source of truth; it is derived
from this structured form.
"""

from dataclasses import dataclass
from enum import StrEnum

from onevoicecut.domain.ids import JobId

UNCERTAIN_MARKER = "[?] "


class SegmentKind(StrEnum):
    """What a segment's audio actually is.

    Source footage mixes the speaker with a singer and with background music,
    so this is a routine property of the input, not an error condition.
    """

    SPEECH = "speech"  # the spoken message
    MUSIC = "music"  # singing or instrumental — never part of the message
    UNCERTAIN = "uncertain"  # the adapter did not, or could not, classify


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    start_s: float
    end_s: float
    text: str
    speaker: str | None
    confidence: float | None
    # Defaults to the SAFE answer, not the common one: an adapter that never
    # classifies must not accidentally assert that its output is the message.
    kind: SegmentKind = SegmentKind.UNCERTAIN


@dataclass(frozen=True, slots=True)
class Transcript:
    job_id: JobId
    segments: tuple[TranscriptSegment, ...]
    engine_id: str
    diarized: bool
    language: str = "es"


def without_music(
    segments: tuple[TranscriptSegment, ...],
) -> tuple[TranscriptSegment, ...]:
    """Drop sung and instrumental audio. Never the message, for any consumer.

    This is the one rule every message-facing consumer shares. What each does
    with `UNCERTAIN` on top of it is their own policy — see `speech_segments`
    and `render_message_text`, which deliberately answer that differently.
    """
    return tuple(s for s in segments if s.kind is not SegmentKind.MUSIC)


def speech_segments(
    segments: tuple[TranscriptSegment, ...],
) -> tuple[TranscriptSegment, ...]:
    """Strictly confirmed speech: no music, and no unverified audio either.

    The selector for consumers that cannot safely absorb unverified text — an
    LLM will not honour an inline marker the way a human reader does. Whether
    map-reduce windowing (slice 10a) uses this or the laxer export policy is a
    decision that belongs to that slice, taken against the empty-summary risk
    described in `render_message_text`.
    """
    return tuple(s for s in without_music(segments) if s.kind is SegmentKind.SPEECH)


def render_message_text(transcript: Transcript) -> str:
    """Derive the plain-text message from the structured transcript.

    `MUSIC` is dropped: sung lyrics are not the message. `UNCERTAIN` is kept but
    marked — dropping it would render an all-uncertain transcript (exactly what a
    non-classifying adapter produces) as an empty file, turning a multi-hour run
    into zero bytes. Marking keeps the text usable without ever presenting
    unverified audio as confirmed message.

    The marking rule is fixed per kind, never decided per segment: the same kind
    always renders the same way regardless of its neighbours.

    Segments with no text are ranges, not lines. A classifying adapter reports
    every non-speech range it kept out of its decode, so those ranges stay
    addressable in the source footage — they carry timestamps and nothing else.
    Rendering them would print a marker that marks nothing, once per silence, for
    the length of a three-hour recording.
    """
    return "\n".join(
        segment.text
        if segment.kind is SegmentKind.SPEECH
        else f"{UNCERTAIN_MARKER}{segment.text}"
        for segment in without_music(transcript.segments)
        if segment.text
    )
