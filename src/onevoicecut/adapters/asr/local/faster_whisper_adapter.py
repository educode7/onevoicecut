"""The local ASR engine: faster-whisper behind `TranscriptionPort`.

The engine loads in the constructor, not on the first chunk. The resolver builds
adapters at job resolution precisely so a missing resource is an error before the
run starts; a lazily-loaded model would move "these weights are not on this
machine" three hours into a multi-hour job, after the operator walked away. It
costs one eager load per job, which is the same load the first chunk would pay.

Diarization is no longer a flat UNSUPPORTED: `diarization.py` probes what this
install can actually support, and the answer is REQUIRES_SETUP until the package
and a licence token are both present. UNSUPPORTED would be a claim about the
engine rather than about the machine, and the engine is not the problem. The
weaker claim still refuses a speaker-mode job — only AVAILABLE admits one — which
is the failure this axis was built to prevent, because the transcript that comes
back from a silently unlabelled run looks correct.

Content classification, the independent second axis, is now AVAILABLE: a Silero
voice-activity pass runs over the chunk, and the decode runs behind the same
filter plus the guards that break Whisper's degenerate repetition loops. The
filtering is only half of it. Non-speech audio is removed from the *decode*, so
the decoder has nothing to hallucinate over, and then put *back into the result*
as MUSIC-classified ranges carrying their timestamps. Dropping it instead would
satisfy "no fabricated speech" while destroying every musical range the operator
might cut a clip from — which is why the spec states classification and
non-discarding as two separate requirements.

`timeout_s` is accepted and deliberately not honoured. CTranslate2 inference is
uninterruptible from Python once it enters the C++ decode loop, so there is no
in-call budget this adapter can enforce — enforcement belongs to the supervisory
watchdog that kills the worker process from outside (slice 7b-i). Pretending to
honour it here would be a timeout that never fires.
"""

import math

import numpy as np
from faster_whisper import WhisperModel, decode_audio
from faster_whisper.transcribe import Segment
from faster_whisper.vad import VadOptions, get_speech_timestamps

from onevoicecut.adapters.asr.local.declarations import (
    CLASSIFICATION,
    WORD_TIMING,
    diarization_support,
    is_installed,
)
from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.domain.errors import DomainError, EngineUnavailable, TranscriptionFailed
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment
from onevoicecut.ports.capabilities import (
    TranscriptionCapabilities,
    WordTimingSupport,
)
from onevoicecut.ports.transcription import TranscriptionRequest
from onevoicecut.usecases.admit_job import _validate_compatibility

ENGINE_NAME = "faster-whisper"

# The rate Whisper and Silero both work at. Decoding once here and handing the
# samples to both is what keeps the voice-activity pass and the decode looking at
# byte-identical audio; two decodes of the same file could disagree at the edges.
SAMPLE_RATE = 16000

# Whisper's own defaults, restated because they are load-bearing here rather than
# incidental. `no_speech_prob` above this is the engine saying it heard no speech
# in a window it nonetheless produced text for — the signal this adapter reads to
# refuse promoting that text to SPEECH.
NO_SPEECH_THRESHOLD = 0.6
# A decode whose text compresses better than this is the degenerate repetition
# loop ("gracias por ver el video" a hundred times), not a transcript.
COMPRESSION_RATIO_THRESHOLD = 2.4

# Below this, a hole between two decoded segments is boundary rounding, not audio
# that went missing. Reporting those as ranges would bury the real ones.
MIN_REPORTED_GAP_S = 0.5

# What counts as a degenerate decoder loop rather than a sentence. Measured, not
# guessed: across 30 provoking fixtures every loop `tiny` produced ran 4 to 9
# words with exactly one distinct word, while `compression_ratio` never once
# reached Whisper's own 2.4 threshold (it topped out at 2.33). The guard nominally
# responsible for breaking these catches none of them, which is why this exists.
MIN_LOOP_WORDS = 4
MAX_LOOP_DISTINCT_WORDS = 2
_LOOP_PUNCTUATION = ",.;:!?¡¿-—…\"'"

_Range = tuple[float, float]

# Named in the refusal below, so the message carries its own remedy. Defined here
# rather than imported from `runtime/` because an adapter must not depend on the
# composition root; the worker reads the variable, this only knows its name.
LOCAL_DEVICE_ENV = "ONEVOICECUT_LOCAL_DEVICE"

