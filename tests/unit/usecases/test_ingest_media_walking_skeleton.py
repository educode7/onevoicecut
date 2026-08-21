"""Proves the fake path crosses all five layers: domain, ports, usecases,
fakes-as-adapters, and a real `.txt` file on disk.
"""

import asyncio
from pathlib import Path
from typing import AsyncIterator

from tests.fakes import DEFAULT_JOB_ID, DEFAULT_MEDIA_ID, FakePorts, build_fake_ports
from tests.fakes.transcription import (
    FakeTranscriptionPort,
    NonClassifyingFakeTranscriptionPort,
)
from transcribe.domain.jobs import EngineChoice, SpeakerMode
from transcribe.domain.transcript import SegmentKind
from transcribe.usecases.ingest_media import IngestMedia


async def _fake_upload_stream() -> AsyncIterator[bytes]:
    yield b"fake-video-bytes"


def test_ingest_media_produces_real_transcript_and_txt_export(tmp_path: Path) -> None:
    ports = build_fake_ports(root=tmp_path)
    source = asyncio.run(
        ports.media_source.store(
            DEFAULT_MEDIA_ID, "clip.mp4", _fake_upload_stream(), max_bytes=10_000
        )
    )

    use_case = IngestMedia(
        audio_extractor=ports.audio_extractor,
        transcription=ports.transcription,
        storage=ports.storage,
    )

    transcript = use_case.run(
        job_id=DEFAULT_JOB_ID,
        source=source,
        job_dir=tmp_path,
        engine=EngineChoice.LOCAL,
        speaker_mode=SpeakerMode.SINGLE,
    )

    assert transcript.job_id == DEFAULT_JOB_ID
    assert transcript.diarized is False
    assert len(transcript.segments) > 0
    assert transcript.segments[0].text == "hola mundo"

    exported_path = ports.storage.load_export_path(DEFAULT_JOB_ID)
    assert exported_path is not None
    assert exported_path.exists()
    assert exported_path.read_text(encoding="utf-8").strip() == "hola mundo"


def _run(ports: FakePorts, tmp_path: Path) -> None:
    source = asyncio.run(
        ports.media_source.store(
            DEFAULT_MEDIA_ID, "clip.mp4", _fake_upload_stream(), max_bytes=10_000
        )
    )
    IngestMedia(
        audio_extractor=ports.audio_extractor,
        transcription=ports.transcription,
        storage=ports.storage,
    ).run(
        job_id=DEFAULT_JOB_ID,
        source=source,
        job_dir=tmp_path,
        engine=EngineChoice.LOCAL,
        speaker_mode=SpeakerMode.SINGLE,
    )


def test_export_excludes_music_but_transcript_retains_it(tmp_path: Path) -> None:
    """The message export drops the lyrics; the structured transcript keeps them.

    This is the whole point of marking rather than filtering: the musical range
    stays addressable so a clip candidate can still point at it.
    """
    ports = build_fake_ports(
        root=tmp_path,
        transcription=FakeTranscriptionPort(
            script=(
                ("hoy quiero contarles algo", SegmentKind.SPEECH),
                ("y volare sin ti", SegmentKind.MUSIC),
                ("como les decia", SegmentKind.SPEECH),
            )
        ),
    )
    _run(ports, tmp_path)

    exported = ports.storage.load_export_path(DEFAULT_JOB_ID)
    assert exported is not None
    text = exported.read_text(encoding="utf-8")
    assert "volare sin ti" not in text
    assert text == "hoy quiero contarles algo\ncomo les decia"

    stored = ports.storage.load_transcript(DEFAULT_JOB_ID)
    assert stored is not None
    assert [s.kind for s in stored.segments] == [
        SegmentKind.SPEECH,
        SegmentKind.MUSIC,
        SegmentKind.SPEECH,
    ]
    music = next(s for s in stored.segments if s.kind is SegmentKind.MUSIC)
    assert music.text == "y volare sin ti"
    assert music.end_s > music.start_s


def test_unclassified_engine_export_is_marked_not_empty(tmp_path: Path) -> None:
    """A non-classifying engine must not turn a multi-hour run into zero bytes."""
    ports = build_fake_ports(
        root=tmp_path, transcription=NonClassifyingFakeTranscriptionPort()
    )
    _run(ports, tmp_path)

    exported = ports.storage.load_export_path(DEFAULT_JOB_ID)
    assert exported is not None
    text = exported.read_text(encoding="utf-8")
    assert text != ""
    assert text.startswith("[?] ")
