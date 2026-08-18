"""Proves the fake path crosses all five layers: domain, ports, usecases,
fakes-as-adapters, and a real `.txt` file on disk.
"""

import asyncio
from pathlib import Path
from typing import AsyncIterator

from tests.fakes import DEFAULT_JOB_ID, DEFAULT_MEDIA_ID, build_fake_ports
from transcribe.domain.jobs import EngineChoice, SpeakerMode
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
