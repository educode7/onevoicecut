"""The one place a native binary becomes a domain outcome.

Two adapters now spawn ffmpeg -- the audio extractor and the video renderer --
and the part they share is not the command line, which `argv.py` composes, but
the *policy* around the spawn: check PATH first and say what to install, treat a
binary that vanished between the check and the launch as the same missing
binary, translate an overrun, and turn a non-zero exit into a domain error
carrying whatever ffmpeg actually complained about. Four translations, and a
second adapter reimplementing any one of them is how a machine that lost ffmpeg
gets an actionable sentence from one half of this app and a `WinError 2` from
the other.

**What is deliberately not shared is the runner.** Only a render needs a working
directory -- its filter graph names two files by bare filename -- and widening
the extractor's runner to carry an argument it never uses would churn every fake
in four test modules to describe something that does not apply to slicing audio.
So each adapter keeps its own runner shape and hands this module a closure over
it. The policy is common; the call is not.
"""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from onevoicecut.domain.errors import DomainError, FfmpegUnavailable


def missing_binary_message(binary: str) -> str:
    return (
        f"{binary} was not found on PATH. ffmpeg is a system binary and is NOT a "
        f"pip dependency, so `pip install -r requirements.txt` does not provide "
        f"it. Install ffmpeg (https://ffmpeg.org/download.html -- on Windows, "
        f"`winget install Gyan.FFmpeg`) and make sure {binary} is on PATH, then "
        f"retry the job."
    )


def real_process(
    argv: list[str], *, cwd: Path | None, timeout_s: float | None
) -> subprocess.CompletedProcess[str]:
    """The actual spawn. List form, and `shell` is never passed -- not even
    False, so no future edit can flip it without appearing in a diff."""
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
    )


class BinaryInvoker:
    """PATH verification and the three failure translations, per adapter instance.

    Stateful only in what it has already verified. That cache is why it is an
    object rather than a function: a three-hour job slices dozens of chunks and a
    clip is rendered once per distinct profile, and re-scanning PATH before each
    is pure waste.
    """

    def __init__(self) -> None:
        self._verified: set[str] = set()

    def require_available(self, binary: str) -> None:
        """Fail with an instruction, not a stack trace, when the binary is gone."""
        if binary in self._verified:
            return
        if shutil.which(binary) is None:
            raise FfmpegUnavailable(missing_binary_message(binary))
        self._verified.add(binary)

    def invoke(
        self,
        argv: list[str],
        *,
        spawn: Callable[[], subprocess.CompletedProcess[str]],
        timeout_s: float | None,
        on_timeout: type[DomainError],
        on_failure: type[DomainError],
    ) -> subprocess.CompletedProcess[str]:
        """Check, spawn, translate. Nothing from `subprocess` escapes.

        The two error types are separate because an overrun and a refusal are
        not the same news: probing an unreadable container is an
        `UnsupportedContainer`, and probing one that hung is not -- collapsing
        them would tell an operator their file is malformed when the machine was
        merely wedged.
        """
        binary = argv[0]
        self.require_available(binary)

        try:
            completed = spawn()
        except FileNotFoundError as error:
            # `which` succeeded but the spawn did not: the binary can vanish in
            # between, and a stale PATH entry can point at a deleted directory.
            raise FfmpegUnavailable(missing_binary_message(binary)) from error
        except subprocess.TimeoutExpired as error:
            raise on_timeout(f"{binary} timed out after {timeout_s}s") from error

        if completed.returncode != 0:
            raise on_failure(
                f"{binary} exited {completed.returncode}: "
                f"{completed.stderr.strip() or 'no diagnostics'}"
            )
        return completed
