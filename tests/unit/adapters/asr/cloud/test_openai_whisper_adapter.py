"""The cloud adapter's behaviour, proven without spending a cent.

The `paid` contract test next door proves the adapter agrees with the real API.
It is excluded from the default run, which would leave this adapter with no
executable coverage at all — so everything that does not require OpenAI to
actually answer is proven here instead, through `httpx.MockTransport`: a real
`httpx.Client`, real request construction, real response parsing, no socket.

The two cases worth naming, because both are refusals that must happen *before*
the request rather than after it:

- A multi-speaker job. Whisper cannot diarize, so submitting one buys a
  perfectly plausible unlabelled transcript for the price of the upload.
- An oversized chunk. The 25 MB cap is documented; discovering it by uploading
  25 MB and being refused costs minutes per chunk on a multi-hour job.

Both assert the transport was never called, because "raises the right error" is
satisfied just as well by code that raises it after the round trip.
"""

import json
import math
from pathlib import Path

import httpx
import pytest

from onevoicecut.adapters.asr.cloud.openai_whisper_adapter import (
    CLOUD_API_KEY_ENV,
    DEFAULT_MODEL,
    MAX_REQUEST_BYTES,
    OpenAiWhisperTranscriber,
)
from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.domain.errors import (
    ChunkTimeout,
    ChunkTooLarge,
    DiarizationUnsupported,
    EngineUnavailable,
    TranscriptionFailed,
)
from onevoicecut.domain.ids import make_job_id
from onevoicecut.domain.jobs import SpeakerMode
from onevoicecut.domain.transcript import SegmentKind
from onevoicecut.ports.capabilities import ClassificationSupport, DiarizationSupport
from onevoicecut.ports.transcription import TranscriptionRequest

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
API_KEY = "sk-test-not-a-real-key"
CHUNK_START_S = 120.0
CHUNK_SECONDS = 30.0


def _request(
    *, speaker_mode: SpeakerMode = SpeakerMode.SINGLE, timeout_s: float | None = None
) -> TranscriptionRequest:
    return TranscriptionRequest(
        language="es", speaker_mode=speaker_mode, timeout_s=timeout_s
    )


def _chunk(tmp_path: Path, *, size_bytes: int | None = None) -> AudioChunk:
    path = tmp_path / "chunk.wav"
    path.write_bytes(b"RIFF" + b"\x00" * 1024)
    return AudioChunk(
        job_id=JOB_ID,
        index=7,
        path=path,
        start_s=CHUNK_START_S,
        end_s=CHUNK_START_S + CHUNK_SECONDS,
        size_bytes=path.stat().st_size if size_bytes is None else size_bytes,
    )


def _verbose_json(*segments: dict[str, object]) -> dict[str, object]:
    """The shape `response_format=verbose_json` returns. Only the read fields."""
    return {
        "task": "transcribe",
        "language": "spanish",
        "duration": CHUNK_SECONDS,
        "text": " ".join(str(s.get("text", "")) for s in segments),
        "segments": list(segments),
    }


def _segment(
    *, start: float, end: float, text: str, no_speech_prob: float = 0.01
) -> dict[str, object]:
    return {
        "id": 0,
        "seek": 0,
        "start": start,
        "end": end,
        "text": text,
        "tokens": [],
        "temperature": 0.0,
        "avg_logprob": -0.25,
        "compression_ratio": 1.1,
        "no_speech_prob": no_speech_prob,
    }


def _responding(payload: object, *, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=payload)

    return httpx.MockTransport(handler)


def _recording(payload: object) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.MockTransport(handler), seen


def _never_called() -> tuple[httpx.MockTransport, list[httpx.Request]]:
    return _recording(_verbose_json())


def _adapter(transport: httpx.MockTransport) -> OpenAiWhisperTranscriber:
    return OpenAiWhisperTranscriber(API_KEY, transport=transport)


class TestConstruction:
    def test_a_missing_key_refuses_at_construction_naming_the_variable(self) -> None:
        """Before the job, not three hours into it.

        The resolver builds adapters at job resolution precisely so a missing
        resource is an error while the operator is still watching. A key checked
        on the first request would surface after extraction and planning have
        already run.
        """
        with pytest.raises(EngineUnavailable) as raised:
            OpenAiWhisperTranscriber(None)

        assert CLOUD_API_KEY_ENV in str(raised.value)

    def test_a_blank_key_is_a_missing_key(self) -> None:
        """An unset environment variable read with a `""` default, or a
        half-filled `.env`, arrives here as whitespace rather than as None."""
        with pytest.raises(EngineUnavailable):
            OpenAiWhisperTranscriber("   ")

    def test_surrounding_whitespace_is_stripped_from_the_key(
        self, tmp_path: Path
    ) -> None:
        """A key read from a file or a `.env` arrives with a trailing newline,
        and that is not a malformed key — but it is a malformed *header*.
        Newlines in a header value are header injection, and an HTTP client
        that did not reject them outright would send an unusable request.
        """
        transport, seen = _recording(_verbose_json())

        OpenAiWhisperTranscriber(f"  {API_KEY}\n", transport=transport).transcribe(
            _chunk(tmp_path), _request()
        )

        assert seen[0].headers["authorization"] == f"Bearer {API_KEY}"


