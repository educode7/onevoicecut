"""What the render adapter does to `subprocess` and to the job directory, without ffmpeg.

Every test here substitutes the runner, so the suite proves how the adapter
*invokes* ffmpeg -- one process, launched from the directory whose bare filenames
the filter graph references, with both sidecars already written into it -- and
never that ffmpeg produces a watchable file. That second claim needs the real
binary and lives in the `integration`-marked unit that follows.

**The working directory is load-bearing here, not incidental.**
`build_render_argv` refuses to put a path inside `-filter_complex`, so
`<clip>.cmds` and `<clip>.ass` are bare filenames. An adapter that wrote them
somewhere else, or spawned from somewhere else, would compose a graph naming two
files ffmpeg cannot open -- and would fail at render time, hours after the
transcription that produced the cues.

**Only one of the two is the adapter's to write**, which the `prepared_render_dir`
fixture below is the shape of. The trajectory determines the command file
completely; the subtitle document needs a caption safe area that lives on a
render profile the request does not carry, so the caller that resolved the
profile writes it and the adapter refuses to spawn without it.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from onevoicecut.adapters.ffmpeg.argv import build_render_argv
from onevoicecut.adapters.ffmpeg.sendcmd import build_sendcmd_script
from onevoicecut.adapters.ffmpeg.subtitles import render_ass
from onevoicecut.adapters.ffmpeg.video_render import FfmpegVideoRenderer
from onevoicecut.domain.errors import (
    ClipRangeInvalid,
    DomainError,
    FfmpegUnavailable,
    RenderFailed,
)
from onevoicecut.domain.framing import (
    CropKeyframe,
    CropRect,
    CropTrajectory,
    KeyframeOrigin,
    TimeSpan,
    TrackingConfidence,
)
from onevoicecut.domain.ids import InvalidIdError, make_media_id
from onevoicecut.domain.media import SourceMedia
from onevoicecut.domain.rendering import (
    OutputSpec,
    RenderProfile,
    SafeArea,
    SubtitleCue,
)
from onevoicecut.ports.capabilities import RenderSupport
from onevoicecut.ports.video_render import RenderRequest

CLIP_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFD"
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")
SPAN = TimeSpan(120.0, 150.0)
OUTPUT = OutputSpec(width=1080, height=1920)
CROP = CropRect(x=40, y=0, width=606, height=1080)
CUES = (SubtitleCue(start_s=0.0, end_s=2.0, text="hermanos, buenos dias"),)
# Only ever used to produce the `.ass` this adapter treats as prepared input.
# Declared here rather than taken from `RENDER_PROFILES`: the shipped registry's
# safe area is a re-measurement-sensitive value, and `render_ass` only requires
# that a profile be measured at all — an unmeasured one it refuses by name.
CAPTION_PROFILE = RenderProfile(
    name="vertical",
    output=OUTPUT,
    safe_area=SafeArea(top=0.06, bottom=0.18, left=0.05, right=0.14),
    max_duration_s=90.0,
)


class RecordingRunner:
    """Captures the call instead of spawning anything.

    It also lists the working directory it was handed, because "the sidecars
    exist by the time ffmpeg starts" is the claim -- checking after `render`
    returns would pass for an adapter that wrote them afterwards.
    """

    def __init__(self, *, returncode: int = 0, stderr: str = "") -> None:
        self.calls: list[tuple[list[str], Path, float]] = []
        self.saw_in_cwd: list[str] = []
        self._returncode = returncode
        self._stderr = stderr

    def __call__(
        self, argv: list[str], *, cwd: Path, timeout_s: float
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, cwd, timeout_s))
        self.saw_in_cwd = sorted(entry.name for entry in cwd.iterdir())
        if self._returncode == 0:
            Path(argv[-1]).write_bytes(b"x" * 2048)  # stands in for ffmpeg's output
        return subprocess.CompletedProcess(argv, self._returncode, "", self._stderr)

    @property
    def argv(self) -> list[str]:
        return self.calls[-1][0]

    @property
    def cwd(self) -> Path:
        return self.calls[-1][1]


@pytest.fixture
def without_ffmpeg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Overrides the package fixture, which assumes both binaries are present.

    Asserting against the real PATH would make these tests pass or fail on
    whether this machine happens to have ffmpeg, which is exactly the coupling
    the injected runner exists to remove.
    """
    monkeypatch.setattr(shutil, "which", lambda _name: None)


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "jobs" / "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    directory.mkdir(parents=True)
    return directory


