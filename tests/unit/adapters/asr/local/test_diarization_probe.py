"""What this install can say about speaker labelling, before a job asks for it.

Deliberately in a module of its own, and deliberately importable with no optional
extras installed at all — no `faster_whisper`, no `pyannote.audio`, no torch. The
probe is a decision about the *environment*, so a test of it that could only run
on a machine already carrying half a gigabyte of wheels would be a test of the
machine.

The axis has three values and the third is the point. `UNSUPPORTED` means an
engine that can *never* diarize — the cloud adapter, permanently. The local engine
is not that: it can, once someone installs the package and accepts the model
licence. Saying `REQUIRES_SETUP` distinguishes "this build cannot yet" from "this
engine never will", and those want different answers from an operator.

What the probe is careful *not* to claim is that diarization will work. It reports
install state, and install state is not proof — the lesson slice 7c learned when
CTranslate2 loaded happily onto a device it could not compute on. The proof
belongs with the diarizing call in 9a-ii, the way `_prove` belongs with the
decode. This module's job is to make the honest declaration cheap enough to make
before a three-hour job starts.
"""

import pytest

from onevoicecut.adapters.asr.local.diarization import (
    DIARIZATION_PACKAGE,
    HF_TOKEN_ENV,
    diarization_support,
    is_installed,
)
from onevoicecut.ports.capabilities import DiarizationSupport

TOKEN = "hf_not-a-real-token"


class TestTheDecision:
    """A pure function of two facts, so both answers are reachable on a machine
    that has neither the package nor a token."""

    def test_installed_and_licensed_is_available(self) -> None:
        assert (
            diarization_support(installed=True, token=TOKEN)
            is DiarizationSupport.AVAILABLE
        )

    def test_a_missing_package_requires_setup(self) -> None:
        assert (
            diarization_support(installed=False, token=TOKEN)
            is DiarizationSupport.REQUIRES_SETUP
        )

    def test_an_installed_package_without_a_token_requires_setup(self) -> None:
        """The licence half. `pyannote.audio`'s models are gated: the package
        installs freely and the weights do not download until someone has
        accepted the terms on their own account. A build with the code and no
        credential can no more diarize than one with neither.
        """
        assert (
            diarization_support(installed=True, token=None)
            is DiarizationSupport.REQUIRES_SETUP
        )

    def test_a_blank_token_is_an_absent_one(self) -> None:
        assert (
            diarization_support(installed=True, token="   ")
            is DiarizationSupport.REQUIRES_SETUP
        )

    def test_it_never_answers_unsupported(self) -> None:
        """`UNSUPPORTED` is a claim about the *engine*, not about the install.

        The local engine can diarize; this machine may not be set up for it. An
        operator told `unsupported` would go looking for a different engine
        rather than for a missing package, and the cloud one — the only engine
        that genuinely cannot — is the wrong place to land.
        """
        answers = {
            diarization_support(installed=installed, token=token)
            for installed in (True, False)
            for token in (TOKEN, None)
        }

        assert DiarizationSupport.UNSUPPORTED not in answers


class TestTheInstallProbe:
    def test_it_reports_this_machine_honestly(self) -> None:
        """Runs against whatever the machine has, and asserts against the same
        question asked directly — so it passes on a developer box with the
        extras and on CI without them, and fails only if the probe disagrees
        with reality."""
        import importlib.util

        try:
            expected = importlib.util.find_spec(DIARIZATION_PACKAGE) is not None
        except (ImportError, ValueError):
            expected = False

        assert is_installed() is expected

    def test_a_missing_parent_package_is_not_an_error(self) -> None:
        """The gotcha this probe exists to contain.

        `importlib.util.find_spec("pyannote.audio")` does not return `None` when
        `pyannote` is absent — it **raises `ModuleNotFoundError`**, because
        resolving a dotted name imports the parent first. A probe written the
        obvious way crashes `capabilities()` on precisely the machines it was
        written to describe.
        """
        assert is_installed(finder=_raising_finder) is False

    def test_an_importable_package_reads_as_installed(self) -> None:
        assert is_installed(finder=lambda name: object()) is True

    def test_a_finder_returning_none_reads_as_absent(self) -> None:
        assert is_installed(finder=lambda name: None) is False


def test_the_token_variable_is_named_for_the_message_not_read_here() -> None:
    """The adapter knows the variable's name and never reads it.

    Same split as `LOCAL_DEVICE_ENV` and `CLOUD_ASR_API_KEY`: the composition
    root reads the environment, the adapter takes the value and names the
    variable in its own refusal. An adapter that read its own configuration
    could not be pointed at a test value.
    """
    assert HF_TOKEN_ENV.startswith("HUGGING")


def _raising_finder(name: str) -> object:
    raise ModuleNotFoundError(f"No module named {name.split('.')[0]!r}")


@pytest.mark.parametrize("token", ["", "   ", "\n", None])
def test_every_empty_shape_of_a_token_requires_setup(token: str | None) -> None:
    """An exported-but-empty variable is the shape a half-written `.env` takes,
    and a token read out of a file carries its newline."""
    assert (
        diarization_support(installed=True, token=token)
        is DiarizationSupport.REQUIRES_SETUP
    )