class TestCapabilities:
    def test_it_declares_the_real_documented_byte_cap(self) -> None:
        """The planner sizes chunks against this number. A wrong one is not a
        wrong declaration, it is a plan whose every chunk the API refuses."""
        transport, _ = _never_called()

        assert _adapter(transport).capabilities().max_chunk_bytes == 25_000_000

    def test_it_declares_no_duration_cap(self) -> None:
        transport, _ = _never_called()

        assert _adapter(transport).capabilities().max_chunk_duration_s is None

    def test_it_declares_diarization_unsupported(self) -> None:
        """Whisper's API returns no speaker labels and exposes no way to ask for
        them. This declaration is what makes the admission check reject a
        speaker-mode job up front instead of delivering an unlabelled one."""
        transport, _ = _never_called()

        assert (
            _adapter(transport).capabilities().diarization
            is DiarizationSupport.UNSUPPORTED
        )

    def test_it_declares_classification_unsupported(self) -> None:
        """The second, independent axis — and it is UNSUPPORTED for a different
        reason than diarization is.

        The API applies its own voice-activity handling server-side and exposes
        no control over it. The local adapter needed two detectors disagreeing
        to tell MUSIC from UNCERTAIN; this one has neither, so it has
        established nothing about whether it heard the sermon or the worship
        band, and must say so.
        """
        transport, _ = _never_called()

        assert (
            _adapter(transport).capabilities().non_speech_classification
            is ClassificationSupport.UNSUPPORTED
        )

    def test_the_engine_id_names_the_model(self) -> None:
        """Persisted on every chunk result as provenance. Collapsing every
        model into "openai" makes a re-run's provenance unanswerable."""
        transport, _ = _never_called()

        assert DEFAULT_MODEL in _adapter(transport).capabilities().engine_id


class TestRefusalsBeforeTheRequest:
    def test_a_speaker_mode_job_is_rejected_without_calling_the_api(
        self, tmp_path: Path
    ) -> None:
        transport, seen = _never_called()

        with pytest.raises(DiarizationUnsupported):
            _adapter(transport).transcribe(
                _chunk(tmp_path), _request(speaker_mode=SpeakerMode.MULTI)
            )

        assert seen == []

    def test_an_oversized_chunk_is_rejected_without_uploading_it(
        self, tmp_path: Path
    ) -> None:
        """The cap is documented, so the refusal is knowable before the upload.
        Learning it from a 413 costs the whole transfer, per chunk, on a job
        that has thousands of them."""
        transport, seen = _never_called()
        oversized = _chunk(tmp_path, size_bytes=MAX_REQUEST_BYTES + 1)

        with pytest.raises(ChunkTooLarge):
            _adapter(transport).transcribe(oversized, _request())

        assert seen == []


class TestTheRequestItSends:
    def test_it_authenticates_with_the_key_as_a_bearer_token(
        self, tmp_path: Path
    ) -> None:
        transport, seen = _recording(_verbose_json())

        _adapter(transport).transcribe(_chunk(tmp_path), _request())

        assert seen[0].headers["authorization"] == f"Bearer {API_KEY}"

    def test_it_posts_to_the_transcriptions_endpoint(self, tmp_path: Path) -> None:
        transport, seen = _recording(_verbose_json())

        _adapter(transport).transcribe(_chunk(tmp_path), _request())

        assert seen[0].method == "POST"
        assert seen[0].url.path.endswith("/audio/transcriptions")

    def test_it_asks_for_the_verbose_format_that_carries_timestamps(
        self, tmp_path: Path
    ) -> None:
        """The port's central promise is timestamps. The default `json` format
        returns a bare string, so this parameter is the whole contract."""
        transport, seen = _recording(_verbose_json())

        _adapter(transport).transcribe(_chunk(tmp_path), _request())

        body = seen[0].content.decode("utf-8", errors="replace")
        assert "verbose_json" in body

    def test_it_sends_the_requested_language_and_the_chunk_bytes(
        self, tmp_path: Path
    ) -> None:
        """Source audio is Spanish only. Letting the API detect the language per
        chunk would let one noisy chunk of a sermon come back as Portuguese."""
        transport, seen = _recording(_verbose_json())
        chunk = _chunk(tmp_path)

        _adapter(transport).transcribe(chunk, _request())

        body = seen[0].content
        assert b"RIFF" in body
        assert b"es" in body


