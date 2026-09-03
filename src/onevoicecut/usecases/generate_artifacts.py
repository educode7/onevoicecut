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

import json
import math
from dataclasses import dataclass

from onevoicecut.domain.errors import ContextLengthExceeded, GenerationFailed
from onevoicecut.domain.transcript import TranscriptSegment, is_speech
from onevoicecut.ports.text_generation import TextGenerationPort

# From design.md. A silent change to either is a change in what the model is
# asked to reason about, and nothing downstream would report it.
DEFAULT_MAP_WINDOW_TOKENS = 3000
DEFAULT_MAP_OVERLAP_TOKENS = 200

# Deliberately crude, deliberately conservative. See the module docstring.
CHARS_PER_TOKEN = 4

# One line per segment is how a window is rendered, which is also what lets one be
# split back apart without the transcript that produced it.
SEGMENT_SEPARATOR = "\n"

# Named in every refusal, so a message about a malformed answer says what was
# expected instead of only what arrived.
RESPONSE_SHAPE = 'JSON: {"summary": str, "segment_ids": [int]}'

# Spanish, because the source is. The prompts are the one place in this module
# where the material's language shows through.
_MAP_INSTRUCTION = (
    "Resume el siguiente fragmento de una predicacion. Cita los momentos "
    "destacados unicamente por su identificador de segmento, nunca por tiempo. "
    f"Responde en {RESPONSE_SHAPE}."
)
_FOLD_INSTRUCTION = (
    "Combina los dos resumenes parciales siguientes en uno solo, sin repetir "
    "ideas y sin agregar nada que no este en ellos."
)


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


@dataclass(frozen=True, slots=True)
class MapPartial:
    """One window's answer: prose to fold, and the moments it pointed at.

    The ids are kept separate from the prose because they are the only part that
    can be checked. Summary text is not verifiable against anything; a segment id
    either was in the window or was invented, and that is the whole reason the
    model is asked for ids instead of timestamps.
    """

    summary: str
    cited_ids: tuple[int, ...]


def parse_map_response(raw: str, window: MapWindow) -> MapPartial:
    """Read one window's answer, refusing anything it made up.

    Checked against **the window that produced it**, never against the transcript
    as a whole. Otherwise a model could cite a moment it was never shown and the
    citation would validate — which is exactly the fabrication the id scheme
    exists to catch.

    One bad id refuses the whole response rather than being dropped. A model that
    fabricated a reference may well have fabricated the sentence around it, and
    the prose is not checkable the way an id is; silently discarding the evidence
    while keeping the text is the worse of the two failures.
    """
    try:
        payload = json.loads(raw)
    except ValueError as error:
        raise GenerationFailed(
            f"the model did not answer with {RESPONSE_SHAPE}: {error}"
        ) from error

    if not isinstance(payload, dict) or not isinstance(payload.get("summary"), str):
        raise GenerationFailed(
            f"the model's answer carried no summary; expected {RESPONSE_SHAPE}"
        )

    raw_ids = payload.get("segment_ids", [])
    if not isinstance(raw_ids, list) or any(
        not isinstance(value, int) or isinstance(value, bool) for value in raw_ids
    ):
        # `"s0001"` is what a model returns when it echoes the rendered form
        # instead of the number, and coercing it would hide the disagreement.
        raise GenerationFailed(
            f"the model's segment_ids were not integers: {raw_ids!r}"
        )

    allowed = set(window.segment_ids)
    invented = sorted(value for value in raw_ids if value not in allowed)
    if invented:
        raise GenerationFailed(
            f"the model cited segment id(s) {invented} that its window did not "
            f"contain; a fabricated reference points at the wrong moment of the "
            f"recording while looking correct"
        )

    return MapPartial(summary=payload["summary"], cited_ids=tuple(raw_ids))


