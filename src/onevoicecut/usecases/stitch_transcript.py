"""Reconcile overlapping chunk results into one transcript. Deterministic, no ASR.

Chunking with overlap is what stops a hard cut from splitting a word, and the
price is that every internal boundary decodes the same audio twice. This module
decides which copy survives. Both failure modes are invisible in the artifact: a
duplicated phrase reads as the speaker repeating themselves, a lost one as
something they never said.

This is also the single place chunk-local times become track-relative. Nothing
downstream re-offsets, which is why `TranscriptionPort.transcribe` documents its
return as chunk-local.
"""

import re
from dataclasses import replace

from onevoicecut.domain.chunking import ChunkPlan, ChunkResult, PlannedChunk
from onevoicecut.domain.transcript import TranscriptSegment

# Below this, a shared run of tokens is coincidence rather than the same utterance.
MIN_MATCH_TOKENS = 4

# `\w` is Unicode-aware for str patterns, so accents survive: Spanish `si` and
# `sí` are different words, and folding them would cut in the wrong place.
_TOKEN = re.compile(r"\w+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _tokens_with_owner(
    segments: list[TranscriptSegment],
) -> list[tuple[str, int]]:
    """Flatten to tokens, each remembering which segment produced it."""
    return [
        (token, index)
        for index, segment in enumerate(segments)
        for token in _tokens(segment.text)
    ]


def _shift(segment: TranscriptSegment, offset: float) -> TranscriptSegment:
    return replace(
        segment, start_s=segment.start_s + offset, end_s=segment.end_s + offset
    )


def _clip_before(
    segments: list[TranscriptSegment], cut: float
) -> list[TranscriptSegment]:
    """Keep what precedes the cut; truncate a straddling segment, drop it if empty."""
    kept: list[TranscriptSegment] = []
    for segment in segments:
        if segment.start_s >= cut:
            continue
        kept.append(
            segment if segment.end_s <= cut else replace(segment, end_s=cut)
        )
    return kept


def _clip_after(
    segments: list[TranscriptSegment], cut: float
) -> list[TranscriptSegment]:
    """Keep what follows the cut; truncate a straddling segment, drop it if empty."""
    kept: list[TranscriptSegment] = []
    for segment in segments:
        if segment.end_s <= cut:
            continue
        kept.append(
            segment if segment.start_s >= cut else replace(segment, start_s=cut)
        )
    return kept


def _matched_cut(
    tail: list[TranscriptSegment], head: list[TranscriptSegment]
) -> float | None:
    """Longest suffix-of-tail / prefix-of-head token run, longest match first."""
    tail_tokens = _tokens_with_owner(tail)
    head_tokens = _tokens_with_owner(head)
    longest = min(len(tail_tokens), len(head_tokens))

    for length in range(longest, MIN_MATCH_TOKENS - 1, -1):
        if [t for t, _ in tail_tokens[-length:]] != [t for t, _ in head_tokens[:length]]:
            continue

        position = len(tail_tokens) - length
        owner = tail_tokens[position][1]
        starts_the_segment = position == 0 or tail_tokens[position - 1][1] != owner

        if starts_the_segment:
            # Clean boundary: drop our copy and take the chunk's, which carries
            # fuller right-context because it continues past the overlap.
            return tail[owner].start_s
        # The phrase starts mid-segment, so cutting at that segment's start would
        # discard the words in front of it — text cannot be split at a token
        # boundary without mangling it. Keep our whole segment instead and take
        # the chunk from after it. Preserving words beats optimal context.
        return tail[owner].end_s

    return None


def _fallback_cut(
    window_start: float, window_end: float, accumulator: list[TranscriptSegment]
) -> float:
    """Midpoint of the contested window, snapped to the nearest segment boundary.

    Fires whenever the two decodes disagree — which on this material is routine,
    not exceptional, because music and singing in an overlap window produce no
    stable token run to match on. Bounded by construction: the cut stays inside
    the window, so it can never discard more than `overlap_s` of audio.
    """
    midpoint = (window_start + window_end) / 2
    boundaries = sorted(
        boundary
        for segment in accumulator
        for boundary in (segment.start_s, segment.end_s)
        if window_start <= boundary <= window_end
    )
    if not boundaries:
        return midpoint
    return min(boundaries, key=lambda boundary: abs(boundary - midpoint))


def _require_complete(plan: ChunkPlan, results: tuple[ChunkResult, ...]) -> None:
    """Every planned chunk, exactly once.

    A hole in the results stitches into a transcript that reads as complete — the
    words either side of the gap simply run together. Chunk 84 of 87 failing is a
    designed-for case; delivering the remainder as if it were the transcript is
    not. Callers that want a partial view must ask for one explicitly.
    """
    planned_indices = {chunk.index for chunk in plan.chunks}
    seen = [result.index for result in results]

    missing = sorted(planned_indices - set(seen))
    if missing:
        raise ValueError(f"cannot stitch: no result for planned chunk(s) {missing}")

    duplicated = sorted({index for index in seen if seen.count(index) > 1})
    if duplicated:
        raise ValueError(f"cannot stitch: more than one result for chunk(s) {duplicated}")

    unplanned = sorted(set(seen) - planned_indices)
    if unplanned:
        raise ValueError(f"cannot stitch: result for unplanned chunk(s) {unplanned}")


def stitch_transcript(
    plan: ChunkPlan, results: tuple[ChunkResult, ...]
) -> tuple[TranscriptSegment, ...]:
    _require_complete(plan, results)

    planned: dict[int, PlannedChunk] = {chunk.index: chunk for chunk in plan.chunks}
    ordered = sorted(results, key=lambda result: result.index)

    accumulator: list[TranscriptSegment] = []
    previous: PlannedChunk | None = None

    for result in ordered:
        chunk = planned[result.index]
        segments = [_shift(segment, chunk.start_s) for segment in result.segments]

        if previous is None:
            accumulator = segments
            previous = chunk
            continue

        window_start, window_end = chunk.start_s, previous.end_s
        if window_end <= window_start:
            accumulator = accumulator + segments  # no overlap to reconcile
            previous = chunk
            continue

        tail = [s for s in accumulator if s.end_s > window_start]
        head = [s for s in segments if s.start_s < window_end]

        cut = _matched_cut(tail, head)
        if cut is None:
            cut = _fallback_cut(window_start, window_end, accumulator)

        accumulator = _clip_before(accumulator, cut) + _clip_after(segments, cut)
        previous = chunk

    return tuple(accumulator)
