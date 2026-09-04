"""Fakes conforming to TranscriptionPort.

Two of them, because the port has two independent capability axes and a single
fake cannot represent both sides of either. `FakeTranscriptionPort` stands for a
well-behaved engine that classifies; `NonClassifyingFakeTranscriptionPort` stands
for one that cannot, and must say so rather than assert speech.

Both use `_validate_compatibility` from the use-case layer for port-level
defense-in-depth — the same single definition of compatibility that admission
uses, closing the spec's "single definition of compatibility" requirement.
"""

import re
from collections.abc import Callable
from dataclasses import replace

from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.domain.errors import DiarizationUnsupported, TranscriptionFailed
from onevoicecut.domain.jobs import SpeakerMode
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment, WordTiming
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DiarizationSupport,
    TranscriptionCapabilities,
    WordTimingSupport,
)
from onevoicecut.ports.transcription import TranscriptionRequest
from onevoicecut.usecases.admit_job import _validate_compatibility

Script = tuple[tuple[str, SegmentKind], ...]

_DEFAULT_SCRIPT: Script = (("hola mundo", SegmentKind.SPEECH),)

# Trailing whitespace is captured with the word so a join is lossless.
_WORD = re.compile(r"\S+\s*")


def _lay_out(script: Script, chunk: AudioChunk) -> tuple[TranscriptSegment, ...]:
    """Spread the scripted lines evenly across the chunk, keeping end_s > start_s.

    Times are CHUNK-LOCAL, per the port invariant.
    """
    duration = chunk.end_s - chunk.start_s
    step = duration / len(script)
    return tuple(
        TranscriptSegment(
            start_s=i * step,
            end_s=(i + 1) * step,
            text=text,
            speaker=None,
            confidence=0.99,
            kind=kind,
        )
        for i, (text, kind) in enumerate(script)
    )


class FakeTranscriptionPort:
    """Declares AVAILABLE classification; emits exactly what its script says."""

    def __init__(self, script: Script | None = None) -> None:
        self._script: Script = script if script is not None else _DEFAULT_SCRIPT

    def capabilities(self) -> TranscriptionCapabilities:
        return TranscriptionCapabilities(
            engine_id="fake-asr",
            diarization=DiarizationSupport.UNSUPPORTED,
            non_speech_classification=ClassificationSupport.AVAILABLE,
            word_timing=WordTimingSupport.UNSUPPORTED,
            max_chunk_bytes=None,
            max_chunk_duration_s=None,
        )

    def transcribe(
        self, chunk: AudioChunk, request: TranscriptionRequest
    ) -> tuple[TranscriptSegment, ...]:
        _validate_compatibility(
            self.capabilities().diarization, request.speaker_mode
        )
        return _lay_out(self._script, chunk)


class DiarizingFakeTranscriptionPort:
    """The third double: an engine that *can* satisfy speaker mode.

    The other two reject `MULTI`, which is right for what they stand for but makes
    the accepted path untestable — a use case that silently dropped speaker mode
    would pass every test written against an engine that refuses it anyway.
    """

    def capabilities(self) -> TranscriptionCapabilities:
        return TranscriptionCapabilities(
            engine_id="diarizing-fake-asr",
            diarization=DiarizationSupport.AVAILABLE,
            non_speech_classification=ClassificationSupport.AVAILABLE,
            word_timing=WordTimingSupport.UNSUPPORTED,
            max_chunk_bytes=None,
            max_chunk_duration_s=None,
        )

    def transcribe(
        self, chunk: AudioChunk, request: TranscriptionRequest
    ) -> tuple[TranscriptSegment, ...]:
        speaker = "SPEAKER_00" if request.speaker_mode is SpeakerMode.MULTI else None
        return tuple(
            replace(segment, speaker=speaker)
            for segment in _lay_out(_DEFAULT_SCRIPT, chunk)
        )


