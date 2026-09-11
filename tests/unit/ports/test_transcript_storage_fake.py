"""The fake held to the same clip-export contract the filesystem is.

A fake earns its use in a use-case test only where it cannot pass something the
real adapter fails. Clip exports are exactly where that could go wrong: they are
the first record keyed by a pair rather than by an id, and a dict keyed by the
first half alone loses the second silently.
"""

from pathlib import Path

from onevoicecut.domain.framing import TrackingConfidence
from onevoicecut.domain.generation import ScriptVariant
from onevoicecut.domain.ids import make_clip_id, make_job_id, make_media_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.rendering import (
    CaptionCoverage,
    ClipExport,
    ClipState,
    DurationCompliance,
    DurationComplianceKind,
    OutputQuality,
    OutputQualityKind,
    RenderedClip,
    SubtitleTimingSource,
)
from tests.contract.clip_export_storage import assert_keyed_by_clip_and_profile
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
CLIP_ID = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFG")


def an_export(profile: str) -> ClipExport:
    return ClipExport(
        job_id=JOB_ID,
        clip_id=CLIP_ID,
        failure=None,
        clip=RenderedClip(
            clip_id=CLIP_ID,
            job_id=JOB_ID,
            path=Path("render") / f"{profile}.mp4",
            source_start_s=120.0,
            source_end_s=150.0,
            quality=OutputQuality(kind=OutputQualityKind.NATIVE, factor=0.89),
            subtitle_timing=SubtitleTimingSource.WORD_LEVEL,
            captions=CaptionCoverage.CONFIRMED_SPEECH,
            tracking=TrackingConfidence.WELL_TRACKED,
            duration=DurationCompliance(
                kind=DurationComplianceKind.WITHIN_CEILING, overrun_s=0.0
            ),
        ),
        profile=profile,
        title="Hermanos, escuchen",
        description="Un momento del sermon",
        variants=(
            ScriptVariant(
                target="tiktok", format="plain", body="Hola", duration_target_s=45.0
            ),
        ),
        state=ClipState.PENDING,
    )


def test_the_fake_keys_an_export_by_clip_and_profile(tmp_path: Path) -> None:
    storage = FakeTranscriptStoragePort(tmp_path)
    storage.create_job(
        JobRecord(
            job_id=JOB_ID,
            media_id=MEDIA_ID,
            state=JobState.PENDING,
            speaker_mode=SpeakerMode.SINGLE,
            engine=EngineChoice.LOCAL,
            created_at=1723501234.5,
            updated_at=1723501234.5,
            worker_pid=None,
            error=None,
            owner=None,
        )
    )

    assert_keyed_by_clip_and_profile(
        storage, JOB_ID, CLIP_ID, an_export("vertical"), an_export("square")
    )