@pytest.fixture(autouse=True)
def prepared_render_dir(job_dir: Path) -> Path:
    """The render directory as a caller hands it over: the `.ass` already in it.

    Autouse because it is the adapter's precondition rather than one test's
    setup -- a clip is never rendered without a subtitle document, and the one
    test that proves the refusal removes the file rather than skipping this.
    """
    render_dir = job_dir / "render"
    render_dir.mkdir(parents=True, exist_ok=True)
    (render_dir / f"{CLIP_ID}.ass").write_text(
        render_ass(CUES, profile=CAPTION_PROFILE), encoding="utf-8", newline="\n"
    )
    return render_dir


@pytest.fixture
def media(job_dir: Path) -> SourceMedia:
    source = job_dir / "source"
    source.write_bytes(b"not really a video")
    return SourceMedia(
        media_id=MEDIA_ID,
        original_filename="predicacion; rm -rf ~.mp4",
        stored_path=source,
        size_bytes=18,
        container="mp4",
        checksum="deadbeef",
    )


def _trajectory() -> CropTrajectory:
    return CropTrajectory(
        keyframes=(
            CropKeyframe(at_s=0.0, rect=CROP, origin=KeyframeOrigin.TRACKED),
            CropKeyframe(
                at_s=1.0,
                rect=CropRect(x=52, y=0, width=606, height=1080),
                origin=KeyframeOrigin.TRACKED,
            ),
        ),
        tracking=TrackingConfidence.WELL_TRACKED,
    )


def _request(media: SourceMedia, *, span: TimeSpan = SPAN) -> RenderRequest:
    return RenderRequest(
        media=media,
        span=span,
        trajectory=_trajectory(),
        cues=CUES,
        output=OUTPUT,
    )


def _dest(job_dir: Path, *, name: str = f"{CLIP_ID}.mp4") -> Path:
    return job_dir / "render" / name