# One second of silence. Enough to force a full encoder pass — Whisper pads any
# input to its thirty-second window — and short enough that the proof costs
# milliseconds against a job measured in hours.
_PROOF_SECONDS = 1


class FasterWhisperTranscriber:
    """A local, offline Whisper decode. Nothing leaves the machine."""

    def __init__(
        self,
        model_size: str,
        *,
        device: str = "auto",
        compute_type: str = "default",
        download_root: str | None = None,
        hf_token: str | None = None,
    ) -> None:
        """`model_size` has no default on purpose.

        It decides both transcript quality and hours of runtime, and it is
        recorded on every chunk result as provenance. A default here would make
        that choice invisible at the one place it is actually made.
        """
        self._model_size = model_size
        # Held, never read from the environment: the composition root reads it
        # and this only knows the variable's name, for the refusal. Not verified
        # here either — see `diarization.py` on why install state is a probe and
        # not a proof.
        self._hf_token = hf_token
        try:
            self._model = WhisperModel(
                model_size,
                device=device,
                compute_type=compute_type,
                download_root=download_root,
            )
        except Exception as error:
            raise EngineUnavailable(
                f"the local {ENGINE_NAME} engine could not load model "
                f"{model_size!r}: {error}. Engine choice is per job and is never "
                f"substituted."
            ) from error
        self._prove(device)

    def _prove(self, device: str) -> None:
        """Decode one second of silence, to find out whether this device works.

        Loading the weights is not proof. CTranslate2 allocates the model on the
        selected device and returns happily, then resolves its compute libraries
        lazily on the first `encode()` — so a machine with a GPU but no usable
        cuBLAS constructs fine and dies on the first chunk. That failure is
        content-dependent, which is what makes it dangerous: a chunk the
        voice-activity filter rejects never reaches the encoder and therefore
        "succeeds", so the same build transcribes music and dies on a sermon.

        This is what the constructor already claimed to do. Now it does it.

        It never falls back to CPU. That would be the same job twenty times
        slower, chosen by nobody — the identical silent substitution the resolver
        refuses between engines, and the message says which knob to turn instead.
        """
        try:
            segments, _info = self._model.transcribe(
                np.zeros(SAMPLE_RATE * _PROOF_SECONDS, dtype=np.float32)
            )
            # The generator is where the encode actually happens; leaving it
            # undrained would prove nothing at all.
            tuple(segments)
        except Exception as error:
            raise EngineUnavailable(
                f"the local {ENGINE_NAME} engine loaded model "
                f"{self._model_size!r} on device {device!r} but cannot compute "
                f"with it: {error}. Set {LOCAL_DEVICE_ENV}=cpu to run on the "
                f"processor, or install the CUDA runtime this device needs. The "
                f"engine is never silently substituted."
            ) from error

    def capabilities(self) -> TranscriptionCapabilities:
        return TranscriptionCapabilities(
            engine_id=f"{ENGINE_NAME}:{self._model_size}",
            diarization=diarization_support(
                installed=is_installed(), token=self._hf_token
            ),
            non_speech_classification=CLASSIFICATION,
            word_timing=WORD_TIMING,
            # Both None: a local engine imposes no per-request cap the way the
            # cloud API's 25 MB limit does. It is bounded only by the machine,
            # and the planner already bounds stride by target_chunk_seconds.
            max_chunk_bytes=None,
            max_chunk_duration_s=None,
        )

    def transcribe(
        self, chunk: AudioChunk, request: TranscriptionRequest
    ) -> tuple[TranscriptSegment, ...]:
        _validate_compatibility(self.capabilities().diarization, request.speaker_mode)
        try:
            audio = decode_audio(str(chunk.path), sampling_rate=SAMPLE_RATE)
            # Never longer than the plan says this chunk is: the port promises
            # chunk-local times bounded by the chunk's own duration, and the last
            # chunk of a track can be shorter than its planned window.
            duration_s = min(len(audio) / SAMPLE_RATE, chunk.end_s - chunk.start_s)

            vad_options = VadOptions()
            speech = tuple(
                (float(r["start"]) / SAMPLE_RATE, float(r["end"]) / SAMPLE_RATE)
                for r in get_speech_timestamps(audio, vad_options)
            )

            decoded, _info = self._model.transcribe(
                audio,
                language=request.language,
                vad_filter=True,
                vad_parameters=vad_options,
                no_speech_threshold=NO_SPEECH_THRESHOLD,
                compression_ratio_threshold=COMPRESSION_RATIO_THRESHOLD,
                # Whisper conditions each window on the text it just produced. Over
                # a long musical passage that turns one invented line into a
                # self-reinforcing loop, so the carry-over is cut here.
                condition_on_previous_text=False,
            )
            # The engine returns a generator; decode failures surface while it is
            # being drained, so materialise inside the guard, not outside it.
            spoken = tuple(_classify(segment) for segment in decoded)
            return _tile(spoken, speech, duration_s)
        except DomainError:
            raise
        except Exception as error:
            raise TranscriptionFailed(
                f"the local {ENGINE_NAME} engine failed on chunk {chunk.index}: "
                f"{error}"
            ) from error