def run_map(
    windows: tuple[MapWindow, ...],
    *,
    generate: TextGenerationPort,
    max_output_tokens: int,
) -> tuple[MapPartial, ...]:
    """One call per window, each answer checked against its own window.

    More partials can come back than windows went in. `chars/4` is deliberately
    crude — it is what keeps a provider-specific tokenizer out of the core — and
    the price is that it is sometimes wrong in the expensive direction. A window
    the provider refuses is halved and retried, so it yields two partials rather
    than one, and REDUCE folds however many arrive. That fold is the mechanism
    which existed for this all along.
    """
    partials: list[MapPartial] = []
    for window in windows:
        partials.extend(
            _map_one(window, generate=generate, max_output_tokens=max_output_tokens)
        )
    return tuple(partials)


def _map_one(
    window: MapWindow, *, generate: TextGenerationPort, max_output_tokens: int
) -> tuple[MapPartial, ...]:
    """Summarise one window, halving it if the provider says it is too long.

    The same recovery slice 8b-i built for oversized audio chunks, and the same
    two properties: **coverage**, because a dropped half is a passage of the
    sermon nobody summarised and the summary reads as well without it; and
    **termination**, because a window of one segment cannot be halved into
    anything and must fail loudly rather than recurse forever.

    The halves do not overlap, unlike the windowing that produced them. Overlap
    exists to protect a thought split across a boundary; here the boundary
    already existed, so re-sending shared segments would pay twice for text the
    model has seen and return two partials repeating each other.
    """
    try:
        raw = generate.complete(_map_prompt(window), max_output_tokens=max_output_tokens)
    except ContextLengthExceeded as error:
        if len(window.segment_ids) < 2:
            raise ContextLengthExceeded(
                f"segment {window.segment_ids[0] if window.segment_ids else '?'} "
                f"alone exceeds the model's context and cannot be split further: "
                f"{error}"
            ) from error

        left, right = _halve(window)
        return _map_one(
            left, generate=generate, max_output_tokens=max_output_tokens
        ) + _map_one(right, generate=generate, max_output_tokens=max_output_tokens)

    return (parse_map_response(raw, window),)


def _halve(window: MapWindow) -> tuple[MapWindow, MapWindow]:
    """Split at a segment boundary, never inside one.

    The rendered text is one line per segment by construction, so lines and ids
    stay aligned and a half can be rebuilt without the original transcript —
    which is what keeps this recovery local to the module that made the window.
    """
    lines = window.text.splitlines()
    middle = len(window.segment_ids) // 2
    return (
        MapWindow(
            segment_ids=window.segment_ids[:middle],
            text=SEGMENT_SEPARATOR.join(lines[:middle]),
        ),
        MapWindow(
            segment_ids=window.segment_ids[middle:],
            text=SEGMENT_SEPARATOR.join(lines[middle:]),
        ),
    )


def reduce_summaries(
    partials: tuple[MapPartial, ...],
    *,
    generate: TextGenerationPort,
    max_output_tokens: int,
    budget_tokens: int = DEFAULT_MAP_WINDOW_TOKENS,
) -> str:
    """Fold partial summaries into one, two at a time.

    Sequential rather than all-at-once because eighty-seven partials do not fit
    in a context window any more than the transcript did — folding everything in
    one call would re-create the problem windowing was invented to solve.

    A single partial is returned untouched: there is nothing to reconcile, and
    paying a model to rephrase one summary buys nothing.

    An oversized fold is refused before it is sent. Spending a billed call to be
    told what the estimate already knew is the one avoidable cost here; 10a-iv
    turns this refusal into a halving retry.
    """
    if not partials:
        # Reached only if every window was filtered away, which admission now
        # refuses up front. This is the floor underneath that guard.
        return ""

    running = partials[0].summary
    for partial in partials[1:]:
        prompt = _fold_prompt(running, partial.summary)
        if estimate_tokens(prompt) > budget_tokens:
            raise GenerationFailed(
                f"folding the running summary with the next partial would need "
                f"{estimate_tokens(prompt)} tokens against a {budget_tokens} "
                f"budget"
            )
        running = generate.complete(prompt, max_output_tokens=max_output_tokens)

    return running


def _map_prompt(window: MapWindow) -> str:
    return (
        f"{_MAP_INSTRUCTION}\n\n{window.text}"
    )


def _fold_prompt(running: str, partial: str) -> str:
    return f"{_FOLD_INSTRUCTION}\n\nA:\n{running}\n\nB:\n{partial}"
