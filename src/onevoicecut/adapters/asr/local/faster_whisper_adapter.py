"""The local ASR engine: faster-whisper behind `TranscriptionPort`.

The engine loads in the constructor, not on the first chunk. The resolver builds
adapters at job resolution precisely so a missing resource is an error before the
run starts; a lazily-loaded model would move "these weights are not on this
machine" three hours into a multi-hour job, after the operator walked away. It
costs one eager load per job, which is the same load the first chunk would pay.

Two capabilities are declared UNSUPPORTED here and stay that way until their own
slice ships. Diarization lands in 9a, voice-activity classification in 7a-iii.
Declaring either before it exists is the failure this port's capability axes were
built to prevent, because the transcript that comes back looks correct.

`timeout_s` is accepted and deliberately not honoured. CTranslate2 inference is
uninterruptible from Python once it enters the C++ decode loop, so there is no
in-call budget this adapter can enforce — enforcement belongs to the supervisory
watchdog that kills the worker process from outside (slice 7b-i). Pretending to
honour it here would be a timeout that never fires.
"""

import math

from faster_whisper import WhisperModel

from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.domain.errors import DomainError, EngineUnavailable, TranscriptionFailed
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DiarizationSupport,
    TranscriptionCapabilities,
)
from onevoicecut.ports.transcription import TranscriptionRequest
from onevoicecut.usecases.admit_job import _validate_compatibility

ENGINE_NAME = "faster-whisper"


class FasterWhisperTranscriber:
    """A local, offline Whisper decode. Nothing leaves the machine."""

    def __init__(
        self,
        model_size: str,
        *,
        device: str = "auto",
        compute_type: str = "default",
        download_root: str | None = None,
    ) -> None:
        """`model_size` has no default on purpose.

        It decides both transcript quality and hours of runtime, and it is
        recorded on every chunk result as provenance. A default here would make
        that choice invisible at the one place it is actually made.
        """
        self._model_size = model_size
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

    def capabilities(self) -> TranscriptionCapabilities:
        return TranscriptionCapabilities(
            engine_id=f"{ENGINE_NAME}:{self._model_size}",
            diarization=DiarizationSupport.UNSUPPORTED,
            non_speech_classification=ClassificationSupport.UNSUPPORTED,
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
            segments, _info = self._model.transcribe(
                str(chunk.path), language=request.language
            )
            # The engine returns a generator; decode failures surface while it is
            # being drained, so materialise inside the guard, not outside it.
            return tuple(
                TranscriptSegment(
                    start_s=float(segment.start),
                    end_s=float(segment.end),
                    text=str(segment.text).strip(),
                    speaker=None,
                    # avg_logprob is the mean token log-probability; exponentiating
                    # it recovers the geometric mean probability, which is a real
                    # measurement rather than a score invented to fill the field.
                    confidence=math.exp(float(segment.avg_logprob)),
                    # Never SPEECH while classification is UNSUPPORTED: without a
                    # voice-activity filter this adapter has established nothing
                    # about whether the audio was the message or a song.
                    kind=SegmentKind.UNCERTAIN,
                )
                for segment in segments
            )
        except DomainError:
            raise
        except Exception as error:
            raise TranscriptionFailed(
                f"the local {ENGINE_NAME} engine failed on chunk {chunk.index}: "
                f"{error}"
            ) from error