class TestTheOneProcess:
    def test_exactly_one_process_is_spawned(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """The spec's single-native-pass requirement, asserted at the only layer
        that could spawn twice. A second invocation -- even an ffprobe of the
        adapter's own output -- would put a decode between decode and encode."""
        runner = RecordingRunner()
        FfmpegVideoRenderer(job_dir, runner=runner).render(
            _request(media), _dest(job_dir)
        )

        assert len(runner.calls) == 1

    def test_the_argv_is_the_composers_own(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        runner = RecordingRunner()
        FfmpegVideoRenderer(job_dir, runner=runner).render(
            _request(media), _dest(job_dir)
        )

        expected = build_render_argv(
            source=media.stored_path,
            dest=_dest(job_dir),
            job_dir=job_dir,
            clip_id=CLIP_ID,
            span=SPAN,
            crop_size=(CROP.width, CROP.height),
            output=OUTPUT,
        )
        assert runner.argv == expected.argv

    def test_it_runs_from_the_directory_the_bare_filenames_resolve_against(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        runner = RecordingRunner()
        FfmpegVideoRenderer(job_dir, runner=runner).render(
            _request(media), _dest(job_dir)
        )

        assert runner.cwd == (job_dir / "render").resolve()


class TestTheSidecarsAreThereBeforeFfmpegIs:
    def test_the_command_file_is_written_into_the_working_directory(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        runner = RecordingRunner()
        FfmpegVideoRenderer(job_dir, runner=runner).render(
            _request(media), _dest(job_dir)
        )

        assert f"{CLIP_ID}.cmds" in runner.saw_in_cwd

    def test_the_callers_subtitle_document_is_in_the_working_directory(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """Written by whoever resolved the profile, and found here by its bare
        name -- which only works because the spawn happens in this directory."""
        runner = RecordingRunner()
        FfmpegVideoRenderer(job_dir, runner=runner).render(
            _request(media), _dest(job_dir)
        )

        assert f"{CLIP_ID}.ass" in runner.saw_in_cwd

    def test_the_command_file_carries_the_trajectorys_own_script(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """Written by the module that owns the format, never composed here --
        `sendcmd`'s line grammar is ffmpeg's, and a second author would drift."""
        FfmpegVideoRenderer(job_dir, runner=RecordingRunner()).render(
            _request(media), _dest(job_dir)
        )

        written = (job_dir / "render" / f"{CLIP_ID}.cmds").read_text(encoding="utf-8")
        assert written == build_sendcmd_script(_trajectory())

    def test_the_subtitle_document_is_left_exactly_as_the_caller_wrote_it(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """The adapter has no profile, so anything it rewrote here would carry a
        margin nobody measured -- the inherited default the safe-area axis
        exists to refuse."""
        prepared = (job_dir / "render" / f"{CLIP_ID}.ass").read_text(encoding="utf-8")

        FfmpegVideoRenderer(job_dir, runner=RecordingRunner()).render(
            _request(media), _dest(job_dir)
        )

        after = (job_dir / "render" / f"{CLIP_ID}.ass").read_text(encoding="utf-8")
        assert after == prepared

    def test_a_missing_subtitle_document_is_refused_before_spawning(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """ffmpeg's own answer is a filter-graph failure after it has opened the
        source and seeked, phrased as a complaint about a bare filename. This
        one names the obligation instead, and costs a `stat`."""
        (job_dir / "render" / f"{CLIP_ID}.ass").unlink()
        runner = RecordingRunner()

        with pytest.raises(RenderFailed, match="subtitle"):
            FfmpegVideoRenderer(job_dir, runner=runner).render(
                _request(media), _dest(job_dir)
            )

        assert runner.calls == []

    def test_both_are_written_with_line_feeds_whatever_the_platform(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """ffmpeg parses these files, not this project. The platform default
        would put a carriage return inside every `sendcmd` line and every ASS
        event, on the one platform this app is deployed to."""
        FfmpegVideoRenderer(job_dir, runner=RecordingRunner()).render(
            _request(media), _dest(job_dir)
        )

        for name in (f"{CLIP_ID}.cmds", f"{CLIP_ID}.ass"):
            assert b"\r\n" not in (job_dir / "render" / name).read_bytes()


class TestWhatItHandsBack:
    def test_it_reports_the_profiles_output_dimensions(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        rendered = FfmpegVideoRenderer(job_dir, runner=RecordingRunner()).render(
            _request(media), _dest(job_dir)
        )

        assert (rendered.width, rendered.height) == (OUTPUT.width, OUTPUT.height)

    def test_it_reports_the_spans_duration_and_the_destination(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        rendered = FfmpegVideoRenderer(job_dir, runner=RecordingRunner()).render(
            _request(media), _dest(job_dir)
        )

        assert rendered.duration_s == SPAN.duration_s
        assert rendered.path == _dest(job_dir).resolve()


class TestFailuresAreDomainErrors:
    def test_a_non_zero_exit_becomes_render_failed(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        runner = RecordingRunner(returncode=1, stderr="Invalid argument")
        with pytest.raises(RenderFailed, match="Invalid argument"):
            FfmpegVideoRenderer(job_dir, runner=runner).render(
                _request(media), _dest(job_dir)
            )

    def test_a_success_that_produced_no_file_becomes_render_failed(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """The shipped extractor already refuses this shape. A zero exit that
        wrote nothing is a failure, not a zero-byte clip."""

        def wrote_nothing(
            argv: list[str], *, cwd: Path, timeout_s: float
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(argv, 0, "", "")

        with pytest.raises(RenderFailed, match="no output"):
            FfmpegVideoRenderer(job_dir, runner=wrote_nothing).render(
                _request(media), _dest(job_dir)
            )

    def test_a_zero_length_span_is_refused_before_spawning(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """Legal to construct, meaningless to render. The composer owns this
        refusal and the adapter must not reach a spawn past it."""
        runner = RecordingRunner()
        with pytest.raises(ClipRangeInvalid):
            FfmpegVideoRenderer(job_dir, runner=runner).render(
                _request(media, span=TimeSpan(120.0, 120.0)), _dest(job_dir)
            )

        assert runner.calls == []


class TestContainment:
    def test_a_destination_outside_the_job_directory_is_refused_before_spawning(
        self, job_dir: Path, media: SourceMedia, tmp_path: Path
    ) -> None:
        runner = RecordingRunner()
        with pytest.raises(DomainError, match="outside"):
            FfmpegVideoRenderer(job_dir, runner=runner).render(
                _request(media), tmp_path / f"{CLIP_ID}.mp4"
            )

        assert runner.calls == []

    def test_a_source_outside_the_job_directory_is_refused_before_spawning(
        self, job_dir: Path, tmp_path: Path
    ) -> None:
        outside = tmp_path / "elsewhere.mp4"
        outside.write_bytes(b"x")
        stray = SourceMedia(
            media_id=MEDIA_ID,
            original_filename="elsewhere.mp4",
            stored_path=outside,
            size_bytes=1,
            container="mp4",
            checksum="d",
        )
        runner = RecordingRunner()
        with pytest.raises(DomainError, match="outside"):
            FfmpegVideoRenderer(job_dir, runner=runner).render(
                _request(stray), _dest(job_dir)
            )

        assert runner.calls == []

    def test_the_sidecars_land_inside_the_job_directory(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """Two files this adapter creates and then names inside a string ffmpeg
        parses. Neither may be written through a path the containment check
        never saw."""
        runner = RecordingRunner()
        FfmpegVideoRenderer(job_dir, runner=runner).render(
            _request(media), _dest(job_dir)
        )

        assert runner.cwd.is_relative_to(job_dir.resolve())

    def test_a_destination_not_named_for_a_clip_is_refused(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """The filter graph is built from the destination's stem, because
        `RenderRequest` carries no clip id of its own. An arbitrary stem would
        be composed into a string ffmpeg parses, so it is validated as a ULID
        first -- the composer's guard, reached through the adapter."""
        runner = RecordingRunner()
        with pytest.raises(InvalidIdError):
            FfmpegVideoRenderer(job_dir, runner=runner).render(
                _request(media), _dest(job_dir, name="escape.mp4")
            )

        assert runner.calls == []


class TestWhatItDeclares:
    def test_it_declares_rendering_available_when_ffmpeg_is_on_path(
        self, job_dir: Path
    ) -> None:
        capabilities = FfmpegVideoRenderer(
            job_dir, runner=RecordingRunner()
        ).capabilities()

        assert capabilities.rendering is RenderSupport.AVAILABLE
        assert capabilities.renderer_id

    def test_it_declares_the_clip_bound_it_was_configured_with(
        self, job_dir: Path
    ) -> None:
        """Declared, never enforced here: the guard runs above the port, before
        anything is spawned. A bound only the adapter could apply would be a
        bound nobody upstream can read."""
        renderer = FfmpegVideoRenderer(
            job_dir, runner=RecordingRunner(), max_clip_seconds=180.0
        )

        assert renderer.capabilities().max_clip_seconds == 180.0


class TestTheRenderTimeout:
    """Twenty times realtime, floored at a minute.

    Two orders of magnitude tighter than extraction's four-hour ceiling, and
    deliberately so: extraction bounds a hung process over a multi-hour input,
    while a clip is minutes. A render that has been going for twenty times the
    footage it was given is hung, not slow.
    """

    def test_the_bound_scales_with_the_clip(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        runner = RecordingRunner()
        FfmpegVideoRenderer(job_dir, runner=runner).render(
            _request(media), _dest(job_dir)
        )

        assert runner.calls[-1][2] == 20 * SPAN.duration_s

    def test_a_very_short_clip_still_gets_a_floor(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """A two-second clip pays codec startup like any other. A bound
        proportional to duration alone would kill the shortest renders first."""
        runner = RecordingRunner()
        FfmpegVideoRenderer(job_dir, runner=runner).render(
            _request(media, span=TimeSpan(120.0, 122.0)), _dest(job_dir)
        )

        assert runner.calls[-1][2] == 60.0

    def test_an_overrun_becomes_a_domain_error(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """`TimeoutExpired` is a `subprocess` type. A caller that had to catch
        it would be catching the library this port exists to hide."""

        def hung(
            argv: list[str], *, cwd: Path, timeout_s: float
        ) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout_s)

        with pytest.raises(RenderFailed, match="timed out"):
            FfmpegVideoRenderer(job_dir, runner=hung).render(
                _request(media), _dest(job_dir)
            )

    def test_the_overrun_is_not_a_range_error(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """The two types decide retry-or-refuse. A hung render is worth another
        attempt; an impossible range never is, and collapsing them would make
        that call unavailable to every caller."""

        def hung(
            argv: list[str], *, cwd: Path, timeout_s: float
        ) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout_s)

        with pytest.raises(RenderFailed) as caught:
            FfmpegVideoRenderer(job_dir, runner=hung).render(
                _request(media), _dest(job_dir)
            )

        assert not isinstance(caught.value, ClipRangeInvalid)
        assert str(caught.value).find("600") >= 0  # the bound it actually applied


class TestTheBinaryMustBeThereFirst:
    """ffmpeg is a system binary a machine can lose between one job and the next.

    The shipped extractor already answers this with a sentence naming what to
    install; a second adapter that answered with `FileNotFoundError: [WinError 2]`
    would make the same missing binary readable in one half of this app and not
    the other.
    """

    def test_a_missing_binary_is_a_domain_error(
        self, without_ffmpeg: None, job_dir: Path, media: SourceMedia
    ) -> None:
        with pytest.raises(FfmpegUnavailable, match="ffmpeg"):
            FfmpegVideoRenderer(job_dir, runner=RecordingRunner()).render(
                _request(media), _dest(job_dir)
            )

    def test_nothing_is_spawned_when_the_binary_is_missing(
        self, without_ffmpeg: None, job_dir: Path, media: SourceMedia
    ) -> None:
        runner = RecordingRunner()
        with pytest.raises(FfmpegUnavailable):
            FfmpegVideoRenderer(job_dir, runner=runner).render(
                _request(media), _dest(job_dir)
            )

        assert runner.calls == []

    def test_the_message_is_the_shipped_one(
        self, without_ffmpeg: None, job_dir: Path, media: SourceMedia
    ) -> None:
        """Reused rather than rewritten. Two adapters composing their own
        remediation text is how one of them ends up out of date."""
        with pytest.raises(FfmpegUnavailable) as caught:
            FfmpegVideoRenderer(job_dir, runner=RecordingRunner()).render(
                _request(media), _dest(job_dir)
            )

        message = str(caught.value).lower()
        assert "install" in message
        assert "path" in message
        assert "pip" in message  # states explicitly that it is not one

    def test_a_binary_removed_after_the_check_still_fails_cleanly(
        self, job_dir: Path, media: SourceMedia
    ) -> None:
        """`which` succeeding does not guarantee the spawn will: the binary can
        vanish in between, and a stale PATH entry can name a deleted directory."""

        def vanished(
            argv: list[str], *, cwd: Path, timeout_s: float
        ) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError(2, "The system cannot find the file specified")

        with pytest.raises(FfmpegUnavailable, match="ffmpeg"):
            FfmpegVideoRenderer(job_dir, runner=vanished).render(
                _request(media), _dest(job_dir)
            )

    def test_it_declares_setup_is_required_rather_than_claiming_availability(
        self, without_ffmpeg: None, job_dir: Path
    ) -> None:
        """"This build ships no renderer" and "ffmpeg is not on this machine"
        send an operator to two different places, which is why `RenderSupport`
        has three members and this adapter can only ever report the latter two."""
        capabilities = FfmpegVideoRenderer(
            job_dir, runner=RecordingRunner()
        ).capabilities()

        assert capabilities.rendering is RenderSupport.REQUIRES_SETUP

    def test_the_check_is_cached_across_renders(
        self, job_dir: Path, media: SourceMedia, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One profile per destination means several renders of one clip, and
        re-scanning PATH before each is waste the shipped adapter already
        refuses."""
        lookups: list[str] = []

        def counting_which(name: str) -> str:
            lookups.append(name)
            return f"/usr/bin/{name}"

        monkeypatch.setattr(shutil, "which", counting_which)

        renderer = FfmpegVideoRenderer(job_dir, runner=RecordingRunner())
        renderer.render(_request(media), _dest(job_dir))
        renderer.render(_request(media), _dest(job_dir))

        assert lookups.count("ffmpeg") == 1
