"""The diarizing call: who spoke when, over one chunk's decoded samples.

Split from the adapter for the same reason `declarations.py` was, and the two
halves answer different questions. The declaration asks whether this *install*
can label speakers — cheaply, on every planning pass, with no imports. This
module makes the call itself, and the call is expensive: building the pipeline
loads torch and gated weights, which is precisely the cost the declaration
refuses to pay. Slice 7c learned that a library loading happily proves nothing
about running — but the proof cannot live in `capabilities()` either, because
callers that only wanted the byte cap would pay for it. So it lives here, built
on the first chunk of a job that actually asked for speakers, once, and reused
by every later chunk of that job.

Labels are namespaced `c{chunk:02d}/S{speaker:02d}` because they are only true
within one chunk. The pipeline's cluster identities restart on every run, so
chunk 4's `SPEAKER_00` and chunk 5's may be different people; reconciling them
is the `SpeakerResolver` seam (slice 9b). The namespace is what makes
per-chunk labels unambiguous until then — and what makes a leaked cross-chunk
comparison visibly wrong rather than silently misleading.

A segment no diarized speech region touches keeps `speaker=None`. The spec
asks for "a speaker label per segment" on a speaker-mode job, and this is the
honest reading: `_tile` restores filtered non-speech ranges as segments, and a
musical range has no speaker. Handing one the preacher's label would fabricate
attribution — the same class of quiet lie as returning unlabelled output,
pointed the other way.

Everything pyannote and torch touch is imported inside a function. The pure
half — overlap assignment, stable indices, the namespace, the build-once
lifecycle behind an injected loader — stays readable and testable on a
checkout carrying none of the heavy extras.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from typing import Any

from onevoicecut.adapters.asr.local.declarations import HF_TOKEN_ENV
from onevoicecut.domain.errors import EngineUnavailable
from onevoicecut.domain.transcript import TranscriptSegment

# (start_s, end_s, raw_label), chunk-local: the pipeline is handed one chunk's
# samples and never sees the track, so its times are already in the frame the
# port promises.
SpeakerRegion = tuple[float, float, str]

# Injected in tests so the lifecycle below is provable with no pipeline, no
# torch and no gated weights anywhere near the default suite.
PipelineLoader = Callable[[str], Any]

# The checkpoint whose terms the operator accepted. Named once, here, so a
# refusal can carry it.
DIARIZATION_CHECKPOINT = "pyannote/speaker-diarization-3.1"


def speaker_label(chunk_index: int, speaker_index: int) -> str:
    return f"c{chunk_index:02d}/S{speaker_index:02d}"


def speaker_indices(labels: Iterable[str]) -> dict[str, int]:
    """A stable, dense numbering for one chunk's raw cluster labels.

    Sorted order rather than first-appearance: the same annotation must number
    the same way every time it is read, and appearance order depends on which
    segment happens to be iterated first. Whatever names the pipeline chose —
    4.x renames clusters to `SPEAKER_00` and up, but nothing here may assume
    it — the numbering restarts at zero per chunk.
    """
    return {label: index for index, label in enumerate(sorted(set(labels)))}


def regions_from_annotation(annotation: Any) -> tuple[SpeakerRegion, ...]:
    """Flatten one pyannote `Annotation` into data the pure half can own.

    The duck-typed parameter is the boundary: pyannote objects stop here, and
    everything downstream is tuples of floats and strings.
    """
    return tuple(
        (float(turn.start), float(turn.end), str(label))
        for turn, _track, label in annotation.itertracks(yield_label=True)
    )


def assign_speakers(
    segments: Sequence[TranscriptSegment],
    regions: Sequence[SpeakerRegion],
    chunk_index: int,
) -> tuple[TranscriptSegment, ...]:
    """Give every segment the speaker that holds most of it.

    Maximal overlap, ties to the earlier region — annotations iterate in time
    order, so a segment straddling a speaker change belongs to whoever spoke
    first, deterministically. A segment no region touches keeps its
    `speaker=None`; see the module docstring for why labelling it anyway would
    be fabrication. Nothing but `speaker` is touched.
    """
    indices = speaker_indices(label for _, _, label in regions)
    labelled: list[TranscriptSegment] = []

    for segment in segments:
        best: SpeakerRegion | None = None
        best_overlap = 0.0
        for region in regions:
            overlap = min(segment.end_s, region[1]) - max(segment.start_s, region[0])
            if overlap > best_overlap:
                best_overlap = overlap
                best = region
        labelled.append(
            segment
            if best is None
            else replace(
                segment, speaker=speaker_label(chunk_index, indices[best[2]])
            )
        )

    return tuple(labelled)


def _load_pipeline(token: str) -> Any:
    """The one place pyannote is imported — never at module level.

    Returns `Optional` in pyannote 4.x, so None is a real answer and the
    caller's refusal, not a crash.
    """
    from pyannote.audio import Pipeline

    return Pipeline.from_pretrained(DIARIZATION_CHECKPOINT, token=token)


class LocalDiarizer:
    """One pipeline per speaker-mode job, built on the first chunk that asks.

    The adapter holds one of these for its whole life, and an adapter lives for
    one job, so "paid once per job" is a property of the wiring rather than of
    a cache somebody has to remember to clear.
    """

    def __init__(
        self, token: str | None, *, loader: PipelineLoader = _load_pipeline
    ) -> None:
        # Unreachable through `transcribe` — the capability check refuses a
        # speaker-mode job first — but a blank credential must still be refused
        # here, by name, rather than handed to the hub to fail with somebody
        # else's message.
        if token is None or not token.strip():
            raise EngineUnavailable(
                f"speaker-mode transcription needs a Hugging Face licence "
                f"token: set {HF_TOKEN_ENV}. Engine choice is per job and is "
                f"never substituted."
            )
        self._token = token
        self._loader = loader
        self._pipeline: Any | None = None

    def pipeline(self) -> Any:
        """The built pipeline, or the domain error explaining why there is none.

        Every construction failure — gated weights, revoked licence, offline
        machine, the `None` pyannote 4.x may return — becomes
        `EngineUnavailable`. None is never cached: rebuilding on every chunk of
        a job that cannot diarize would pay the failure repeatedly, and the
        worker loop must see a domain error, never a provider exception.
        """
        if self._pipeline is None:
            try:
                built = self._loader(self._token)
            except Exception as error:
                raise EngineUnavailable(
                    f"the diarization pipeline {DIARIZATION_CHECKPOINT!r} could "
                    f"not be built: {error}"
                ) from error
            if built is None:
                raise EngineUnavailable(
                    f"the diarization pipeline {DIARIZATION_CHECKPOINT!r} could "
                    f"not be built: the loader returned nothing for a "
                    f"checkpoint that should exist"
                )
            self._pipeline = built
        return self._pipeline

    def regions(self, audio: Any, *, sample_rate: int) -> tuple[SpeakerRegion, ...]:
        """Speaker regions over one chunk's decoded samples.

        Handed the same array the decoder and the voice-activity pass saw: two
        decodes of one file could disagree at the edges, and a speaker map
        drawn against different samples than the transcript is off by exactly
        that disagreement. The imports stay inside — this method only ever runs
        on a machine that asked for diarization.

        Callers reach this through `transcribe`'s guard, so a failure mid-call
        surfaces as `TranscriptionFailed` for the chunk; the construction
        refusal above stays an `EngineUnavailable` because it describes the
        job, not the chunk.
        """
        import numpy as np
        import torch

        waveform = torch.from_numpy(
            np.asarray(audio, dtype=np.float32)
        ).unsqueeze(0)
        output = self.pipeline()(
            {"waveform": waveform, "sample_rate": sample_rate}
        )
        # pyannote 4.x returns a `DiarizeOutput` bundle; a legacy-mode pipeline
        # returns the bare `Annotation`. One `getattr` serves both, and the
        # inclusive variant is the right one: overlapping speech belongs to
        # both speakers, and the maximal-overlap assignment above decides what
        # a straddling segment is mostly.
        annotation = getattr(output, "speaker_diarization", output)
        return regions_from_annotation(annotation)