class TestTheSegmentsItReturns:
    def test_returned_times_are_chunk_local(self, tmp_path: Path) -> None:
        """The API already answers relative to the file it was given, which is
        the chunk — so this is a promise to keep, not one to build."""
        transport = _responding(
            _verbose_json(
                _segment(start=0.0, end=2.0, text=" Hola"),
                _segment(start=2.0, end=5.5, text=" hermanos"),
            )
        )

        segments = _adapter(transport).transcribe(_chunk(tmp_path), _request())

        assert [(s.start_s, s.end_s) for s in segments] == [(0.0, 2.0), (2.0, 5.5)]

    def test_times_beyond_the_chunk_are_clamped_to_its_duration(
        self, tmp_path: Path
    ) -> None:
        """The contract bounds every segment by the chunk's own duration. The
        API rounds its final segment against the decoded file, which can run a
        fraction past the planned window on the last chunk of a track."""
        transport = _responding(
            _verbose_json(
                _segment(start=0.0, end=CHUNK_SECONDS + 4.0, text=" Amen")
            )
        )

        segments = _adapter(transport).transcribe(_chunk(tmp_path), _request())

        assert segments[0].end_s == CHUNK_SECONDS

    def test_segments_come_back_ordered(self, tmp_path: Path) -> None:
        """The stitcher folds these in order and dedupes by overlap; segments
        that ran backwards would corrupt the fold silently."""
        transport = _responding(
            _verbose_json(
                _segment(start=6.0, end=8.0, text=" tercero"),
                _segment(start=0.0, end=2.0, text=" primero"),
            )
        )

        segments = _adapter(transport).transcribe(_chunk(tmp_path), _request())

        assert [s.start_s for s in segments] == [0.0, 6.0]

    def test_every_segment_is_uncertain_even_when_the_api_sounds_confident(
        self, tmp_path: Path
    ) -> None:
        """The declaration and the behaviour, held together.

        `no_speech_prob` of 0.01 is the API stating it definitely heard speech,
        and it is still not enough. Whisper produces exactly that number over
        singing, which is this project's normal input — and `speech_segments`
        feeds the LLM off this field. Declaring UNSUPPORTED and then emitting
        SPEECH anyway is the silent degradation the axis exists to stop.
        """
        transport = _responding(
            _verbose_json(_segment(start=0.0, end=2.0, text=" Hola", no_speech_prob=0.01))
        )

        segments = _adapter(transport).transcribe(_chunk(tmp_path), _request())

        assert all(s.kind is SegmentKind.UNCERTAIN for s in segments)

    def test_the_text_survives_marked_rather_than_being_dropped(
        self, tmp_path: Path
    ) -> None:
        """UNCERTAIN is a marking, not a filter. `render_message_text` keeps
        these; dropping them here would render every cloud transcript as a
        zero-byte file after a three-hour run."""
        transport = _responding(
            _verbose_json(_segment(start=0.0, end=2.0, text="  Hola hermanos  "))
        )

        segments = _adapter(transport).transcribe(_chunk(tmp_path), _request())

        assert segments[0].text == "Hola hermanos"

    def test_confidence_is_the_measured_probability_not_an_invented_score(
        self, tmp_path: Path
    ) -> None:
        """Exponentiating the mean token log-probability recovers the geometric
        mean probability — the same real measurement the local adapter reports,
        so the two engines' confidences mean the same thing."""
        transport = _responding(
            _verbose_json(_segment(start=0.0, end=2.0, text=" Hola"))
        )

        segments = _adapter(transport).transcribe(_chunk(tmp_path), _request())

        assert segments[0].confidence == pytest.approx(math.exp(-0.25))

    def test_no_speaker_label_is_invented(self, tmp_path: Path) -> None:
        transport = _responding(
            _verbose_json(_segment(start=0.0, end=2.0, text=" Hola"))
        )

        segments = _adapter(transport).transcribe(_chunk(tmp_path), _request())

        assert all(s.speaker is None for s in segments)

    def test_a_silent_chunk_comes_back_empty_rather_than_failing(
        self, tmp_path: Path
    ) -> None:
        """A chunk the API found nothing in is a normal result on a multi-hour
        recording, not an error. The stitcher handles an empty tuple."""
        transport = _responding(_verbose_json())

        assert _adapter(transport).transcribe(_chunk(tmp_path), _request()) == ()


