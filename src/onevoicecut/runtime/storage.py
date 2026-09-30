"""The storage object a composition root constructs: `core` plus the three
facades, presented as one.

`worker.py`, `render_worker.py` and `main.py` each build storage exactly once
and hand the result to everything above them. Deviation 3 freezes the two
workers' call bodies, so once `adapters/storage/` is retired in favour of the
three facades that now own the layout, the frozen
`FilesystemTranscriptStorage(data_dir)` line still needs a name to resolve — and
the composition layer, the only package allowed to construct adapters, is where
that name belongs.

It is the union of the three facades rather than a fourth implementation: the
facades already divide `TranscriptStoragePort` exactly along module ownership,
so inheriting them adds no behaviour and leaves nothing to drift. `job_dir` is
the one method all three declare, and the `transcripts` and `clips` docstrings
both say in so many words that `jobs` owns admission, which is what creates the
directory — so `jobs` first in the bases is also the correct answer for the one
method that overlaps.

The three `__init__`s are called explicitly rather than left to `super()`
chaining: each stores the same `core` as `self._core`, none of them chain on,
and spelling all three out says "one shared core, three facades" without asking
the reader to reason about method resolution to believe it.

`data_dir` stays injected rather than read from the environment here for the
same reason it was in the adapter this replaces: resolving
`ONEVOICECUT_DATA_DIR` is the composition root's job, and a layer that reads its
own configuration cannot be pointed at a `tmp_path`.
"""

from pathlib import Path

from onevoicecut.shared.infrastructure.storage.core import StorageCore
# Re-exported (`X as X` for mypy's `no_implicit_reexport`) so the render worker
# keeps resolving `RENDER_DIRNAME` from the module it already names.
from onevoicecut.shared.infrastructure.storage.core import (
    RENDER_DIRNAME as RENDER_DIRNAME,
)
from onevoicecut.systems.pipeline.clips.infrastructure.storage.clip_store import (
    FilesystemClipStore,
)
from onevoicecut.systems.pipeline.jobs.infrastructure.storage.job_store import (
    FilesystemJobStore,
)
from onevoicecut.systems.pipeline.transcripts.infrastructure.storage.transcript_store import (
    FilesystemTranscriptStore,
)


class FilesystemTranscriptStorage(
    FilesystemJobStore, FilesystemTranscriptStore, FilesystemClipStore
):
    """`TranscriptStoragePort` satisfied by three facades over one `core`.

    See the module docstring for why this exists. The contracts and the
    reasoning live on the port and on the facade that implements each half;
    nothing here decides anything.
    """

    def __init__(self, data_dir: Path) -> None:
        core = StorageCore(data_dir)
        FilesystemJobStore.__init__(self, core)
        FilesystemTranscriptStore.__init__(self, core)
        FilesystemClipStore.__init__(self, core)
