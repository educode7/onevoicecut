"""ASS subtitle generation. Text in, document out — no ffmpeg, no process.

Kept beside `argv.py` and kept pure for the same reason: the applicable
threat-matrix row is asserted without ffmpeg installed and without launching
anything.

**Cue text is ASR output, and ASR output is model output.** `{...}` is an ASS
override block — it can move a caption off-screen, change its colour, or swallow
the rest of the line. A decoder that hallucinates `{\\an8}`, and Whisper-family
decoders hallucinate confidently, would be authoring subtitle directives inside a
clip nobody reviewed.

Three characters carry syntax meaning here and none carries meaning in
transcribed Spanish speech, so each is **substituted rather than escaped**: ASS
defines no reliable literal escape for any of the three, and a substitution that
renders wrong is better than a directive that executes.

| Character | Becomes | Why it cannot survive |
| --- | --- | --- |
| `{` | `(` | opens an override block |
| `}` | `)` | closes one, and an unmatched one is undefined behaviour |
| `\\` | `/` | begins an ASS escape |

The backslash matters more than it looks. `\\N` is a hard line break, so the text
`\\Nada` renders as a break followed by "ada" — a syllable silently deleted.
That is why escaping runs **before** intended breaks are inserted, and why the
break constant is applied by this module rather than by a caller who might apply
it first.

**Geometry is a function of the render profile, never a module constant.** The
margins come from the profile's safe area and the sizes are measured against the
resolution the header declares. A constant here is invisible while one profile
ships and is the wrong answer for every profile after it — silently, because the
file parses, renders, and looks correct at the one resolution it was written for.
"""

from onevoicecut.domain.errors import RenderProfileInvalid
from onevoicecut.domain.rendering import RenderProfile, SubtitleCue

# ASS's hard line break. The only one that reaches a rendered file, because
# `escape_cue_text` has already removed every backslash the source could carry.
LINE_BREAK = "\\N"

# Half the cue builder's 42-character budget, because a cue is sized for two
# lines. Named here rather than shared: the builder decides how much text a cue
# carries, this module decides how that text sits in the frame.
MAX_LINE_CHARS = 21

_SUBSTITUTIONS = str.maketrans({"{": "(", "}": ")", "\\": "/"})

# A dialogue line is one line by definition: a CR or LF inside the text ends the
# event early and turns the remainder into a malformed line libass will refuse.
_STRIPPED = str.maketrans({"\r": " ", "\n": " "})

# Of the output height. Typography is expressed as a fraction for the same reason
# the safe area is: a pixel size means nothing without the frame it is measured
# in, and a profile retargeted to another resolution should keep its apparent
# size rather than silently shrink. The value is a shape, not a measurement —
# 13b-v's real render is where a wrong one becomes visible.
FONT_HEIGHT_FRACTION = 0.05

# Of the font size. An outline that stayed absolute while the font scaled would
# read as a heavy border on a small frame and as no border at all on a large one.
OUTLINE_FONT_FRACTION = 1 / 16


def _header(profile: RenderProfile) -> str:
    """The document preamble, as a function of the profile rather than a constant.

    **`PlayRes` is the load-bearing line.** Without it libass measures typography
    against its own default frame, which is invisible while one profile ships and
    becomes a different apparent caption size per profile the moment a second one
    does. Declaring it makes every size below mean output pixels.

    **`MarginV` comes from `bottom` because the caption is bottom-anchored.**
    Alignment 2 places the text against the bottom edge, and ASS carries one
    vertical margin, which for that alignment is the distance from it. The
    profile's `top` is a real measurement — destinations put interface over the
    top of the frame too — but it constrains nothing a bottom-anchored caption
    does, so it is declared and unused here rather than folded into a number it
    has no part in.
    """
    safe_area = profile.safe_area
    if safe_area is None:
        raise RenderProfileInvalid(
            f"render profile {profile.name!r} declares no caption safe area, so "
            f"there is no margin to place a caption against; a margin is never "
            f"inherited from another profile"
        )

    width, height = profile.output.width, profile.output.height
    font_size = round(FONT_HEIGHT_FRACTION * height)
    outline = max(1, round(OUTLINE_FONT_FRACTION * font_size))
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Italic, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{font_size},&H00FFFFFF,&H00000000,&H80000000,-1,0,3,{outline},0,2,{round(safe_area.left * width)},{round(safe_area.right * width)},{round(safe_area.bottom * height)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def escape_cue_text(text: str) -> str:
    """Neutralise every character that carries ASS syntax meaning.

    Substitution, not escaping — see the module docstring for why ASS leaves no
    third option. Ordinary Spanish passes through untouched, which is every
    caption of every sermon; this is a tax paid only by text that could not have
    been said out loud.
    """
    return text.translate(_SUBSTITUTIONS).translate(_STRIPPED)


def render_ass(
    cues: tuple[SubtitleCue, ...], *, profile: RenderProfile
) -> str:
    """One dialogue event per cue, escaped on the way in.

    Escaping here rather than at the call site is deliberate: a caller who
    forgot would produce a file that burns in fine and carries a directive, and
    nothing downstream inspects a subtitle document for override blocks.

    No cues is a valid document. A clip whose span held no eligible segment
    renders without captions rather than failing the burn-in on a malformed
    file — the coverage declaration is what tells an operator why.

    **The profile is required and keyword-only.** There is no signature that
    renders a document without one, which is what makes "a margin is never
    shared between profiles" a property of the wiring rather than of two call
    sites agreeing.
    """
    events = "".join(
        f"Dialogue: 0,{_stamp(cue.start_s)},{_stamp(cue.end_s)},Default,,0,0,0,,"
        f"{_wrap(escape_cue_text(cue.text))}\n"
        for cue in cues
    )
    return _header(profile) + events


def _wrap(text: str) -> str:
    """Break once, at the word boundary nearest the middle.

    **After escaping, never before.** That ordering is what makes "the only
    breaks in a document are ones this module inserted" true structurally rather
    than by convention — source text carrying a backslash has already lost it by
    the time this runs.

    Balanced rather than greedy: two lines of similar length read as a caption,
    while a full line above a single word reads as a mistake. One break only, so
    a cue the builder oversized still renders as a caption rather than a wall of
    text — bounding the length is the builder's job, not this module's.
    """
    if len(text) <= MAX_LINE_CHARS:
        return text

    spaces = [i for i, character in enumerate(text) if character == " "]
    if not spaces:
        # A single unbroken run — a hallucinated one, in practice. Breaking
        # mid-word reads as an error, so it is left long and the builder's
        # length bound is what keeps it off the screen.
        return text

    middle = len(text) / 2
    at = min(spaces, key=lambda index: abs(index - middle))
    return f"{text[:at]}{LINE_BREAK}{text[at + 1 :]}"


def _stamp(seconds: float) -> str:
    """`H:MM:SS.cc`, ASS's own shape.

    Centiseconds, not milliseconds. A stamp in the wrong shape is not rejected
    by libass — it is read as zero, so every caption would land at the start of
    the clip and nothing would report it.
    """
    centiseconds = round(seconds * 100)
    hours, rest = divmod(centiseconds, 360_000)
    minutes, rest = divmod(rest, 6_000)
    whole, hundredths = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{whole:02d}.{hundredths:02d}"