class FlakyFakeTranscriptionPort:
    """Fails on chosen chunks, a chosen number of times each.

    One double covers both failure isolation and retry, because they are the same
    engine seen over different numbers of attempts: `{84: 99}` is a chunk that
    never recovers, `{84: 1}` is the transient cloud error that succeeds on the
    second try. Recording every attempt is what lets a test assert that a retry
    re-ran *only* the failed chunk.
    """

    def __init__(
        self,
        failures: dict[int, int] | None = None,
        *,
        error: Callable[[str], Exception] = TranscriptionFailed,
    ) -> None:
        self._remaining = dict(failures or {})
        self._error = error
        self.attempts: list[int] = []
        self.requests: list[TranscriptionRequest] = []

    def capabilities(self) -> TranscriptionCapabilities:
        return TranscriptionCapabilities(
            engine_id="flaky-fake-asr",
            diarization=DiarizationSupport.UNSUPPORTED,
            non_speech_classification=ClassificationSupport.AVAILABLE,
            word_timing=WordTimingSupport.UNSUPPORTED,
            max_chunk_bytes=None,
            max_chunk_duration_s=None,
        )

    def transcribe(
        self, chunk: AudioChunk, request: TranscriptionRequest
    ) -> tuple[TranscriptSegment, ...]:
        self.attempts.append(chunk.index)
        self.requests.append(request)
        remaining = self._remaining.get(chunk.index, 0)
        if remaining > 0:
            self._remaining[chunk.index] = remaining - 1
            raise self._error(f"engine failed on chunk {chunk.index}")
        return _lay_out(_DEFAULT_SCRIPT, chunk)


class NonClassifyingFakeTranscriptionPort:
    """Stands for an engine with no VAD or hallucination control.

    It transcribes, but it cannot tell the spoken message from a song, so every
    segment it returns is UNCERTAIN. It must never emit SPEECH — that would be
    asserting a fact it never established.
    """

    def __init__(self, texts: tuple[str, ...] = ("texto sin verificar",)) -> None:
        self._texts = texts

    def capabilities(self) -> TranscriptionCapabilities:
        return TranscriptionCapabilities(
            engine_id="unclassifying-fake-asr",
            diarization=DiarizationSupport.UNSUPPORTED,
            non_speech_classification=ClassificationSupport.UNSUPPORTED,
            word_timing=WordTimingSupport.UNSUPPORTED,
            max_chunk_bytes=None,
            max_chunk_duration_s=None,
        )

    def transcribe(
        self, chunk: AudioChunk, request: TranscriptionRequest
    ) -> tuple[TranscriptSegment, ...]:
        _validate_compatibility(
            self.capabilities().diarization, request.speaker_mode
        )
        script: Script = tuple((t, SegmentKind.UNCERTAIN) for t in self._texts)
        return _lay_out(script, chunk)


class WordTimingFakeTranscriptionPort:
    """Declares AVAILABLE word timing and produces one entry per word.

    The fifth fake, and the third capability axis to need one: a fake that could
    only answer `UNSUPPORTED` would let the whole word-timing path ship with the
    supporting branch never executed.

    Word text carries its own trailing whitespace, so `"".join(w.text)`
    reconstructs the segment exactly. That losslessness is the point rather than
    a detail — captions are rendered from these, and a reconstruction that
    dropped the spaces would render a sermon as one long word.
    """

    def __init__(self, script: Script | None = None) -> None:
        self._script: Script = script if script is not None else _DEFAULT_SCRIPT

    def capabilities(self) -> TranscriptionCapabilities:
        return TranscriptionCapabilities(
            engine_id="word-timing-fake-asr",
            diarization=DiarizationSupport.UNSUPPORTED,
            non_speech_classification=ClassificationSupport.AVAILABLE,
            word_timing=WordTimingSupport.AVAILABLE,
            max_chunk_bytes=None,
            max_chunk_duration_s=None,
        )

    def transcribe(
        self, chunk: AudioChunk, request: TranscriptionRequest
    ) -> tuple[TranscriptSegment, ...]:
        _validate_compatibility(self.capabilities().diarization, request.speaker_mode)
        return tuple(
            replace(segment, words=_time_words(segment))
            for segment in _lay_out(self._script, chunk)
        )


def _time_words(segment: TranscriptSegment) -> tuple[WordTiming, ...]:
    """Spread the segment's words evenly across its span.

    Even spacing is exactly what a *real* adapter must never invent — it looks
    like timing and drifts with every syllable the speaker lingers on. A fake is
    the one place it is honest, because nothing here claims to have measured
    anything; what is under test is the shape and the invariants, not the
    accuracy.
    """
    words = _WORD.findall(segment.text)
    if not words:
        return ()

    span = (segment.end_s - segment.start_s) / len(words)
    return tuple(
        WordTiming(
            start_s=segment.start_s + i * span,
            end_s=segment.start_s + (i + 1) * span,
            text=word,
        )
        for i, word in enumerate(words)
    )
