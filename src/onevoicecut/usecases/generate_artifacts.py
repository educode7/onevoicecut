"""Summary and clip candidates from a finished transcript, via map-reduce.

Lives entirely above `TextGenerationPort`, which knows nothing about summaries,
windows or chunking — that is what makes swapping an LLM provider an adapter-only
change.

This module currently holds the **MAP windowing** half. A three-hour sermon does
not fit in any context window, so it is cut into overlapping windows and each is
summarised separately before the partials are folded (slice 10a-iii).

Two decisions here are worth more than the code that implements them.

**The model never emits a timestamp.** Each segment is rendered with its **id** —
its index in the transcript — and the model is asked to cite ids back. The use
case resolves them against the real `Transcript`, and 10a-iii rejects any id a
window did not contain. Asked for a number, an LLM will produce a plausible one,
and a fabricated timestamp points an operator at the wrong minute of a three-hour
video while looking entirely correct: the same class of silent failure as
undeclared diarization, in the artifact rather than in the transcript.

**Tokens are estimated as `chars/4`, with no tokenizer behind it.** The estimate
is conservative, the adapter raises `ContextLengthExceeded` when it is wrong, and
10a-iv halves the window and retries. That keeps a tokenizer dependency — and a
provider-specific one at that — out of the core, and it makes the estimate the
same for every provider.
"""

import math
from dataclasses import dataclass

from onevoicecut.domain.transcript import TranscriptSegment, is_speech

# From design.md. A silent change to either is a change in what the model is
# asked to reason about, and nothing downstream would report it.
DEFAULT_MAP_WINDOW_TOKENS = 3000
DEFAULT_MAP_OVERLAP_TOKENS = 200

# Deliberately crude, deliberately conservative. See the module docstring.
CHARS_PER_TOKEN = 4


@dataclass(frozen=True, slots=True)
class MapWindow:
    """One prompt's worth of transcript, and the ids it is allowed to cite.

    Carries both because the two must agree: 10a-iii rejects any id the model
    returns that the window did not contain, so a window whose text and manifest
    disagreed would either reject a valid citation or admit an invented one.

    Not a domain entity — it is an artefact of prompt construction, with no life
    outside this module, so it stays here rather than joining the summary and
    clip candidates in `domain/generation.py`.
    """

    segment_ids: tuple[int, ...]
    text: str


