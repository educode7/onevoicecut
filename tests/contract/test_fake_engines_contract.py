"""The three test doubles, held to the same contract as a real engine.

This is not ceremony. Every use-case test in the suite is written against these
fakes, so if a fake drifts from the port contract, the tests it backs are proving
something about a thing that does not exist. The doubles are also where each
capability combination is *only* reachable in the default run — the real engines
that declare AVAILABLE need weights or a paid API.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.chunking import AudioChunk
from onevoicecut.domain.ids import make_job_id
from onevoicecut.ports.transcription import TranscriptionPort
from tests.contract.transcription import CHUNK_START_S, TranscriptionPortContract
from tests.fakes.transcription import (
    DiarizingFakeTranscriptionPort,
    FakeTranscriptionPort,
    NonClassifyingFakeTranscriptionPort,
)

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
CHUNK_SECONDS = 3.0


@pytest.fixture
def a_chunk(tmp_path: Path) -> AudioChunk:
    """The fakes never open it, so the bytes are irrelevant — but the path must
    exist, because a real adapter subclassing this contract will read it."""
    path = tmp_path / "chunk.flac"
    path.write_bytes(b"not really audio")
    return AudioChunk(
        job_id=JOB_ID,
        index=0,
        path=path,
        start_s=CHUNK_START_S,
        end_s=CHUNK_START_S + CHUNK_SECONDS,
        size_bytes=path.stat().st_size,
    )


class TestClassifyingFake(TranscriptionPortContract):
    """Declares classification AVAILABLE, diarization UNSUPPORTED."""

    @pytest.fixture
    def port(self) -> TranscriptionPort:
        return FakeTranscriptionPort()

    @pytest.fixture
    def chunk(self, a_chunk: AudioChunk) -> AudioChunk:
        return a_chunk


class TestDiarizingFake(TranscriptionPortContract):
    """The only double that may accept a speaker-mode job — and the only reason
    the AVAILABLE branch of the diarization contract is exercised at all."""

    @pytest.fixture
    def port(self) -> TranscriptionPort:
        return DiarizingFakeTranscriptionPort()

    @pytest.fixture
    def chunk(self, a_chunk: AudioChunk) -> AudioChunk:
        return a_chunk


class TestNonClassifyingFake(TranscriptionPortContract):
    """Declares classification UNSUPPORTED, so the contract holds it to
    UNCERTAIN — the same rule the real local engine is under today."""

    @pytest.fixture
    def port(self) -> TranscriptionPort:
        return NonClassifyingFakeTranscriptionPort()

    @pytest.fixture
    def chunk(self, a_chunk: AudioChunk) -> AudioChunk:
        return a_chunk
