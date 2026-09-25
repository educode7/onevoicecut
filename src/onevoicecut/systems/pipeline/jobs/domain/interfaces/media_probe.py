"""The probe-only slice of the extractor, owned by `jobs`.

The upload decision splits `AudioExtractorPort` in two: admission and upload
ask `probe` before a job may be queued — container, duration, audio stream and
picture — while `extract` and `slice` are worker-side and belong to
`transcripts`. One `FfmpegAudioExtractor` satisfies both Protocols
structurally, so the split decides who may depend on what, not how ffmpeg is
invoked. The full extractor port never enters `jobs`.
"""

from typing import Protocol

from onevoicecut.shared.domain.media import MediaProbe, SourceMedia


class MediaProbePort(Protocol):
    def probe(self, media: SourceMedia) -> MediaProbe:
        """Raises UnsupportedContainer."""
        ...