def estimate_tokens(text: str) -> int:
    """Rounded up, because under-counting a budget is a request the provider
    refuses. Costing a fraction of a token as a whole one is the safe error."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def render_segment(index: int, segment: TranscriptSegment) -> str:
    """`[s0042] text`. The id is the transcript index, which makes resolution a
    lookup rather than a search and stops two windows numbering the same segment
    differently."""
    return f"[s{index:04d}] {segment.text}"


def map_windows(
    segments: tuple[TranscriptSegment, ...],
    *,
    window_tokens: int = DEFAULT_MAP_WINDOW_TOKENS,
    overlap_tokens: int = DEFAULT_MAP_OVERLAP_TOKENS,
) -> tuple[MapWindow, ...]:
    """Cut the transcript into overlapping windows, losing nothing.

    Coverage is the property that matters and the one whose violation is silent:
    a dropped segment is a passage of the sermon the model never saw, and the
    summary that comes back reads exactly as well without it.

    Overlap exists so a thought split across a boundary survives in one piece
    rather than being summarised twice as two half-thoughts, with nothing left to
    reconcile them. It is bounded because it is duplicated work paid on every
    window of a three-hour transcript.

    A segment too large for the budget gets a window of its own, over budget. It
    cannot be made to fit and it must not be dropped, so it is handed on and
    `ContextLengthExceeded` deals with it — which is what 10a-iv's halving retry
    is for.
    """
    if overlap_tokens >= window_tokens:
        # Every window would start where the last one did. Refusing at the call
        # beats a job that never ends.
        raise ValueError(
            f"overlap_tokens ({overlap_tokens}) must be smaller than "
            f"window_tokens ({window_tokens}); an overlap at least the window "
            f"makes every window start where the previous one did"
        )

    return _windows_over(
        tuple(enumerate(segments)),
        window_tokens=window_tokens,
        overlap_tokens=overlap_tokens,
    )


def speech_windows(
    segments: tuple[TranscriptSegment, ...],
    *,
    window_tokens: int = DEFAULT_MAP_WINDOW_TOKENS,
    overlap_tokens: int = DEFAULT_MAP_OVERLAP_TOKENS,
) -> tuple[MapWindow, ...]:
    """The same windowing, over confirmed speech only.

    The `.txt` export keeps `UNCERTAIN` and marks it, because a reader seeing
    `[?]` knows what they are looking at. The model gets the stricter rule, and
    the difference is the entire reason `speech_segments` and
    `render_message_text` are two functions: **a model will not honour an inline
    marker the way a reader does.** Hand it a marked chorus and it may summarise
    the worship set as the preacher's argument — fluently, confidently, and with
    nothing in the artifact saying so. `MUSIC` goes for the plainer reason that
    sung lyrics are not the message.

    **The filter never renumbers.** Ids are resolved against the real
    `Transcript`, music included, so windows numbering their own survivors 0,1,2
    would point every citation at the wrong moment of the sermon — the exact
    failure ids exist to prevent.

    A transcript with no speech produces no windows at all, not one empty one. A
    model asked to summarise nothing answers anyway, and that answer would become
    the summary. Reaching that state is not hypothetical: every cloud transcript
    does, which is why such a job is refused at admission.
    """
    return _windows_over(
        tuple((i, s) for i, s in enumerate(segments) if is_speech(s)),
        window_tokens=window_tokens,
        overlap_tokens=overlap_tokens,
    )


def _windows_over(
    numbered: tuple[tuple[int, TranscriptSegment], ...],
    *,
    window_tokens: int,
    overlap_tokens: int,
) -> tuple[MapWindow, ...]:
    """Indexed rather than positional, so a filtered transcript keeps its ids."""
    costs = [estimate_tokens(render_segment(i, s)) for i, s in numbered]
    windows: list[MapWindow] = []
    start = 0

    while start < len(numbered):
        end = start
        budget = 0
        # At least one segment, always. That is what guarantees progress when a
        # single segment exceeds the whole budget.
        while end < len(numbered) and (
            end == start or budget + costs[end] <= window_tokens
        ):
            budget += costs[end]
            end += 1

        windows.append(_window(numbered, start, end))
        if end >= len(numbered):
            break
        start = _next_start(costs, start, end, overlap_tokens)

    return tuple(windows)


def _window(
    numbered: tuple[tuple[int, TranscriptSegment], ...], start: int, end: int
) -> MapWindow:
    chosen = numbered[start:end]
    return MapWindow(
        segment_ids=tuple(index for index, _ in chosen),
        text="\n".join(render_segment(index, segment) for index, segment in chosen),
    )


def _next_start(costs: list[int], start: int, end: int, overlap_tokens: int) -> int:
    """Rewind over the tail that fits in the overlap budget, never past the start.

    **At least one segment is always carried** when any overlap was asked for,
    even one that exceeds the budget on its own. Segments are indivisible, so a
    budget expressed in tokens cannot always be honoured exactly — the same
    reason an oversized segment gets a window of its own rather than being
    dropped. Carrying nothing would be a hard boundary, which is precisely what
    overlap exists to avoid: a thought split there is summarised twice as two
    half-thoughts, with nothing left to reconcile them.

    Zero asked for is zero given, so a caller that genuinely wants hard
    boundaries can have them.

    Never returning `start` itself is the termination rule: a window whose ids
    the previous one already held makes no progress, and a transcript longer than
    the budget would window forever — inside a job already measured in hours.
    """
    if overlap_tokens <= 0:
        return end

    rewound = end
    carried = 0
    while rewound > start + 1:
        cost = costs[rewound - 1]
        if carried and carried + cost > overlap_tokens:
            break
        rewound -= 1
        carried += cost
    return rewound
