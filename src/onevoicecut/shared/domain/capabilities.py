"""Adapter capability declaration.

A field belongs here only if a use case must read it to (a) reject or warn about
a job before work starts, or (b) compute a chunk plan.

The "or warn" half is a deliberate, recorded widening of the original rule (see
design.md): rejection and warning are the same structural read — the use case
must consult the adapter before work begins — and they differ only in severity.
"""

from dataclasses import dataclass
from enum import StrEnum


class DiarizationSupport(StrEnum):
    UNSUPPORTED = "unsupported"  # engine can never diarize (e.g. OpenAI Whisper API)
    REQUIRES_SETUP = "requires_setup"  # engine could, this install cannot yet
    AVAILABLE = "available"


class ClassificationSupport(StrEnum):
    """Whether the engine can tell the spoken message from music and singing.

    An independent axis from diarization: an engine may support either, both, or
    neither. Never infer one from the other.
    """

    UNSUPPORTED = "unsupported"  # no VAD/hallucination control; output is all UNCERTAIN
    AVAILABLE = "available"


class WordTimingSupport(StrEnum):
    """Whether the engine can say when each word was spoken.

    Two states, not three. Diarization has a `REQUIRES_SETUP` because it is an
    install with a licence behind it; word timing is a decoder flag — an engine
    either produces the timings or it does not.

    An engine that cannot must return no words rather than dividing the segment
    evenly across them. Evenly spaced words look completely plausible: they read
    as timing, they render as captions, and they drift further from the audio
    with every syllable the speaker lingers on.
    """

    UNSUPPORTED = "unsupported"
    AVAILABLE = "available"


class DetectionSupport(StrEnum):
    """Whether this build can locate the preacher inside the wide shot.

    Three members and not two, because the operator's remediation genuinely
    differs: "choose another tracker" versus "install the vision extras and let
    the weights download". The same argument `DiarizationSupport` made, and the
    same conclusion.

    **The duplication with `DiarizationSupport` is deliberate.** A shared
    `SupportLevel` would be shorter and would couple independent axes to one
    vocabulary, which is the first step toward inferring one from another — the
    thing every axis in this system forbids. Two small enums cost six lines and
    make "never infer one axis from the other" a type-level fact.
    """

    UNSUPPORTED = "unsupported"  # no vision adapter in this build
    REQUIRES_SETUP = "requires_setup"  # adapter present, weights or extras absent
    AVAILABLE = "available"


class RenderSupport(StrEnum):
    """Whether this build can turn a trajectory into a file.

    Three members, mirroring `DiarizationSupport`, because the remediation
    genuinely differs: "this build ships no renderer" and "ffmpeg is not on this
    machine" send an operator to two different places. Collapsing them would
    send them to the wrong one.
    """

    UNSUPPORTED = "unsupported"  # no render adapter in this build
    REQUIRES_SETUP = "requires_setup"  # adapter present, ffmpeg absent
    AVAILABLE = "available"


@dataclass(frozen=True, slots=True)
class RenderCapabilities:
    """What a renderer declares before a clip is dispatched to it.

    `max_clip_seconds` is the render-resource-exhaustion guard, declared rather
    than enforced only inside the adapter — a bound nobody can read is a bound
    only the adapter can apply, and slice 13c reads this one to decide how much
    footage a tracker is asked to sample. `None` is unbounded, the same way the
    transcription caps express it; zero would be a limit that refuses every clip.
    """

    renderer_id: str
    rendering: RenderSupport
    max_clip_seconds: float | None


@dataclass(frozen=True, slots=True)
class TrackerCapabilities:
    """What a subject tracker declares before a clip is dispatched.

    Its own type rather than a field on `TranscriptionCapabilities`: an install
    can transcribe and not track, or track and not transcribe, and one record
    covering both would make a missing tracker read as a missing engine.
    """

    tracker_id: str
    detection: DetectionSupport


@dataclass(frozen=True, slots=True)
class TranscriptionCapabilities:
    engine_id: str
    diarization: DiarizationSupport
    # Required, with no default: an adapter that never states whether it can tell
    # speech from music is a gap the admission check cannot reason about.
    non_speech_classification: ClassificationSupport
    # Required for the same reason, on a third and independent axis. Never infer
    # one axis from another: an engine can time words and not classify music.
    word_timing: WordTimingSupport
    max_chunk_bytes: int | None
    max_chunk_duration_s: float | None


@dataclass(frozen=True, slots=True)
class DeclaredSupport:
    """The capability axes an install can state **without building an engine**.

    Exactly the two the admission guard reads, and exactly the two both adapters
    can answer from constants and a `find_spec`. The rest of
    `TranscriptionCapabilities` — the engine id, the byte caps — needs a
    constructed adapter, and the web process must not construct one inside an
    HTTP request.

    That distinction is not cosmetic: promising the wider type is what left the
    admission guard disconnected from the composition root for three slices.
    """

    diarization: DiarizationSupport
    non_speech_classification: ClassificationSupport
    word_timing: WordTimingSupport