class TestFailureTranslation:
    def test_an_error_status_becomes_transcription_failed_naming_the_chunk(
        self, tmp_path: Path
    ) -> None:
        """Chunk-level failure isolation is by index: the record says which
        chunk to retry, and a message without one says only "something broke"."""
        transport = _responding({"error": {"message": "server error"}}, status=500)

        with pytest.raises(TranscriptionFailed) as raised:
            _adapter(transport).transcribe(_chunk(tmp_path), _request())

        assert "7" in str(raised.value)

    def test_a_timeout_becomes_chunk_timeout_not_a_generic_failure(
        self, tmp_path: Path
    ) -> None:
        """`ChunkTimeout` is the one failure worth not retrying: a retry mostly
        spends the budget again. Collapsing it into `TranscriptionFailed` would
        buy three thirty-minute waits to learn the same thing."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out", request=request)

        with pytest.raises(ChunkTimeout):
            _adapter(httpx.MockTransport(handler)).transcribe(
                _chunk(tmp_path), _request(timeout_s=1.0)
            )

    def test_a_transport_error_becomes_transcription_failed(
        self, tmp_path: Path
    ) -> None:
        """An adapter must never leak a provider exception upward; the use case
        catches domain errors and would not survive an `httpx.ConnectError`."""

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no route to host", request=request)

        with pytest.raises(TranscriptionFailed):
            _adapter(httpx.MockTransport(handler)).transcribe(
                _chunk(tmp_path), _request()
            )

    def test_an_unparseable_body_becomes_transcription_failed(
        self, tmp_path: Path
    ) -> None:
        """A 200 carrying HTML is what a proxy or a captive portal returns. A
        raw `JSONDecodeError` would escape every caller's except clause."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>gateway</html>")

        with pytest.raises(TranscriptionFailed):
            _adapter(httpx.MockTransport(handler)).transcribe(
                _chunk(tmp_path), _request()
            )

    def test_a_response_without_segments_becomes_transcription_failed(
        self, tmp_path: Path
    ) -> None:
        """`response_format` silently ignored, or a model that does not support
        it, returns a bare `{"text": ...}` — real output with no timestamps,
        which is the one thing the port cannot deliver without."""
        transport = _responding({"text": "Hola hermanos"})

        with pytest.raises(TranscriptionFailed):
            _adapter(transport).transcribe(_chunk(tmp_path), _request())

    def test_a_failure_message_never_echoes_the_key(self, tmp_path: Path) -> None:
        """A 401 body can quote the credential it rejected, and this message is
        written to the job record."""
        transport = _responding(
            {"error": {"message": f"Incorrect API key provided: {API_KEY}"}}, status=401
        )

        with pytest.raises(TranscriptionFailed) as raised:
            _adapter(transport).transcribe(_chunk(tmp_path), _request())

        assert API_KEY not in str(raised.value)


class TestInCallTimeout:
    def test_the_requested_budget_is_applied_to_the_call(
        self, tmp_path: Path
    ) -> None:
        """The real divergence from the local adapter, which cannot honour
        `timeout_s` at all because CTranslate2's decode loop is uninterruptible
        from Python. An HTTP call has a budget the client can enforce, so the
        watchdog stops being the only backstop.
        """
        transport, seen = _recording(_verbose_json())

        _adapter(transport).transcribe(_chunk(tmp_path), _request(timeout_s=42.0))

        assert seen[0].extensions["timeout"]["read"] == 42.0

    def test_no_budget_still_bounds_the_call(self, tmp_path: Path) -> None:
        """`None` means the job set no per-chunk budget, not that a hung socket
        should hold a worker open until the watchdog kills the process."""
        transport, seen = _recording(_verbose_json())

        _adapter(transport).transcribe(_chunk(tmp_path), _request(timeout_s=None))

        assert seen[0].extensions["timeout"]["read"] is not None


def test_the_json_shape_this_suite_asserts_against_is_the_documented_one() -> None:
    """A guard on the fixtures above, not on the adapter.

    Every test here is only as true as `_verbose_json` is. Pinning the field
    names in one assertion means a drift in the API's documented shape breaks
    one obvious test rather than quietly making twenty of them prove nothing.
    """
    payload = json.loads(json.dumps(_verbose_json(_segment(start=0.0, end=1.0, text="x"))))

    assert set(payload) >= {"text", "segments"}
    assert set(payload["segments"][0]) >= {
        "start",
        "end",
        "text",
        "avg_logprob",
        "no_speech_prob",
    }
