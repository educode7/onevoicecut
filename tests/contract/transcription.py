"""The contract every `TranscriptionPort` must satisfy, whatever is behind it.

One body, run against every adapter — the fakes in the default suite, the real
local engine under `localmodel`, the cloud engine when it lands. Adapters are
structural (`typing.Protocol`), so nothing forces them to agree; this is what
does.

The interesting part is that the contract does not assume one behaviour. It
asserts the **relationship between what an adapter declares and what it does**,
which is the invariant the capability axes exist for:

- An adapter that declares `diarization=UNSUPPORTED` MUST refuse a speaker-mode
  job. Returning unlabelled segments instead is the dangerous failure, because
  the transcript looks perfectly fine.
- An adapter that declares `non_speech_classification=UNSUPPORTED` MUST NOT emit
  `SPEECH`. It has established nothing about whether the audio was the message
  or the song before it, and `UNCERTAIN` is the honest answer.

Both axes are independent, and neither may be inferred from the other. So the
same test body reads `capabilities()` and holds the adapter to its own claim
rather than to a hard-coded expectation — which is also what keeps this file
correct when a future slice flips one of those declarations to AVAILABLE.
"""

import pytest

from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.domain.errors import DiarizationUnsupported
from onevoicecut.domain.jobs import SpeakerMode
from onevoicecut.domain.transcript import SegmentKind
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DiarizationSupport,
    WordTimingSupport,
)
from onevoicecut.ports.transcription import TranscriptionPort, TranscriptionRequest

# Deliberately not zero. A chunk carved out of hour two of a sermon starts at
# 7200 s absolute, and an adapter that returned absolute times would still look
# correct against a chunk starting at 0.
CHUNK_START_S = 120.0


def single_speaker() -> TranscriptionRequest:
    return TranscriptionRequest(
        language="es", speaker_mode=SpeakerMode.SINGLE, timeout_s=None
    )


def multi_speaker() -> TranscriptionRequest:
    return TranscriptionRequest(
        language="es", speaker_mode=SpeakerMode.MULTI, timeout_s=None
    )


class TranscriptionPortContract:
    """Subclass it and supply the two fixtures. Every test comes with it.

    A base class rather than a parametrized fixture because the real engine's
    case has to carry a `localmodel` mark that the fakes must not: markers apply
    per module, so the split has to be per subclass.
    """

    @pytest.fixture
    def port(self) -> TranscriptionPort:
        raise NotImplementedError("supply the adapter under test")

    @pytest.fixture
    def chunk(self) -> AudioChunk:
        raise NotImplementedError("supply an audio chunk the adapter can read")

    def test_it_declares_an_engine_identity(self, port: TranscriptionPort) -> None:
        """`engine_id` is persisted on every chunk result as provenance. Empty
        makes "which engine produced this transcript" unanswerable afterwards."""
        assert port.capabilities().engine_id

    def test_returned_times_are_chunk_local(
        self, port: TranscriptionPort, chunk: AudioChunk
    ) -> None:
        """The port's central promise, and the one whose violation is silent.

        Absolute times would stitch into a transcript whose timestamps drift
        further from reality with every chunk — and every clip cut from it would
        be aimed at the wrong moment of the sermon.
        """
        duration_s = chunk.end_s - chunk.start_s

        for segment in port.transcribe(chunk, single_speaker()):
            assert 0.0 <= segment.start_s <= segment.end_s <= duration_s

    def test_segments_are_ordered_and_non_negative(
        self, port: TranscriptionPort, chunk: AudioChunk
    ) -> None:
        """The stitcher folds these in order and dedupes by overlap; segments
        that ran backwards would corrupt the fold silently."""
        segments = port.transcribe(chunk, single_speaker())

        assert [s.start_s for s in segments] == sorted(s.start_s for s in segments)

    def test_it_never_claims_speech_it_cannot_verify(
        self, port: TranscriptionPort, chunk: AudioChunk
    ) -> None:
        """The classification axis, held to the adapter's own declaration.

        An engine with no voice-activity filter has established nothing about
        whether it heard the preacher or the worship band. It may still emit
        text — it may even hallucinate it — but calling that text SPEECH is the
        claim it has not earned, and the `.txt` export and the LLM windows both
        select on exactly that field.
        """
        capabilities = port.capabilities()
        segments = port.transcribe(chunk, single_speaker())

        if capabilities.non_speech_classification is ClassificationSupport.UNSUPPORTED:
            assert all(s.kind is SegmentKind.UNCERTAIN for s in segments)
        else:
            assert all(s.kind in set(SegmentKind) for s in segments)

    def test_it_honours_its_own_diarization_declaration(
        self, port: TranscriptionPort, chunk: AudioChunk
    ) -> None:
        """The diarization axis, same shape, independent of the one above.

        Silently returning unlabelled segments for a multi-speaker job is the
        failure worth naming: the operator asked to tell two voices apart, got a
        transcript that looks complete, and nothing anywhere says the question
        was ignored.
        """
        capabilities = port.capabilities()

        if capabilities.diarization is DiarizationSupport.AVAILABLE:
            assert port.transcribe(chunk, multi_speaker()) is not None
        else:
            with pytest.raises(DiarizationUnsupported):
                port.transcribe(chunk, multi_speaker())

    def test_a_declared_byte_cap_is_a_real_number_or_absent(
        self, port: TranscriptionPort
    ) -> None:
        """`None` means "no cap"; a number means the planner must respect it.
        Zero or negative would be neither, and the planner would silently
        produce chunks nothing could accept."""
        capabilities = port.capabilities()

        for cap in (capabilities.max_chunk_bytes, capabilities.max_chunk_duration_s):
            assert cap is None or cap > 0

    def test_it_honours_its_own_word_timing_declaration(
        self, port: TranscriptionPort, chunk: AudioChunk
    ) -> None:
        """The third axis, held to the same shape as the other two.

        An adapter that cannot time words must return none — never an even
        division of the segment across them. Evenly spaced words are the
        dangerous answer here for the same reason unlabelled diarization is:
        they look exactly like timing, they render as captions, and they drift
        further from the audio with every syllable the speaker lingers on.

        Where timing *is* declared, reconstructing the segment from its words
        must be lossless. Captions are rendered from these, so a reconstruction
        that dropped the spaces would render a sermon as one long word.
        """
        capabilities = port.capabilities()
        segments = port.transcribe(chunk, single_speaker())

        if capabilities.word_timing is WordTimingSupport.UNSUPPORTED:
            assert all(segment.words == () for segment in segments)
            return

        for segment in segments:
            if not segment.text:
                # A filtered non-speech range is a range, not words.
                assert segment.words == ()
                continue
            assert segment.words
            assert "".join(word.text for word in segment.words) == segment.text
            for word in segment.words:
                assert segment.start_s <= word.start_s <= word.end_s <= segment.end_s
