"""The first capability gap this system warns about instead of refusing.

`ports/capabilities.py` says a field belongs there only if a use case must read
it to "reject **or warn about** a job before work starts", and records the "or
warn" half as a deliberate widening. Until now nothing used it: diarization and
classification both refuse, because a job that asked to tell two voices apart and
a job whose engine cannot tell the sermon from the song both produce artifacts
that are wrong rather than merely thinner.

Word timing is different, and the difference is the whole reason the axis warns.
A transcript without word timings is **complete**. Every sentence is there, every
timestamp is real, the summary and the clip candidates are exactly what they would
have been. What is missing is caption precision in a rendered clip — a smaller
loss than no transcript at all, and one an operator may knowingly accept to use
the engine they wanted.

So it is said out loud and the job proceeds. Refusing would deny a working
transcript over a rendering nicety; staying silent would let an operator discover
it when the captions land on the wrong syllable.

Returned rather than delivered through an injected callback. A
`warn: Callable | None = None` seam is precisely the shape that left the
admission capability guard disconnected from the composition root for three
slices — a default nobody has to supply is a default nobody supplies.
"""

from pathlib import Path

import pytest

from onevoicecut.shared.domain.errors import DiarizationUnsupported
from onevoicecut.shared.domain.ids import make_operator_id
from onevoicecut.systems.pipeline.jobs.domain.jobs import EngineChoice, JobState, SpeakerMode
from onevoicecut.shared.domain.capabilities import (
    ClassificationSupport,
    DeclaredSupport,
    DiarizationSupport,
    WordTimingSupport,
)
from onevoicecut.shared.application.principal import Principal
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.admit_job import (
    Admission,
    AdmitJobCommand,
    AdmitJobHandler,
)
from tests.fakes.transcript_storage import FakeTranscriptStoragePort

OWNER = make_operator_id("maria")


def _declares(
    *,
    word_timing: WordTimingSupport = WordTimingSupport.AVAILABLE,
    diarization: DiarizationSupport = DiarizationSupport.AVAILABLE,
) -> DeclaredSupport:
    return DeclaredSupport(
        diarization=diarization,
        non_speech_classification=ClassificationSupport.AVAILABLE,
        word_timing=word_timing,
    )


def _admit(
    storage: FakeTranscriptStoragePort,
    declared: DeclaredSupport | None,
    *,
    speaker_mode: SpeakerMode = SpeakerMode.SINGLE,
) -> Admission:
    """Dependencies on the handler; the caller's identity on the command."""
    capabilities = (
        None if declared is None else (lambda _engine: declared)
    )
    return AdmitJobHandler(storage=storage, capabilities=capabilities).handle(
        AdmitJobCommand(
            principal=Principal(identity=OWNER),
            engine=EngineChoice.LOCAL,
            speaker_mode=speaker_mode,
        )
    )


class TestAnEngineThatCannotTimeWords:
    def test_the_job_is_still_admitted(self, tmp_path: Path) -> None:
        """A transcript without word timings is complete. Refusing would deny a
        working transcript over a rendering nicety."""
        admission = _admit(
            FakeTranscriptStoragePort(tmp_path),
            _declares(word_timing=WordTimingSupport.UNSUPPORTED),
        )

        assert admission.job.state is JobState.PENDING

    def test_a_warning_names_the_missing_capability(self, tmp_path: Path) -> None:
        """"Something is limited" is not actionable. The operator's lever is the
        engine they chose, so the warning has to say which capability it lacks."""
        admission = _admit(
            FakeTranscriptStoragePort(tmp_path),
            _declares(word_timing=WordTimingSupport.UNSUPPORTED),
        )

        assert any("word" in warning.lower() for warning in admission.warnings)

    def test_the_warning_says_what_is_lost_rather_than_only_what_is_missing(
        self, tmp_path: Path
    ) -> None:
        """An operator who does not render clips does not care. One who does
        needs to know it is captions, not the transcript."""
        admission = _admit(
            FakeTranscriptStoragePort(tmp_path),
            _declares(word_timing=WordTimingSupport.UNSUPPORTED),
        )

        assert any("caption" in warning.lower() for warning in admission.warnings)

    def test_storage_is_touched_exactly_as_it_would_be_without_the_warning(
        self, tmp_path: Path
    ) -> None:
        """A warning is not a half-refusal. The record is created normally."""
        storage = FakeTranscriptStoragePort(tmp_path)

        _admit(storage, _declares(word_timing=WordTimingSupport.UNSUPPORTED))

        assert any(call.startswith("create_job") for call in storage.calls)


class TestAnEngineThatCanTimeWords:
    def test_there_is_nothing_to_warn_about(self, tmp_path: Path) -> None:
        admission = _admit(FakeTranscriptStoragePort(tmp_path), _declares())

        assert admission.warnings == ()


class TestWarningsDoNotReplaceRefusals:
    def test_a_speaker_mode_job_is_still_refused(self, tmp_path: Path) -> None:
        """The two are different answers to different questions. An unsatisfiable
        speaker-mode job produces a transcript that is *wrong*; a missing word
        timing produces one that is merely thinner."""
        with pytest.raises(DiarizationUnsupported):
            _admit(
                FakeTranscriptStoragePort(tmp_path),
                _declares(
                    diarization=DiarizationSupport.UNSUPPORTED,
                    word_timing=WordTimingSupport.UNSUPPORTED,
                ),
                speaker_mode=SpeakerMode.MULTI,
            )

    def test_a_refused_job_stores_nothing_despite_the_warning(
        self, tmp_path: Path
    ) -> None:
        """The warning must not become a reason to have already written the
        record before the refusal was reached."""
        storage = FakeTranscriptStoragePort(tmp_path)

        with pytest.raises(DiarizationUnsupported):
            _admit(
                storage,
                _declares(
                    diarization=DiarizationSupport.UNSUPPORTED,
                    word_timing=WordTimingSupport.UNSUPPORTED,
                ),
                speaker_mode=SpeakerMode.MULTI,
            )

        assert storage.calls == []


def test_no_capability_guard_means_no_warnings(tmp_path: Path) -> None:
    """`None` stays legal for the E2E harness. Nothing was declared, so nothing
    can be said about it — silence here is honest rather than reassuring."""
    admission = _admit(FakeTranscriptStoragePort(tmp_path), None)

    assert admission.warnings == ()
