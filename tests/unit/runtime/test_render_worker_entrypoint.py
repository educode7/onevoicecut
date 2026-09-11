"""The CLI wiring: id validation, exit codes, and what never gets touched.

`render_pending_exports` (proven in `test_render_worker.py` against fakes) is
the orchestration; this module is about `main` and `run_render` around it --
the same split `worker.py`'s own wiring tests draw between `run_job` and
`main`. Every test here monkeypatches `run_render` rather than constructing a
real `FilesystemTranscriptStorage`, so a wiring mistake is provable without a
job directory on disk; the real filesystem is exercised separately in
`tests/integration/test_render_worker_entrypoint.py`.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.ids import ClipId, JobId, make_clip_id, make_job_id
from onevoicecut.domain.rendering import ClipExport, ClipState
from onevoicecut.runtime import render_worker
from onevoicecut.runtime.render_worker import (
    EXIT_FAILED,
    EXIT_OK,
    EXIT_UNUSABLE,
    main,
)
from tests.unit.runtime.test_render_worker import a_pending_export

JOB_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFD"
CLIP_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFG"


def _argv(data_dir: Path, *, job_id: str = JOB_ID, clip_id: str = CLIP_ID) -> list[str]:
    return ["--job-id", job_id, "--clip-id", clip_id, "--data-dir", str(data_dir)]


class TestIdValidationComesBeforeAnythingIsTouched:
    def test_a_malformed_job_id_is_refused_before_run_render(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            render_worker,
            "run_render",
            lambda *a, **k: pytest.fail("run_render was called with a bad job id"),
        )

        exit_code = main(_argv(tmp_path, job_id="../../etc/passwd"))

        assert exit_code == EXIT_UNUSABLE

    def test_a_malformed_clip_id_is_refused_before_run_render(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            render_worker,
            "run_render",
            lambda *a, **k: pytest.fail("run_render was called with a bad clip id"),
        )

        exit_code = main(_argv(tmp_path, clip_id="not-a-ulid"))

        assert exit_code == EXIT_UNUSABLE


class TestNoSuchClipWasRequested:
    def test_an_empty_pending_set_refuses_without_writing_anything(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """`run_render` returns `None` for "the id pair named no clip at all" --
        the same shape `configured_resolver` returning `None` already gives
        `worker.main` for "nothing to do here"."""
        monkeypatch.setattr(render_worker, "run_render", lambda *a, **k: None)

        assert main(_argv(tmp_path)) == EXIT_UNUSABLE


class TestTheExitCodeReflectsWhatWasRendered:
    def test_every_export_done_exits_ok(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            render_worker,
            "run_render",
            lambda *a, **k: (_a_done_export(),),
        )

        assert main(_argv(tmp_path)) == EXIT_OK

    def test_any_failed_export_exits_failed(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            render_worker,
            "run_render",
            lambda *a, **k: (_a_done_export(), _a_failed_export()),
        )

        assert main(_argv(tmp_path)) == EXIT_FAILED

    def test_a_domain_error_from_run_render_is_reported_not_raised(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from onevoicecut.domain.errors import JobNotFound

        def _raise(*a: object, **k: object) -> tuple[ClipExport, ...] | None:
            raise JobNotFound(f"no job stored under {JOB_ID!r}")

        monkeypatch.setattr(render_worker, "run_render", _raise)

        assert main(_argv(tmp_path)) == EXIT_FAILED
        assert JOB_ID in capsys.readouterr().err


class TestRunRenderReceivesTheParsedIds:
    def test_the_validated_ids_and_data_dir_reach_run_render(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        received: list[tuple[JobId, ClipId, Path]] = []

        def _spy(
            job_id: JobId, clip_id: ClipId, data_dir: Path, **kwargs: object
        ) -> tuple[ClipExport, ...]:
            received.append((job_id, clip_id, data_dir))
            return (_a_done_export(),)

        monkeypatch.setattr(render_worker, "run_render", _spy)

        main(_argv(tmp_path))

        assert received == [(make_job_id(JOB_ID), make_clip_id(CLIP_ID), tmp_path)]


def _a_done_export() -> ClipExport:
    from onevoicecut.domain.framing import TrackingConfidence
    from onevoicecut.domain.rendering import (
        CaptionCoverage,
        DurationCompliance,
        DurationComplianceKind,
        OutputQuality,
        OutputQualityKind,
        RenderedClip,
        SubtitleTimingSource,
    )
    from tests.unit.runtime.test_render_worker import CLIP_ID as _CLIP_ID, JOB_ID as _JOB_ID

    return ClipExport(
        job_id=_JOB_ID,
        clip_id=_CLIP_ID,
        profile="vertical",
        source_start_s=120.0,
        source_end_s=150.0,
        title="t",
        description="d",
        variants=a_pending_export().variants,
        state=ClipState.DONE,
        failure=None,
        clip=RenderedClip(
            clip_id=_CLIP_ID,
            job_id=_JOB_ID,
            path=Path("render/vertical/x.mp4"),
            source_start_s=120.0,
            source_end_s=150.0,
            quality=OutputQuality(kind=OutputQualityKind.NATIVE, factor=0.89),
            subtitle_timing=SubtitleTimingSource.SEGMENT_LEVEL,
            captions=CaptionCoverage.CONFIRMED_SPEECH,
            tracking=TrackingConfidence.WELL_TRACKED,
            duration=DurationCompliance(
                kind=DurationComplianceKind.WITHIN_CEILING, overrun_s=0.0
            ),
        ),
    )


def _a_failed_export() -> ClipExport:
    from tests.unit.runtime.test_render_worker import CLIP_ID as _CLIP_ID, JOB_ID as _JOB_ID

    return ClipExport(
        job_id=_JOB_ID,
        clip_id=_CLIP_ID,
        profile="square",
        source_start_s=120.0,
        source_end_s=150.0,
        title="t",
        description="d",
        variants=a_pending_export().variants,
        state=ClipState.FAILED,
        failure="RenderProfileInvalid: nope",
        clip=None,
    )
