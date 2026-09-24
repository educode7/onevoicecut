"""The HTTP surface for `13b-iv`: requesting a clip export, and reading it back.

`POST .../clips` only ever *writes* `PENDING` exports (13b-iv delivers no
spawn — see `openspec/changes/video-transcription-pipeline/tasks.md`'s note
on `13b.29`). A `PENDING` export with no render worker is the queued state,
the same way a `QUEUED` job with no worker is one.

The two `GET` routes are read-only, mirroring `test_job_status_route.py`'s
own write-nothing proof: nothing here has a worker to race against.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from onevoicecut.adapters.web.app import WebDependencies, create_app
from onevoicecut.domain.generation import ClipCandidate, GenerationResult, ScriptVariant
from onevoicecut.domain.ids import JobId, make_clip_id, make_job_id, make_media_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.rendering import (
    CaptionCoverage,
    ClipExport,
    ClipState,
    DurationCompliance,
    DurationComplianceKind,
    OutputQuality,
    OutputQualityKind,
    OutputSpec,
    RenderedClip,
    RenderProfile,
    SafeArea,
    SubtitleTimingSource,
)
from onevoicecut.domain.framing import TrackingConfidence
from onevoicecut.usecases.generate_artifacts import SCRIPT_TARGETS
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    OPERATOR_A,
    accepting_extractor,
    auth_headers,
    fake_authenticate,
)

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
MEDIA_ID = make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE")
CLIP_ID = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFF")

# The shipped `vertical` profile is measured now (see
# `usecases/render_profiles.py`), but its fractions are a re-measurement-
# sensitive value and the routes' behaviour never depends on them -- so this
# fixture stands in for a measured destination, the same role
# `test_render_profiles.py`'s `MEASURED` plays, and the route tests survive an
# operator re-measuring the live interfaces untouched.
MEASURED_VERTICAL = RenderProfile(
    name="vertical",
    output=OutputSpec(width=1080, height=1920),
    safe_area=SafeArea(top=0.06, bottom=0.18, left=0.05, right=0.14),
    max_duration_s=90.0,
)
TEST_RENDER_PROFILES = {"vertical": MEASURED_VERTICAL}
OTHER_CLIP_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFG"
FIXED_CLIP_ID_HEX = str(CLIP_ID)


def a_job(state: JobState = JobState.COMPLETED) -> JobRecord:
    return JobRecord(
        job_id=JOB_ID,
        media_id=MEDIA_ID,
        state=state,
        speaker_mode=SpeakerMode.SINGLE,
        engine=EngineChoice.LOCAL,
        created_at=1000.0,
        updated_at=1000.0,
        worker_pid=None,
        error=None,
        owner=OPERATOR_A,
    )


def a_variant(target: str) -> ScriptVariant:
    return ScriptVariant(target=target, format="plain", body=f"Hola {target}", duration_target_s=45.0)


def a_candidate(
    *, start_s: float = 120.0, end_s: float = 150.0, targets: tuple[str, ...] = ("tiktok", "facebook")
) -> ClipCandidate:
    return ClipCandidate(
        start_s=start_s,
        end_s=end_s,
        hook="Hermanos, escuchen",
        quote="Un momento del sermon",
        rationale="Resume el argumento central",
        score=0.9,
        variants=tuple(a_variant(t) for t in targets),
    )


@pytest.fixture
def storage(tmp_path: Path) -> FakeTranscriptStoragePort:
    store = FakeTranscriptStoragePort(tmp_path)
    store.create_job(a_job())
    return store


@pytest.fixture
async def client(storage: FakeTranscriptStoragePort) -> AsyncIterator[AsyncClient]:
    app = create_app(
        WebDependencies(
            storage=storage,
            authenticate=fake_authenticate,
            extractor_for=accepting_extractor,
            new_clip_id=lambda: CLIP_ID,
            render_profiles=TEST_RENDER_PROFILES,
            script_targets=SCRIPT_TARGETS,
        )
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=auth_headers()
    ) as http:
        yield http


async def request_clip(
    client: AsyncClient,
    *,
    candidate_index: int = 0,
    targets: tuple[str, ...] = ("tiktok", "facebook"),
    job_id: JobId = JOB_ID,
) -> Any:
    return await client.post(
        f"/api/jobs/{job_id}/clips",
        json={"candidate_index": candidate_index, "targets": list(targets)},
    )


class TestRequestingAClip:
    async def test_a_job_not_completed_is_refused_with_409(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        """Artifacts are seeded even though the job is not `COMPLETED`, so a
        state check that was silently removed would still be caught -- the
        job would otherwise be refused for the *other* reason
        (`ArtifactsNotAvailable`) and this test would prove nothing about the
        state gate specifically."""
        storage.update_job(a_job(JobState.TRANSCRIBING))
        storage.save_artifacts(
            JOB_ID, GenerationResult(job_id=JOB_ID, summary="s", clip_candidates=(a_candidate(),))
        )

        response = await request_clip(client)

        assert response.status_code == 409

    async def test_a_job_with_no_generated_artifacts_is_refused_with_409(
        self, client: AsyncClient
    ) -> None:
        """`COMPLETED` and "generation has run" are two different facts today
        -- `save_artifacts` still has no production caller."""
        response = await request_clip(client)

        assert response.status_code == 409

    async def test_a_completed_job_with_a_candidate_is_accepted(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        storage.save_artifacts(
            JOB_ID, GenerationResult(job_id=JOB_ID, summary="s", clip_candidates=(a_candidate(),))
        )

        response = await request_clip(client)

        assert response.status_code == 202
        body = response.json()
        assert body["clip_id"] == FIXED_CLIP_ID_HEX
        assert body["profiles"] == ["vertical"]

    async def test_two_networks_sharing_one_profile_report_one_profile(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        """Every shipped destination is `vertical`, so the fan-out for two
        networks reports one profile -- the multi-profile grouping itself is
        `TestGroupVariantsByProfile`'s job, not this route's to re-prove."""
        storage.save_artifacts(
            JOB_ID, GenerationResult(job_id=JOB_ID, summary="s", clip_candidates=(a_candidate(),))
        )

        response = await request_clip(client, targets=("tiktok", "facebook"))

        assert response.json()["profiles"] == ["vertical"]

    async def test_the_pending_export_is_written_before_the_response(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        storage.save_artifacts(
            JOB_ID, GenerationResult(job_id=JOB_ID, summary="s", clip_candidates=(a_candidate(),))
        )

        await request_clip(client)

        (export,) = storage.load_clip_exports(JOB_ID, CLIP_ID)
        assert export.state is ClipState.PENDING
        assert export.clip is None

    async def test_the_written_export_carries_the_candidates_source_range(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        storage.save_artifacts(
            JOB_ID,
            GenerationResult(
                job_id=JOB_ID,
                summary="s",
                clip_candidates=(a_candidate(start_s=200.0, end_s=240.0),),
            ),
        )

        await request_clip(client)

        (export,) = storage.load_clip_exports(JOB_ID, CLIP_ID)
        assert (export.source_start_s, export.source_end_s) == (200.0, 240.0)

    async def test_an_out_of_range_candidate_index_is_a_404(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        storage.save_artifacts(
            JOB_ID, GenerationResult(job_id=JOB_ID, summary="s", clip_candidates=(a_candidate(),))
        )

        response = await request_clip(client, candidate_index=5)

        assert response.status_code == 404

    async def test_a_target_naming_no_script_variant_is_a_422(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        storage.save_artifacts(
            JOB_ID, GenerationResult(job_id=JOB_ID, summary="s", clip_candidates=(a_candidate(),))
        )

        response = await request_clip(client, targets=("tiktok", "bluesky"))

        assert response.status_code == 422
        assert "bluesky" in response.json()["detail"]


def a_rendered_clip(*, clip_id: Any = CLIP_ID) -> RenderedClip:
    return RenderedClip(
        clip_id=clip_id,
        job_id=JOB_ID,
        path=Path("clip.mp4"),
        source_start_s=120.0,
        source_end_s=150.0,
        quality=OutputQuality(kind=OutputQualityKind.NATIVE, factor=1.0),
        subtitle_timing=SubtitleTimingSource.WORD_LEVEL,
        captions=CaptionCoverage.CONFIRMED_SPEECH,
        tracking=TrackingConfidence.WELL_TRACKED,
        duration=DurationCompliance(kind=DurationComplianceKind.WITHIN_CEILING, overrun_s=0.0),
    )


def a_pending_export(*, profile: str = "vertical") -> ClipExport:
    return ClipExport(
        job_id=JOB_ID,
        clip_id=CLIP_ID,
        profile=profile,
        source_start_s=120.0,
        source_end_s=150.0,
        title="Hermanos, escuchen",
        description="Un momento del sermon",
        variants=(a_variant("tiktok"),),
        state=ClipState.PENDING,
        clip=None,
        failure=None,
    )


def a_done_export(*, profile: str = "vertical") -> ClipExport:
    return ClipExport(
        job_id=JOB_ID,
        clip_id=CLIP_ID,
        profile=profile,
        source_start_s=120.0,
        source_end_s=150.0,
        title="Hermanos, escuchen",
        description="Un momento del sermon",
        variants=(a_variant("tiktok"),),
        state=ClipState.DONE,
        clip=a_rendered_clip(),
        failure=None,
    )


class TestReadingAClipsExports:
    async def test_a_pending_exports_four_declarations_are_absent_not_invented(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        storage.save_clip_export(a_pending_export())

        response = await client.get(f"/api/jobs/{JOB_ID}/clips/{CLIP_ID}")

        assert response.status_code == 200
        (item,) = response.json()["exports"]
        assert item["state"] == "pending"
        assert item["quality"] is None
        assert item["subtitle_timing"] is None
        assert item["captions"] is None
        assert item["tracking"] is None

    async def test_a_done_exports_declarations_are_reported(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        storage.save_clip_export(a_done_export())

        response = await client.get(f"/api/jobs/{JOB_ID}/clips/{CLIP_ID}")

        (item,) = response.json()["exports"]
        assert item["state"] == "done"
        assert item["quality"] == {"kind": "native", "factor": 1.0}
        assert item["subtitle_timing"] == "word_level"
        assert item["captions"] == "confirmed_speech"
        assert item["tracking"] == "well_tracked"

    async def test_one_export_per_profile_is_listed(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        """Two distinct profiles for one clip, listed as two rows -- a
        single-object response would have to pick one and be wrong about the
        other."""
        storage.save_clip_export(a_pending_export(profile="vertical"))
        storage.save_clip_export(a_pending_export(profile="square"))

        response = await client.get(f"/api/jobs/{JOB_ID}/clips/{CLIP_ID}")

        profiles = {item["profile"] for item in response.json()["exports"]}
        assert profiles == {"vertical", "square"}

    async def test_reading_writes_nothing(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        storage.save_clip_export(a_pending_export())
        storage.calls.clear()

        await client.get(f"/api/jobs/{JOB_ID}/clips/{CLIP_ID}")

        assert storage.calls == []

    async def test_an_unknown_clip_is_a_404(self, client: AsyncClient) -> None:
        response = await client.get(f"/api/jobs/{JOB_ID}/clips/{OTHER_CLIP_ID}")

        assert response.status_code == 404

    async def test_a_malformed_clip_id_is_a_404_too(self, client: AsyncClient) -> None:
        response = await client.get(f"/api/jobs/{JOB_ID}/clips/not-a-ulid")

        assert response.status_code == 404


class TestReadingOneProfilesExport:
    async def test_a_known_profile_on_a_known_clip_resolves(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        storage.save_clip_export(a_pending_export(profile="vertical"))
        storage.save_clip_export(a_pending_export(profile="square"))

        response = await client.get(f"/api/jobs/{JOB_ID}/clips/{CLIP_ID}/square")

        assert response.status_code == 200
        assert response.json()["profile"] == "square"

    async def test_an_unknown_profile_on_a_known_clip_is_a_404(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        """Distinct scenario from an unknown clip: the clip id alone must
        never be treated as identifying a single rendered file."""
        storage.save_clip_export(a_pending_export(profile="vertical"))

        response = await client.get(f"/api/jobs/{JOB_ID}/clips/{CLIP_ID}/square")

        assert response.status_code == 404

    async def test_an_unknown_clip_is_a_404(self, client: AsyncClient) -> None:
        response = await client.get(f"/api/jobs/{JOB_ID}/clips/{OTHER_CLIP_ID}/vertical")

        assert response.status_code == 404

    async def test_reading_writes_nothing(
        self, client: AsyncClient, storage: FakeTranscriptStoragePort
    ) -> None:
        storage.save_clip_export(a_pending_export())
        storage.calls.clear()

        await client.get(f"/api/jobs/{JOB_ID}/clips/{CLIP_ID}/vertical")

        assert storage.calls == []
