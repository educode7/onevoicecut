"""Whether this install can label speakers, answered without loading anything.

Split out of the adapter on purpose. `faster_whisper_adapter` imports the engine
at module level, so anything living there can only be read on a machine carrying
the optional ASR extras — and this is a question *about* which extras a machine
carries. Keeping it here means the declaration can be reasoned about, and tested,
on a checkout with none of them.

**`REQUIRES_SETUP` is the value that earns this module.** `UNSUPPORTED` is a claim
about an engine — the cloud adapter can never diarize, whatever anyone installs.
The local engine can; this machine may simply not be set up for it yet. Collapsing
the two would send an operator looking for a different engine when what they need
is a package and a licence, and the only engine they would find is the one that
genuinely cannot do it.

**This is a probe, not a proof, and the difference has already bitten here.**
Slice 7c watched CTranslate2 load happily onto a device it could not compute on,
which is why `_prove` now decodes a second of silence in the constructor. Install
state is the same kind of claim: the package being importable and a credential
being present says the *setup* is plausible, not that the pipeline will build. The
proof belongs with the diarizing call in 9a-ii, where it can be paid for once by a
job that actually asked for speakers — rather than by every `capabilities()` read,
including the ones that only wanted the byte cap.

The token is a value handed in, never an environment read. Same split as
`LOCAL_DEVICE_ENV` and `CLOUD_ASR_API_KEY`: the composition root reads the
environment, this only knows the variable's name so a refusal can carry its own
remedy.
"""

import importlib.util
from collections.abc import Callable
from typing import Any

from onevoicecut.ports.capabilities import ClassificationSupport, DiarizationSupport

# WhisperX wraps this rather than replacing it, so the package that decides the
# answer is the same either way.
DIARIZATION_PACKAGE = "pyannote.audio"

# A constant, unlike diarization: the Silero voice-activity pass ships with
# `faster_whisper` itself, so nothing about this install can turn it off. Stated
# here rather than inside `capabilities()` so the composition root can read it
# without importing the adapter — which imports the engine at module level.
CLASSIFICATION = ClassificationSupport.AVAILABLE

# Named for the refusal, not read here. `huggingface_hub` recognises several
# spellings; this is the one the project documents, and the worker is what turns
# it into a value.
HF_TOKEN_ENV = "HUGGING_FACE_TOKEN"

SpecFinder = Callable[[str], Any]


def is_installed(*, finder: SpecFinder = importlib.util.find_spec) -> bool:
    """Is the diarization package importable, without importing it.

    `find_spec` is used rather than a `try: import` because importing
    `pyannote.audio` pulls in torch — hundreds of megabytes and several seconds,
    on a call whose whole purpose is to be cheap enough to make before deciding
    anything.

    The `except` is the load-bearing part. Resolving a dotted name imports the
    *parent* package first, so on a machine without `pyannote` at all,
    `find_spec("pyannote.audio")` does not return `None` — it raises
    `ModuleNotFoundError`. Written the obvious way, this probe crashes
    `capabilities()` on exactly the machines it exists to describe.
    """
    try:
        return finder(DIARIZATION_PACKAGE) is not None
    except (ImportError, ValueError):
        # ValueError as well: `find_spec` raises it for a module that is present
        # in `sys.modules` but has no spec, which is what a half-initialised or
        # namespace-shadowed install looks like.
        return False


def diarization_support(*, installed: bool, token: str | None) -> DiarizationSupport:
    """The declaration, from the two facts that decide it.

    Both are required, and the second is easy to dismiss as bureaucracy. It is
    not: `pyannote.audio`'s models are gated, so the package installs freely
    while the weights refuse to download until someone has accepted the terms on
    their own account. A build with the code and no credential can no more
    diarize than one with neither, and declaring `AVAILABLE` on the strength of
    an import would admit a speaker-mode job that dies on its first chunk.
    """
    if not installed or token is None or not token.strip():
        return DiarizationSupport.REQUIRES_SETUP
    return DiarizationSupport.AVAILABLE
