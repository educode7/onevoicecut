"""Which segments become captions, and what the clip then declares about them.

A vertical clip is watched muted, so the burned-in caption is often its only
channel. That raises the stakes on the two declarations a clip carries, and both
are computed from **one basis: the eligible segments overlapping the span** —
never from the cues, and never from `capabilities().word_timing`.

The capability answers "could this engine ever produce word timings". The
segments answer "did *this clip* get them", and only the second is true about the
artifact: a word-timing-capable adapter can still return `()` for a segment.

**Eligibility is the message-facing rule every other consumer shares, with one
deliberate difference.** `MUSIC` is dropped — sung lyrics are not the message.
`UNCERTAIN` is *kept*, because an adapter that cannot classify marks everything
`UNCERTAIN`, and excluding it would leave a muted clip with a silently blank
caption channel. But its marker never reaches the frame: `render_message_text`
writes `[?]` for a human reading a transcript, while a caption **is** the message
and `[?]` on screen is not what the preacher said. The uncertainty is declared as
metadata instead, which is what `CaptionCoverage.INCLUDES_UNVERIFIED` is for.

**Splitting requires word times to split on.** A segment without them yields one
cue carrying the segment's own boundaries, even when that overruns the character
budget. Dividing it evenly would invent the timing — and even spacing looks
exactly like measurement while drifting with every syllable the speaker lingers
on. The clip declares `SEGMENT_LEVEL` so the coarser captions are never passed
off as ordinary ones.

Two quiet traps have explicit guards. `all()` over an empty set is `True`, so a
span with no eligible segment would otherwise declare a vacuous `WORD_LEVEL`. And
cue construction is **total** over the eligible set: only then are "zero cues" and
"no eligible segment" the same condition, which is what lets `NONE` mean
something an operator can act on.
"""

from onevoicecut.domain.framing import TimeSpan
from onevoicecut.domain.rendering import (
    CaptionCoverage,
    SubtitleCue,
    SubtitleTimingSource,
)
from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment, WordTiming

# Two readable lines on a nine-by-sixteen frame. Long enough that a normal phrase
# survives intact, short enough that a cue does not cover the speaker's face.
DEFAULT_MAX_CUE_CHARS = 42


def build_subtitle_cues(
    segments: tuple[TranscriptSegment, ...],
    span: TimeSpan,
    *,
    max_cue_chars: int = DEFAULT_MAX_CUE_CHARS,
) -> tuple[tuple[SubtitleCue, ...], SubtitleTimingSource, CaptionCoverage]:
    """Captions for one clip, plus the two things the clip must declare.

    Returns the cues, where their timing came from, and what the captions
    contain. All three are derived from the same eligible set, so a clip can
    never declare a coverage its captions contradict.
    """
    eligible = tuple(
        segment for segment in segments if _is_eligible(segment, span)
    )

    if not eligible:
        # Not a vacuous WORD_LEVEL, and not CONFIRMED_SPEECH over nothing.
        return (), SubtitleTimingSource.SEGMENT_LEVEL, CaptionCoverage.NONE

    cues = tuple(
        cue
        for segment in eligible
        for cue in _cues_for(segment, span, max_cue_chars)
    )
    return cues, _timing_of(eligible), _coverage_of(eligible)


def _is_eligible(segment: TranscriptSegment, span: TimeSpan) -> bool:
    """Captionable, and actually inside this clip.

    Blank text is excluded on purpose: a classifying adapter reports every
    non-speech range it kept out of its decode, and those carry timestamps and
    nothing else. A caption of nothing is a blank box on screen.
    """
    if segment.kind is SegmentKind.MUSIC:
        return False
    if not segment.text.strip():
        return False
    return segment.start_s < span.end_s and segment.end_s > span.start_s


def _cues_for(
    segment: TranscriptSegment, span: TimeSpan, max_cue_chars: int
) -> tuple[SubtitleCue, ...]:
    """At least one cue for every eligible segment — that is what totality means.

    Without word times there is exactly one, carrying the segment's own clipped
    boundaries. With them the segment is split on word edges, because a cue
    should start when its first word was actually said rather than where an even
    division happened to fall.
    """
    if not segment.words:
        return (
            SubtitleCue(
                start_s=_local(segment.start_s, span),
                end_s=_local(segment.end_s, span),
                text=segment.text.strip(),
            ),
        )

    cues: list[SubtitleCue] = []
    for group in _group_words(segment.words, max_cue_chars):
        cues.append(
            SubtitleCue(
                start_s=_local(group[0].start_s, span),
                end_s=_local(group[-1].end_s, span),
                text="".join(word.text for word in group).strip(),
            )
        )
    return tuple(cues)


def _group_words(
    words: tuple[WordTiming, ...], max_cue_chars: int
) -> tuple[tuple[WordTiming, ...], ...]:
    """Fill a cue up to the budget, never splitting a word to do it.

    Always at least one word per group, so a single word longer than the whole
    budget gets its own overlong cue rather than disappearing — the same
    indivisibility rule the chunk splitter and the MAP windower both follow.
    """
    groups: list[list[WordTiming]] = []
    current: list[WordTiming] = []

    for word in words:
        candidate = "".join(w.text for w in (*current, word)).strip()
        if current and len(candidate) > max_cue_chars:
            groups.append(current)
            current = [word]
        else:
            current.append(word)

    if current:
        groups.append(current)
    return tuple(tuple(group) for group in groups)


def _local(at_s: float, span: TimeSpan) -> float:
    """Source-absolute to clip-local, clamped into the clip.

    The render pass places `-ss` before `-i`, which resets output timestamps to
    zero, so a source-absolute cue would land a hundred seconds away. A segment
    straddling either boundary is clipped rather than dropped: half of it is
    still in the clip, and a caption for the other half would show words before
    or after they were spoken.
    """
    return min(max(at_s - span.start_s, 0.0), span.duration_s)


def _timing_of(eligible: tuple[TranscriptSegment, ...]) -> SubtitleTimingSource:
    """Word-level only when **every** captioned segment carries word times.

    The declaration is about the artifact, and the artifact is one clip: half
    word-level captions are not word-level captions. Eligibility runs first, so
    an untimed music segment — which was never going to be captioned — cannot
    degrade a clip it has no part in.
    """
    if all(segment.words for segment in eligible):
        return SubtitleTimingSource.WORD_LEVEL
    return SubtitleTimingSource.SEGMENT_LEVEL


def _coverage_of(eligible: tuple[TranscriptSegment, ...]) -> CaptionCoverage:
    """What the captions actually contain, so a clip carrying unverified audio
    can never be delivered as though it were confirmed speech."""
    if any(segment.kind is SegmentKind.UNCERTAIN for segment in eligible):
        return CaptionCoverage.INCLUDES_UNVERIFIED
    return CaptionCoverage.CONFIRMED_SPEECH