def _classify(segment: Segment) -> TranscriptSegment:
    """One decoded segment, judged on what the engine reported about it.

    `no_speech_prob` above the threshold is the engine saying it heard no speech
    in a window it nonetheless produced text for. That text is never SPEECH —
    but it stays UNCERTAIN rather than becoming MUSIC, because MUSIC is dropped
    outright by every message-facing consumer and a misjudged sentence would
    vanish from the export instead of arriving marked.

    The text itself is dropped only when both conditions hold: the engine
    declared the window non-speech *and* what it wrote there is a degenerate
    loop. Neither alone is enough. Dropping on the probability alone would
    discard real sentences the engine merely doubted; dropping on repetition
    alone would silence a preacher saying "no, no, no, no" for emphasis, which
    is speech and belongs in the transcript.
    """
    text = str(segment.text).strip()
    heard_speech = float(segment.no_speech_prob) <= NO_SPEECH_THRESHOLD

    return TranscriptSegment(
        start_s=float(segment.start),
        end_s=float(segment.end),
        # The range survives either way — it is still addressable footage. Only
        # the invented words go.
        text="" if not heard_speech and _is_degenerate_loop(text) else text,
        speaker=None,
        # avg_logprob is the mean token log-probability; exponentiating it
        # recovers the geometric mean probability, which is a real measurement
        # rather than a score invented to fill the field.
        confidence=math.exp(float(segment.avg_logprob)),
        kind=SegmentKind.SPEECH if heard_speech else SegmentKind.UNCERTAIN,
    )


def _is_degenerate_loop(text: str) -> bool:
    """Whisper writing the same word until the window runs out.

    Not a judgement about whether the audio was speech — that was already made by
    the caller. This only asks whether what came back is a decoder artefact,
    which is why it looks at the shape of the text and nothing else.
    """
    words = text.lower().translate(str.maketrans("", "", _LOOP_PUNCTUATION)).split()

    return (
        len(words) >= MIN_LOOP_WORDS
        and len(set(words)) <= MAX_LOOP_DISTINCT_WORDS
    )


def _tile(
    spoken: tuple[TranscriptSegment, ...],
    speech: tuple[_Range, ...],
    duration_s: float,
) -> tuple[TranscriptSegment, ...]:
    """Fill every hole the decode left, so the chunk comes back whole.

    The voice-activity filter keeps music out of the decoder, which is what stops
    the hallucination — but it also means the decoder returns nothing at all for
    those ranges, and a chunk of worship music would come back as an empty tuple.
    That range still exists in the source footage and still has to be addressable,
    so it is restored here with empty text and its real timestamps.

    A hole is MUSIC when the voice-activity pass found no voice in it, and
    UNCERTAIN when it did but the decoder produced no text for it anyway. The
    second case is a real disagreement between two detectors; claiming to know
    which one was right is the silent degradation this axis exists to prevent.
    """
    ordered = sorted(spoken, key=lambda s: s.start_s)
    tiled: list[TranscriptSegment] = []
    cursor = 0.0

    for segment in (*ordered, None):
        boundary = duration_s if segment is None else min(segment.start_s, duration_s)
        if boundary - cursor >= MIN_REPORTED_GAP_S:
            tiled.append(
                TranscriptSegment(
                    start_s=cursor,
                    end_s=boundary,
                    text="",
                    speaker=None,
                    confidence=None,
                    kind=(
                        SegmentKind.UNCERTAIN
                        if _overlaps(cursor, boundary, speech)
                        else SegmentKind.MUSIC
                    ),
                )
            )
        if segment is not None:
            tiled.append(segment)
            cursor = max(cursor, segment.end_s)

    return tuple(tiled)


def _overlaps(start_s: float, end_s: float, ranges: tuple[_Range, ...]) -> bool:
    return any(r_start < end_s and start_s < r_end for r_start, r_end in ranges)
