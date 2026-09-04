"""Word-level timing: a third capability axis, declared before it is needed.

Rev 4 put vertical clip rendering in scope, and a rendered clip wants captions
that land on the word rather than on the sentence. A segment already knows when
it starts and ends; what it cannot say is when *"hermanos"* was said inside it.

The axis is added the way the other two were, and for the same reason. An adapter
that cannot produce word timing must **say so and return nothing**, never invent
an even division of the segment across its words. Evenly spaced words look
completely plausible — they read as timing, they render as captions, and they
drift further from the audio with every syllable the speaker lingers on. That is
the same silent failure as undeclared diarization and as `SPEECH` claimed without
a voice-activity pass, on a third and independent axis.

`words` defaults to `()` so nineteen existing construction sites compile
unchanged; `word_timing` is **required with no default**, so no adapter can ship
without answering. Those two look inconsistent and are the same rule from
opposite ends: a *segment* may legitimately have no words, but an *engine* may
never be silent about whether it can produce them.
"""

import dataclasses

import pytest

from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment, WordTiming
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DiarizationSupport,
    TranscriptionCapabilities,
    WordTimingSupport,
)


class TestTheWordTimingEntity:
    def test_it_is_frozen_like_every_other_domain_entity(self) -> None:
        word = WordTiming(start_s=1.0, end_s=1.4, text="hermanos")

        with pytest.raises(dataclasses.FrozenInstanceError):
            word.start_s = 2.0  # type: ignore[misc]

    def test_it_carries_its_own_span_and_text(self) -> None:
        word = WordTiming(start_s=1.0, end_s=1.4, text="hermanos")

        assert (word.start_s, word.end_s, word.text) == (1.0, 1.4, "hermanos")

    def test_it_is_slotted(self) -> None:
        """Every domain entity is. A three-hour sermon is tens of thousands of
        words, and this is the first entity that scales with word count rather
        than with segment count."""
        assert not hasattr(WordTiming(start_s=0.0, end_s=1.0, text="a"), "__dict__")


class TestSegmentsCarryWords:
    def test_the_default_is_no_words(self) -> None:
        """Which is what makes this additive. Nineteen construction sites across
        the suite predate the question and must keep compiling."""
        segment = TranscriptSegment(
            start_s=0.0,
            end_s=1.0,
            text="hola",
            speaker=None,
            confidence=0.9,
            kind=SegmentKind.SPEECH,
        )

        assert segment.words == ()

    def test_words_round_trip(self) -> None:
        words = (WordTiming(0.0, 0.4, "hola"), WordTiming(0.4, 1.0, "hermanos"))
        segment = TranscriptSegment(
            start_s=0.0,
            end_s=1.0,
            text="hola hermanos",
            speaker=None,
            confidence=0.9,
            kind=SegmentKind.SPEECH,
            words=words,
        )

        assert segment.words == words


class TestTheCapabilityAxis:
    def test_it_has_exactly_two_states(self) -> None:
        """Unlike diarization, which has three. There is no "requires setup"
        here: word timing is a decoder flag, not an install with a licence —
        an engine either produces the timings or it does not."""
        assert {member.value for member in WordTimingSupport} == {
            "unsupported",
            "available",
        }

    def test_the_capability_field_has_no_default(self) -> None:
        """Same rule `non_speech_classification` already follows. An adapter
        that never states whether it can time words is a gap the caller cannot
        reason about, and the safe reading of silence is not obvious enough to
        encode as a default."""
        field = TranscriptionCapabilities.__dataclass_fields__["word_timing"]

        assert field.default is dataclasses.MISSING
        assert field.default_factory is dataclasses.MISSING

    def test_it_is_independent_of_the_other_two(self) -> None:
        """Never infer one axis from another — the rule the capability ports
        were built around, now over three axes rather than two. An engine can
        time words and not classify music, or classify and not diarize."""
        capabilities = TranscriptionCapabilities(
            engine_id="fake",
            diarization=DiarizationSupport.UNSUPPORTED,
            non_speech_classification=ClassificationSupport.UNSUPPORTED,
            word_timing=WordTimingSupport.AVAILABLE,
            max_chunk_bytes=None,
            max_chunk_duration_s=None,
        )

        assert capabilities.word_timing is WordTimingSupport.AVAILABLE
