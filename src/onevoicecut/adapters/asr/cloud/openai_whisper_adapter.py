"""The cloud ASR engine: OpenAI's transcription API behind `TranscriptionPort`.

The counterpart to the local adapter, and it diverges from it on three axes that
are worth stating rather than discovering.

**It cannot classify, and says so.** The API applies its own voice-activity
handling server-side and exposes no control over it. The local adapter needed two
detectors — a voice-activity pass and the decoder — to tell MUSIC from UNCERTAIN,
because a hole one found and the other did not is a real disagreement. This
adapter has neither, so every segment comes back `UNCERTAIN`: it has established
nothing about whether it heard the preacher or the worship band, and `SPEECH` is
a claim it has not earned. It emits that even for a segment the API reports a
`no_speech_prob` of 0.01 on — Whisper reports exactly that over singing, which is
this project's normal input, and `speech_segments` selects the LLM's window on
precisely this field.

**It cannot diarize, ever.** Not "not yet", the way the local adapter waits for
slice 9a — the API returns no speaker labels and offers no way to ask for them.

**It can honour `timeout_s`, and the local adapter cannot.** CTranslate2's decode
loop is uninterruptible from Python, so locally the supervisory watchdog is the
only enforcement there is. An HTTP call has a budget the client enforces itself,
which turns the watchdog from the sole backstop into the second one.

Two refusals happen before the request rather than after it, because both are
knowable in advance and both otherwise cost a full upload per chunk on a job that
has thousands: a speaker-mode job, and a chunk over the documented 25 MB cap.

The key is a constructor argument, not an environment read. The adapter knows
only the *name* of the variable, for its own error message; the composition root
reads it. That is the same split the local adapter makes with
`ONEVOICECUT_LOCAL_DEVICE`, and it is what keeps an adapter from depending on the
runtime package that wires it.
"""

import math
from pathlib import Path
from typing import Any

import httpx

from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.domain.errors import (
    ChunkTimeout,
    ChunkTooLarge,
    EngineUnavailable,
    TranscriptionFailed,
)
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DiarizationSupport,
    TranscriptionCapabilities,
)
from onevoicecut.ports.transcription import TranscriptionRequest
from onevoicecut.usecases.admit_job import _validate_compatibility

ENGINE_NAME = "openai-whisper"

# `whisper-1` rather than a newer transcription model on purpose: it is the one
# that still supports `verbose_json`, and therefore the only one that returns
# per-segment timestamps. The port's central promise is timestamps, so a model
# that answers with a bare string cannot satisfy it however good the text is.
DEFAULT_MODEL = "whisper-1"
RESPONSE_FORMAT = "verbose_json"

DEFAULT_BASE_URL = "https://api.openai.com/v1"
TRANSCRIPTIONS_PATH = "/audio/transcriptions"

# The provider's documented per-request cap. The planner reads this through
# `capabilities()` and sizes chunks against it, so a wrong number here is not a
# wrong declaration — it is a plan whose every chunk the API refuses.
MAX_REQUEST_BYTES = 25_000_000

# Named in the refusal, so the message carries its own remedy. Defined here
# rather than imported from `runtime/` because an adapter must not depend on the
# composition root; the resolver reads the variable, this only knows its name.
CLOUD_API_KEY_ENV = "CLOUD_ASR_API_KEY"

# Never "not yet", and never a function of configuration: this API returns no
# speaker labels and offers no way to ask for them. A module constant rather than
# a literal inside `capabilities()` so the composition root can state it without
# constructing an adapter — which is what lets the admission guard refuse a
# speaker-mode job before extraction rather than three hours into it.
DIARIZATION = DiarizationSupport.UNSUPPORTED

# The second axis, and the one with teeth: an engine that cannot tell the sermon
# from the song filters to nothing when MAP windows are built from confirmed
# speech. Stated here so admission can refuse such a job before three hours of
# transcription rather than deliver a blank summary afterwards.
CLASSIFICATION = ClassificationSupport.UNSUPPORTED

