"""Fake conforming to AudioExtractorPort — no real ffmpeg subprocess.

Real adapters do not know the job id at slice() time either (the contract
signature carries only track/planned-chunk/dest); this fake accepts it at
construction, mirroring how the composition root will scope a real adapter
instance to one job.
"""

from pathlib import Path

from onevoicecut.domain.chunking import AudioChunk, PlannedChunk
from onevoicecut.domain.ids import JobId
from onevoicecut.domain.media import AudioTrack, MediaProbe, SourceMedia

FAKE_DURATION_S = 10.0


class FakeAudioExtractorPort:
    def __init__(
        self,
        job_id: JobId,
        *,
        probe_result: MediaProbe | None = None,
        probe_error: Exception | None = None,
    ) -> None:
        self._job_id = job_id
        # Configurable because the ingest path now decides whether a file is media
        # at all, and both answers have to be reachable without ffmpeg installed.
        self._probe_result = probe_result
        self._probe_error = probe_error

    def probe(self, media: SourceMedia) -> MediaProbe:
        if self._probe_error is not None:
            raise self._probe_error
        if self._probe_result is not None:
            return self._probe_result
        return MediaProbe(
            duration_s=FAKE_DURATION_S, container="mov,mp4,m4a", has_audio=True
        )

    def extract(self, media: SourceMedia, dest: Path) -> AudioTrack:
        return AudioTrack(
            media_id=media.media_id, path=dest, duration_s=FAKE_DURATION_S, size_bytes=1024
        )

    def slice(self, track: AudioTrack, planned: PlannedChunk, dest: Path) -> AudioChunk:
        return AudioChunk(
            job_id=self._job_id,
            index=planned.index,
            path=dest,
            start_s=planned.start_s,
            end_s=planned.end_s,
            size_bytes=track.size_bytes,
        )
