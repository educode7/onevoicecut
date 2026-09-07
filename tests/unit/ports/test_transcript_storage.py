"""What the storage port must offer a render, and why a clip id is not enough.

`save_chunk_result` set the shape every persisted artifact in this system
follows: the value carries its own `job_id`, so a caller cannot save one record
under another job's directory by passing the wrong pair. Clip exports follow it.

**The read side takes a clip and returns a tuple.** One candidate now yields one
export per distinct profile, so "the export for this clip" names a set rather
than a record. A signature returning `ClipExport | None` would have forced every
caller to decide which profile it meant, at the moment the caller is least able
to know -- which is the same reason `export_key` takes both halves.
"""

import inspect

from onevoicecut.ports.transcript_storage import TranscriptStoragePort


class TestTheClipExportMethods:
    def test_the_port_declares_both(self) -> None:
        assert hasattr(TranscriptStoragePort, "save_clip_export")
        assert hasattr(TranscriptStoragePort, "load_clip_exports")

    def test_saving_takes_only_the_export(self) -> None:
        """The export carries its own job id through `clip.job_id`, so there is no
        pair to get wrong -- the rule `save_chunk_result` already follows."""
        parameters = inspect.signature(
            TranscriptStoragePort.save_clip_export
        ).parameters

        assert list(parameters) == ["self", "export"]

    def test_loading_takes_a_clip_and_answers_with_every_profile(self) -> None:
        signature = inspect.signature(TranscriptStoragePort.load_clip_exports)

        assert list(signature.parameters) == ["self", "job_id", "clip_id"]
        assert signature.return_annotation == "tuple[ClipExport, ...]"