# What a chunk gets when the job set no per-chunk budget. `None` would mean a
# hung socket holds a worker open until the watchdog kills the process minutes
# later, having produced nothing — a ceiling nobody chose still beats no ceiling.
FALLBACK_TIMEOUT_S = 900.0
# Reaching the host is not the same as transcribing thirty minutes of audio, and
# a dead host should not consume a chunk's whole budget before saying so.
CONNECT_TIMEOUT_S = 10.0

# The pipeline normalizes every chunk to 16 kHz mono FLAC, but the adapter is
# told a path rather than a format, so the type is derived rather than assumed.
# The provider infers the codec from the filename, so this must stay in step
# with what `adapters/ffmpeg/argv.py` writes.
_CONTENT_TYPES = {
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}
_DEFAULT_CONTENT_TYPE = "application/octet-stream"

_REDACTED = "[redacted]"


class OpenAiWhisperTranscriber:
    """A billed, remote transcription. Audio leaves the machine."""

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        """The key is checked here, before the job, not on the first request.

        The resolver builds adapters at job resolution precisely so a missing
        resource fails while the operator is still watching. A key validated on
        first use would surface after extraction and planning have already run —
        on a three-hour recording, long after everyone walked away.
        """
        if api_key is None or not api_key.strip():
            raise EngineUnavailable(
                f"the cloud {ENGINE_NAME} engine has no API key: set "
                f"{CLOUD_API_KEY_ENV}. Engine choice is per job and is never "
                f"substituted."
            )
        self._api_key = api_key.strip()
        self._model = model
        self._client = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            transport=transport,
        )

    def capabilities(self) -> TranscriptionCapabilities:
        return TranscriptionCapabilities(
            engine_id=f"{ENGINE_NAME}:{self._model}",
            # Never "not yet". The API exposes no diarization at all, so this
            # declaration is what makes admission reject a speaker-mode job up
            # front rather than deliver a plausible unlabelled transcript.
            diarization=DIARIZATION,
            non_speech_classification=CLASSIFICATION,
            max_chunk_bytes=MAX_REQUEST_BYTES,
            # No documented duration cap — the byte cap binds first, and the
            # planner already bounds stride by target_chunk_seconds.
            max_chunk_duration_s=None,
        )

    def transcribe(
        self, chunk: AudioChunk, request: TranscriptionRequest
    ) -> tuple[TranscriptSegment, ...]:
        _validate_compatibility(self.capabilities().diarization, request.speaker_mode)
        if chunk.size_bytes > MAX_REQUEST_BYTES:
            raise ChunkTooLarge(
                f"chunk {chunk.index} is {chunk.size_bytes} bytes, over the "
                f"{ENGINE_NAME} per-request cap of {MAX_REQUEST_BYTES}"
            )

        payload = self._post(chunk, request)
        duration_s = chunk.end_s - chunk.start_s
        return _read_segments(payload, duration_s, chunk.index)

    def close(self) -> None:
        """Release the connection pool. One adapter is built per job, so a pool
        left open outlives the job that opened it."""
        self._client.close()

    def _post(self, chunk: AudioChunk, request: TranscriptionRequest) -> Any:
        budget = request.timeout_s if request.timeout_s is not None else FALLBACK_TIMEOUT_S
        try:
            audio = chunk.path.read_bytes()
        except OSError as error:
            raise TranscriptionFailed(
                f"chunk {chunk.index} could not be read for submission to "
                f"{ENGINE_NAME}: {error}"
            ) from error

        try:
            response = self._client.post(
                TRANSCRIPTIONS_PATH,
                files={
                    "file": (chunk.path.name, audio, _content_type(chunk.path)),
                },
                data={
                    "model": self._model,
                    # Never left to detection. Source audio is Spanish only, and
                    # one noisy chunk of a sermon auto-detected as Portuguese
                    # would come back as fluent nonsense rather than as an error.
                    "language": request.language,
                    "response_format": RESPONSE_FORMAT,
                },
                timeout=httpx.Timeout(
                    budget, connect=min(CONNECT_TIMEOUT_S, budget)
                ),
            )
        except httpx.TimeoutException as error:
            # Deliberately narrower than TranscriptionFailed, which it derives
            # from: this is the one failure worth not retrying, because a retry
            # mostly spends the same budget again.
            raise ChunkTimeout(
                f"chunk {chunk.index} exceeded its {budget:.0f}s budget against "
                f"{ENGINE_NAME}"
            ) from error
        except httpx.HTTPError as error:
            raise TranscriptionFailed(
                f"chunk {chunk.index} could not reach {ENGINE_NAME}: "
                f"{self._redact(str(error))}"
            ) from error

        if response.status_code != httpx.codes.OK:
            # The body is included because it is the only place the provider
            # says *why*, and redacted because a 401 quotes the credential it
            # rejected — and this message is written to the job record.
            raise TranscriptionFailed(
                f"chunk {chunk.index} was refused by {ENGINE_NAME} with status "
                f"{response.status_code}: {self._redact(response.text[:500])}"
            )

        try:
            return response.json()
        except ValueError as error:
            # A 200 carrying HTML is what a proxy or a captive portal returns.
            raise TranscriptionFailed(
                f"chunk {chunk.index} got an unreadable response from "
                f"{ENGINE_NAME}: {error}"
            ) from error

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, _REDACTED)


