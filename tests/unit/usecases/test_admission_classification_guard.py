"""Refusing a job whose engine can never tell the sermon from the song.

Slice 10a-ii settled the question `speech_segments` had been holding open: MAP
windows are built from `SPEECH` only, because a model will not honour an inline
marker the way a reader does. That decision has a consequence the cloud adapter
made certain rather than likely.

The cloud engine declares `non_speech_classification=UNSUPPORTED` and marks every
segment `UNCERTAIN` — correctly, because it has no voice-activity control and
`SPEECH` is a claim it has not earned. So **every cloud transcript filters to
nothing**, and every cloud job would finish COMPLETED with an empty summary and
nothing anywhere saying why.

That is the silent degradation this project refuses everywhere else, so it is
refused here too: an engine that cannot classify cannot produce script artifacts,
and the operator is told at admission rather than after three hours of
transcription. Same shape as the diarization guard next to it, on the second and
independent capability axis — which is exactly what having two axes was for.

The transcript itself is untouched by this. It is the *script artifact* the system
cannot produce, and the refusal says so.
"""

from pathlib import Path

import pytest

from onevoicecut.domain.errors import ClassificationUnsupported, DiarizationUnsupported
from onevoicecut.domain.ids import make_operator_id
from onevoicecut.domain.jobs import EngineChoice, SpeakerMode
from onevoicecut.ports.capabilities import (
    ClassificationSupport,
    DeclaredSupport,
    DiarizationSupport,
)
from onevoicecut.usecases.admit_job import admit_job
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

OWNER = make_operator_id("maria")


def _declares(
    *,
    diarization: DiarizationSupport = DiarizationSupport.AVAILABLE,
    classification: ClassificationSupport = ClassificationSupport.AVAILABLE,
) -> DeclaredSupport:
    return DeclaredSupport(
        diarization=diarization, non_speech_classification=classification
    )


def _admit(
    storage: FakeTranscriptStoragePort,
    declared: DeclaredSupport,
    *,
    speaker_mode: SpeakerMode = SpeakerMode.SINGLE,
) -> object:
    return admit_job(
        engine=EngineChoice.CLOUD,
        speaker_mode=speaker_mode,
        operator=OWNER,
        storage=storage,
        capabilities=lambda _engine: declared,
    )


class TestAnEngineThatCannotClassify:
    def test_the_job_is_refused(self, tmp_path: Path) -> None:
        """Not admitted-then-empty. A three-hour job ending COMPLETED with a
        blank summary is the worst available outcome: it looks like success."""
        with pytest.raises(ClassificationUnsupported):
            _admit(
                FakeTranscriptStoragePort(tmp_path),
                _declares(classification=ClassificationSupport.UNSUPPORTED),
            )

    def test_nothing_is_stored(self, tmp_path: Path) -> None:
        """Same rule the diarization guard already follows: a refused admission
        mints no ids and touches no storage."""
        storage = FakeTranscriptStoragePort(tmp_path)

        with pytest.raises(ClassificationUnsupported):
            _admit(
                storage, _declares(classification=ClassificationSupport.UNSUPPORTED)
            )

        assert storage.calls == []

    def test_the_refusal_names_the_declaration(self, tmp_path: Path) -> None:
        """An operator needs to know *which* capability is missing, not that
        something was incompatible. The remedy differs per axis."""
        with pytest.raises(ClassificationUnsupported) as refusal:
            _admit(
                FakeTranscriptStoragePort(tmp_path),
                _declares(classification=ClassificationSupport.UNSUPPORTED),
            )

        assert "classification" in str(refusal.value).lower()


class TestAnEngineThatCanClassify:
    def test_it_is_admitted(self, tmp_path: Path) -> None:
        job = _admit(FakeTranscriptStoragePort(tmp_path), _declares())

        assert job is not None

    def test_the_two_axes_are_independent(self, tmp_path: Path) -> None:
        """Never infer one from the other — the rule the capability ports were
        built around. An engine that classifies and cannot diarize is the local
        one today, and a single-speaker job on it must admit normally.
        """
        job = _admit(
            FakeTranscriptStoragePort(tmp_path),
            _declares(
                diarization=DiarizationSupport.REQUIRES_SETUP,
                classification=ClassificationSupport.AVAILABLE,
            ),
        )

        assert job is not None


class TestBothAxesTogether:
    def test_diarization_is_still_refused_on_its_own(self, tmp_path: Path) -> None:
        with pytest.raises(DiarizationUnsupported):
            _admit(
                FakeTranscriptStoragePort(tmp_path),
                _declares(diarization=DiarizationSupport.UNSUPPORTED),
                speaker_mode=SpeakerMode.MULTI,
            )

    def test_a_job_failing_both_reports_the_speaker_mode_first(
        self, tmp_path: Path
    ) -> None:
        """Order is a choice, not an accident. Speaker mode is something the
        operator *asked for* and can withdraw; classification is a property of
        the engine they picked. Naming the retractable one first gives them the
        cheaper fix to try.
        """
        with pytest.raises(DiarizationUnsupported):
            _admit(
                FakeTranscriptStoragePort(tmp_path),
                _declares(
                    diarization=DiarizationSupport.UNSUPPORTED,
                    classification=ClassificationSupport.UNSUPPORTED,
                ),
                speaker_mode=SpeakerMode.MULTI,
            )

    def test_no_guard_supplied_still_admits_anything(self, tmp_path: Path) -> None:
        """`None` remains legal for tests and for the E2E harness. Production
        supplies it — a separate test asserts the composition root does."""
        job = admit_job(
            engine=EngineChoice.CLOUD,
            speaker_mode=SpeakerMode.MULTI,
            operator=OWNER,
            storage=FakeTranscriptStoragePort(tmp_path),
        )

        assert job is not None
