"""Composing the one render pass, and keeping paths out of the filter string.

`-filter_complex` is the one place in this adapter where argv's safety argument
stops applying. List form protects the *tokens* — nothing splits them on `;` or a
backtick, because nothing parses them as a command line. But the filter string is
a single token that **ffmpeg itself parses**, with its own grammar: `:` separates
options, `,` separates filters, `'` quotes, `\\` escapes.

A job directory is `C:\\Users\\...` on this machine. Interpolating it into a
filter argument puts a drive-letter colon and two backslashes inside a string
whose parser treats both as syntax — and a job directory an operator named with
an apostrophe would close a quote. So the graph never sees a path: it references
`<clip_id>.cmds` and `<clip_id>.ass` by **bare filename**, and the composer
reports the directory those resolve against so the caller can set it as the
process working directory.

That leaves one thing to guard, and it is why the id is validated first. A bare
filename built from an unvalidated `clip_id` would be an injection point of its
own — a value containing `/`, `..` or a quote would escape the directory or the
quoting. `clip_id` is a ULID and the regex already exists; checking it *before*
composition means the filter string is built from twenty-six characters drawn
from a fixed alphabet.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.ffmpeg.argv import (
    FFMPEG_BINARY,
    RenderInvocation,
    build_render_argv,
)
from onevoicecut.domain.errors import ClipRangeInvalid
from onevoicecut.domain.framing import TimeSpan
from onevoicecut.domain.ids import InvalidIdError, make_clip_id
from onevoicecut.domain.rendering import OutputSpec

CLIP_ID = make_clip_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")
SPAN = TimeSpan(120.0, 150.0)
OUTPUT = OutputSpec(width=1080, height=1920)
CROP_W, CROP_H = 606, 1080


def _invocation(
    job_dir: Path,
    *,
    span: TimeSpan = SPAN,
    clip_id: str = CLIP_ID,
) -> RenderInvocation:
    return build_render_argv(
        source=job_dir / "source",
        dest=job_dir / "render" / f"{clip_id}.mp4",
        job_dir=job_dir,
        clip_id=clip_id,
        span=span,
        crop_size=(CROP_W, CROP_H),
        output=OUTPUT,
    )


def _filter_string(argv: list[str]) -> str:
    return argv[argv.index("-filter_complex") + 1]


class TestTheSinglePass:
    def test_it_is_one_ffmpeg_invocation(self, tmp_path: Path) -> None:
        """The spec calls for a single native pass. Two invocations would
        re-encode the intermediate, and a vertical clip is the one artifact an
        operator publishes."""
        argv = _invocation(tmp_path).argv

        assert argv.count(FFMPEG_BINARY) == 1

    def test_one_filter_complex_chains_the_four_stages(self, tmp_path: Path) -> None:
        graph = _filter_string(_invocation(tmp_path).argv)

        assert graph.index("sendcmd") < graph.index("crop")
        assert graph.index("crop") < graph.index("scale")
        assert graph.index("scale") < graph.index("subtitles")

    def test_only_one_filter_complex_is_passed(self, tmp_path: Path) -> None:
        """A second would silently replace the first rather than adding to it."""
        assert _invocation(tmp_path).argv.count("-filter_complex") == 1

    def test_seeking_happens_before_the_input(self, tmp_path: Path) -> None:
        """`-ss` after `-i` decodes everything up to the start point. Reaching
        two minutes into a three-hour recording that way costs more than the
        render itself."""
        argv = _invocation(tmp_path).argv

        assert argv.index("-ss") < argv.index("-i")

    def test_the_duration_is_the_spans_own(self, tmp_path: Path) -> None:
        argv = _invocation(tmp_path).argv

        assert argv[argv.index("-t") + 1] == "30.000"

    def test_source_and_destination_are_absolute(self, tmp_path: Path) -> None:
        """An absolute path cannot begin with `-`, so a positional filename can
        never be re-read by ffmpeg as an option."""
        argv = _invocation(tmp_path).argv

        assert Path(argv[argv.index("-i") + 1]).is_absolute()
        assert Path(argv[-1]).is_absolute()

    def test_the_crop_size_is_the_one_it_was_given(self, tmp_path: Path) -> None:
        """Never recomputed here. Stage 1 fixed it, `CropTrajectory` holds it,
        and re-deriving it in the composer would be a second answer."""
        graph = _filter_string(_invocation(tmp_path).argv)

        assert f"w={CROP_W}" in graph and f"h={CROP_H}" in graph


class TestNoPathReachesTheFilterString:
    def test_the_command_file_is_a_bare_filename(self, tmp_path: Path) -> None:
        graph = _filter_string(_invocation(tmp_path).argv)

        assert f"{CLIP_ID}.cmds" in graph

    def test_the_subtitle_file_is_a_bare_filename(self, tmp_path: Path) -> None:
        graph = _filter_string(_invocation(tmp_path).argv)

        assert f"{CLIP_ID}.ass" in graph

    def test_the_job_directory_never_appears(self, tmp_path: Path) -> None:
        """The assertion the whole design exists for."""
        graph = _filter_string(_invocation(tmp_path).argv)

        assert str(tmp_path) not in graph
        assert tmp_path.name not in graph

    def test_no_render_prefix_appears_either(self, tmp_path: Path) -> None:
        """Not even a relative one. `render/<clip>.cmds` would put a separator
        in the filter string, and the graph resolves against that directory
        already."""
        graph = _filter_string(_invocation(tmp_path).argv)

        assert "render/" not in graph and "render\\" not in graph

    def test_the_graph_carries_no_path_separator_at_all(self, tmp_path: Path) -> None:
        graph = _filter_string(_invocation(tmp_path).argv)

        assert "/" not in graph and "\\" not in graph

    @pytest.mark.parametrize("hostile", ["with'quote", "with,comma"])
    def test_a_hostile_job_directory_name_still_never_reaches_it(
        self, hostile: str, tmp_path: Path
    ) -> None:
        """Both carry syntax meaning inside a filter argument — `,` separates
        filters and `'` quotes — and an operator naming a directory is not
        thinking about ffmpeg's grammar.

        A colon would belong here and cannot: Windows refuses it in a directory
        name outright, because it separates an NTFS alternate data stream. The
        colon risk is covered anyway and more strongly, since every absolute path
        on this platform carries a drive-letter one and
        `test_the_job_directory_never_appears` asserts the whole path is absent.
        """
        job_dir = tmp_path / hostile
        job_dir.mkdir()

        graph = _filter_string(_invocation(job_dir).argv)

        assert hostile not in graph

    def test_the_composer_reports_the_directory_to_resolve_against(
        self, tmp_path: Path
    ) -> None:
        """Bare filenames are only safe if somebody sets the cwd. Returning it
        beside the argv makes that the caller's obligation rather than a fact
        buried in a docstring."""
        invocation = _invocation(tmp_path)

        assert invocation.cwd == (tmp_path / "render").resolve()


class TestTheClipIdIsValidatedFirst:
    @pytest.mark.parametrize(
        "bad", ["../escape", "a/b", "not-a-ulid", "01HQ3M8XKJ7VNPQR2ZYWB4TCF", "'"]
    )
    def test_a_non_ulid_is_refused(self, bad: str, tmp_path: Path) -> None:
        """Before composition, because the filter string is built from it. A
        value carrying `/`, `..` or a quote would escape the directory or the
        quoting — the one injection point bare filenames leave open."""
        with pytest.raises(InvalidIdError):
            _invocation(tmp_path, clip_id=bad)

    def test_a_valid_ulid_composes(self, tmp_path: Path) -> None:
        assert _invocation(tmp_path).argv


class TestTheSpanIsSane:
    def test_a_reversed_span_never_gets_this_far(self) -> None:
        """The composer does not need to catch it, and finding that out is the
        point of writing the test.

        `TimeSpan` refuses a reversed pair in `__post_init__`, so `-t` can never
        be negative — which matters because ffmpeg reads a negative duration as
        "until the end", rendering the rest of a three-hour sermon into what was
        supposed to be a thirty-second clip. The guard lives one layer down,
        where every consumer gets it rather than only this one.
        """
        with pytest.raises(ValueError):
            TimeSpan(150.0, 120.0)

    def test_a_zero_length_span_is_refused_here(self, tmp_path: Path) -> None:
        """Zero length is legal to *construct* — a span of no duration is a
        coherent value — and meaningless to *render*. So this is the layer that
        refuses it, and `ClipRangeInvalid` says whose error it is: the caller
        asked for a clip with no footage in it."""
        with pytest.raises(ClipRangeInvalid):
            _invocation(tmp_path, span=TimeSpan(120.0, 120.0))
