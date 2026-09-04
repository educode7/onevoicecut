"""Cue text is ASR output, and ASR output is model output.

The threat-matrix row this closes is not hypothetical politeness. `{...}` is an
ASS override block: it can move the caption off-screen, change its colour, or
swallow the rest of the line. A decoder that hallucinates `{\\an8}` — and
Whisper-family decoders hallucinate confidently — would be authoring subtitle
directives inside a clip nobody reviewed.

Three characters carry syntax meaning here and none carries meaning in
transcribed Spanish speech, so each is **substituted rather than escaped**: ASS
has no reliable literal escape for any of the three. `{` and `}` become
parentheses, `\\` becomes a forward slash. That substitution is visible and
lossy, which is the honest trade — the alternative is a caption that renders as
something the preacher did not say, or does not render at all.

The backslash matters more than it looks. A lone `\\` followed by a capital N is
`\\N`, ASS's hard line break: the text `\\Nada` would render as a break plus
"ada". So escaping runs **before** intended breaks are inserted, and a test pins
that order.
"""

from onevoicecut.adapters.ffmpeg.subtitles import (
    LINE_BREAK,
    escape_cue_text,
    render_ass,
)
from onevoicecut.domain.rendering import SubtitleCue


def _dialogue_lines(document: str) -> list[str]:
    return [line for line in document.splitlines() if line.startswith("Dialogue:")]


class TestHostileText:
    def test_an_override_block_cannot_survive(self) -> None:
        """`{\\an8}` repositions the caption. A decoder that invents it would be
        authoring subtitle directives inside a clip nobody reviewed."""
        escaped = escape_cue_text("{\\an8}hola hermanos")

        assert "{" not in escaped
        assert "}" not in escaped

    def test_a_lone_closing_brace_is_neutralised(self) -> None:
        """Unmatched braces are the case a naive "strip matched pairs" misses,
        and libass's behaviour on them is not something to depend on."""
        assert "}" not in escape_cue_text("hola } hermanos")

    def test_a_lone_backslash_cannot_become_a_line_break(self) -> None:
        """The subtle one. `\\Nada` is a hard break followed by "ada", so a
        backslash that survives escaping can silently delete a syllable."""
        escaped = escape_cue_text("\\Nada mas")

        assert "\\" not in escaped
        assert "Nada mas" in escaped

    def test_carriage_returns_and_newlines_are_stripped(self) -> None:
        """A dialogue line is one line by definition. A CR/LF in the text ends
        the event early and turns the remainder into a malformed line."""
        escaped = escape_cue_text("hola\r\nhermanos\nqueridos")

        assert "\r" not in escaped
        assert "\n" not in escaped

    def test_a_long_hallucinated_run_stays_one_line(self) -> None:
        """Length is bounded by the cue builder, not here — but whatever arrives
        must still emit a single well-formed event rather than wrapping."""
        document = render_ass((SubtitleCue(0.0, 2.0, "a" * 5000),))

        assert len(_dialogue_lines(document)) == 1


class TestEscapingOrder:
    def test_source_text_cannot_produce_an_intended_break(self) -> None:
        """Escaping runs before breaks are inserted. Reversed, a source `\\N`
        would survive as a real break and the caption would split where the
        model hallucinated rather than where the builder chose."""
        assert LINE_BREAK not in escape_cue_text("uno\\Ndos")

    def test_the_renderer_is_what_inserts_a_break(self) -> None:
        """Cue text stays plain and this module wraps it, so the only breaks in
        a document are ones inserted *after* escaping.

        The alternative — a sentinel the builder writes and this module converts
        — is a secret handshake between two modules, and a caller applying it in
        the wrong order reintroduces exactly the injection being prevented.
        """
        long_line = "hermanos queridos de la iglesia escuchen esto con atencion"

        assert LINE_BREAK in render_ass((SubtitleCue(0.0, 2.0, long_line),))

    def test_a_short_cue_is_left_on_one_line(self) -> None:
        """Wrapping is for readability, not ceremony."""
        assert LINE_BREAK not in render_ass((SubtitleCue(0.0, 1.0, "hola"),))

    def test_a_wrapped_cue_is_still_one_dialogue_line(self) -> None:
        """`\\N` is a break *within* an event. A real newline would end the
        event and malform the file."""
        long_line = "hermanos queridos de la iglesia escuchen esto con atencion"

        assert len(_dialogue_lines(render_ass((SubtitleCue(0.0, 2.0, long_line),)))) == 1

    def test_a_break_falls_on_a_word_boundary(self) -> None:
        """Breaking mid-word is the one wrap that reads as an error rather than
        as a caption."""
        long_line = "hermanos queridos de la iglesia escuchen esto con atencion"
        text = render_ass((SubtitleCue(0.0, 2.0, long_line),)).rsplit(",,", 1)[1]

        assert all(part.strip() == part for part in text.strip().split(LINE_BREAK))

    def test_ordinary_spanish_text_is_untouched(self) -> None:
        """The escaping must not be a tax on the normal case, which is every
        caption of every sermon."""
        assert escape_cue_text("¿Qué dijo el señor? Amén.") == "¿Qué dijo el señor? Amén."


class TestTheDocument:
    def test_it_carries_the_sections_libass_needs(self) -> None:
        document = render_ass((SubtitleCue(0.0, 1.0, "hola"),))

        assert "[Script Info]" in document
        assert "[V4+ Styles]" in document
        assert "[Events]" in document

    def test_one_dialogue_line_per_cue(self) -> None:
        cues = (
            SubtitleCue(0.0, 1.0, "uno"),
            SubtitleCue(1.0, 2.0, "dos"),
            SubtitleCue(2.0, 3.0, "tres"),
        )

        assert len(_dialogue_lines(render_ass(cues))) == 3

    def test_no_cues_still_produces_a_valid_document(self) -> None:
        """A clip whose span held no eligible segment renders with no captions
        rather than with a broken subtitle file that fails the burn-in."""
        document = render_ass(())

        assert "[Events]" in document
        assert _dialogue_lines(document) == []

    def test_times_are_centisecond_ass_stamps(self) -> None:
        """`H:MM:SS.cc`. A stamp in the wrong shape is not rejected by libass —
        it is read as zero, and every caption lands at the start of the clip."""
        line = _dialogue_lines(render_ass((SubtitleCue(3661.5, 3662.25, "x"),)))[0]

        assert "1:01:01.50" in line
        assert "1:01:02.25" in line

    def test_a_cue_at_zero_is_stamped_from_zero(self) -> None:
        """Cues are clip-local, so the first one legitimately starts at 0.00."""
        line = _dialogue_lines(render_ass((SubtitleCue(0.0, 1.0, "x"),)))[0]

        assert "0:00:00.00" in line

    def test_the_text_is_the_last_field_so_commas_survive(self) -> None:
        """A dialogue line is comma-separated and the text field is last, which
        is what lets Spanish punctuation through untouched."""
        line = _dialogue_lines(render_ass((SubtitleCue(0.0, 1.0, "hola, hermanos"),)))[0]

        assert line.endswith("hola, hermanos")

    def test_hostile_text_is_escaped_on_the_way_into_the_document(self) -> None:
        """The escaping is not something a caller has to remember to apply."""
        document = render_ass((SubtitleCue(0.0, 1.0, "{\\an8}hola"),))

        assert "{" not in document.split("[Events]")[1]