def _content_type(path: Path) -> str:
    return _CONTENT_TYPES.get(path.suffix.lower(), _DEFAULT_CONTENT_TYPE)


def _read_segments(
    payload: Any, duration_s: float, chunk_index: int
) -> tuple[TranscriptSegment, ...]:
    """Turn the provider's answer into the port's, or refuse to.

    The absent-`segments` case is not defensive noise. A model that does not
    support `verbose_json`, or a `response_format` silently ignored, returns a
    perfectly valid `{"text": ...}` — real output, no timestamps. That is the one
    thing this port cannot deliver without, so it is a failure rather than a
    transcript with the times filled in from nowhere.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("segments"), list):
        raise TranscriptionFailed(
            f"chunk {chunk_index} came back from {ENGINE_NAME} without "
            f"timestamped segments; the response carried no {RESPONSE_FORMAT} body"
        )

    try:
        segments = [
            _to_segment(raw, duration_s)
            for raw in payload["segments"]
            if isinstance(raw, dict)
        ]
    except (TypeError, ValueError, KeyError) as error:
        raise TranscriptionFailed(
            f"chunk {chunk_index} came back from {ENGINE_NAME} with a segment "
            f"this adapter cannot read: {error}"
        ) from error

    # The stitcher folds these in order and dedupes by overlap; out-of-order
    # segments would corrupt that fold without ever raising.
    return tuple(sorted(segments, key=lambda s: s.start_s))


def _to_segment(raw: dict[str, Any], duration_s: float) -> TranscriptSegment:
    """One provider segment, clamped into the window the port promises.

    Times arrive relative to the submitted file, which *is* the chunk, so
    chunk-local is a promise to keep rather than one to build. The clamp is for
    the edge: the API rounds its final segment against the decoded file, which
    can run a fraction past the planned window on the last chunk of a track.
    """
    end_s = min(max(float(raw["end"]), 0.0), duration_s)
    start_s = min(max(float(raw["start"]), 0.0), end_s)

    return TranscriptSegment(
        start_s=start_s,
        end_s=end_s,
        text=str(raw.get("text", "")).strip(),
        # Never invented. The API returns no speaker labels, and a namespaced
        # placeholder would read downstream exactly like a real one.
        speaker=None,
        # Exponentiating the mean token log-probability recovers the geometric
        # mean probability — the same real measurement the local adapter
        # reports, so the two engines' confidences mean the same thing.
        confidence=math.exp(float(raw["avg_logprob"]))
        if "avg_logprob" in raw
        else None,
        # The whole classification axis, in one line. See the module docstring:
        # this adapter declares UNSUPPORTED, and this is what honouring that
        # declaration looks like.
        kind=SegmentKind.UNCERTAIN,
    )
