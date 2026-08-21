"""ffmpeg argv composition and path containment — the two applicable threat rows.

Uploaded filenames are attacker-controlled. They reach this layer as metadata
only, but the extraction step still has to name a file on a command line, and
that is where a shell would turn `a; rm -rf ~` into two commands. Nothing here
spawns a process: argv composition is a pure function precisely so the hostile
cases can be asserted without ffmpeg installed.
"""

from pathlib import Path

import pytest

from transcribe.adapters.ffmpeg.argv import (
    FFMPEG_BINARY,
    FFPROBE_BINARY,
    build_extract_argv,
    build_probe_argv,
    resolve_inside,
)
from transcribe.domain.errors import ExtractionFailed

HOSTILE_NAMES = [
    "clip; rm -rf ~.mp4",
    "clip && shutdown.mp4",
    "clip | tee out.mp4",
    "clip `whoami`.mp4",
    "clip $(whoami).mp4",
    "clip with spaces.mp4",
    "--version.mp4",
    "-i.mp4",
    "-f lavfi.mp4",
    "clip'quote.mp4",
    'clip"quote.mp4',
    "clip\nnewline.mp4",
    "clip&background.mp4",
    "clip>redirect.mp4",
    "clip%TEMP%.mp4",
]


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    return tmp_path / "jobs" / "01ARZ3NDEKTSV4RRFFQ69G5FAV"


# --- argv is a list, never a shell string -------------------------------------


def test_extract_argv_starts_with_the_binary(job_dir: Path) -> None:
    argv = build_extract_argv(job_dir / "source.mp4", job_dir / "audio.flac")
    assert argv[0] == FFMPEG_BINARY
    assert isinstance(argv, list)
    assert all(isinstance(token, str) for token in argv)


def test_probe_argv_starts_with_the_binary(job_dir: Path) -> None:
    argv = build_probe_argv(job_dir / "source.mp4")
    assert argv[0] == FFPROBE_BINARY


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_hostile_filename_stays_one_argv_element(job_dir: Path, name: str) -> None:
    """The whole point of list form: no token ever splits on a metacharacter."""
    argv = build_extract_argv(job_dir / name, job_dir / "audio.flac")
    resolved = str((job_dir / name).resolve())
    assert argv.count(resolved) == 1


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_hostile_filename_never_leaks_into_another_token(
    job_dir: Path, name: str
) -> None:
    resolved = str((job_dir / name).resolve())
    argv = build_extract_argv(job_dir / name, job_dir / "audio.flac")
    others = [token for token in argv if token != resolved]
    assert all(resolved not in token for token in others)


def test_source_is_the_value_of_dash_i_not_a_bare_positional(job_dir: Path) -> None:
    """A leading-dash filename after -i is consumed as its value, never re-parsed."""
    argv = build_extract_argv(job_dir / "-i.mp4", job_dir / "audio.flac")
    assert argv[argv.index("-i") + 1] == str((job_dir / "-i.mp4").resolve())


def test_every_path_token_is_absolute(job_dir: Path) -> None:
    """Absolute paths cannot begin with '-', so the positional output file can
    never be re-read as an option."""
    argv = build_extract_argv(job_dir / "-weird.mp4", job_dir / "audio.flac")
    path_tokens = [t for t in argv if ".mp4" in t or ".flac" in t]
    assert path_tokens
    assert all(Path(t).is_absolute() for t in path_tokens)
    assert not any(t.startswith("-") for t in path_tokens)


# --- the hardening flags the design mandates ----------------------------------


def test_extract_argv_disables_stdin_and_restricts_protocols(job_dir: Path) -> None:
    argv = build_extract_argv(job_dir / "source.mp4", job_dir / "audio.flac")
    assert "-nostdin" in argv
    assert argv[argv.index("-protocol_whitelist") + 1] == "file"


def test_probe_argv_restricts_protocols(job_dir: Path) -> None:
    argv = build_probe_argv(job_dir / "source.mp4")
    assert argv[argv.index("-protocol_whitelist") + 1] == "file"


def test_extract_argv_normalizes_to_16k_mono_flac(job_dir: Path) -> None:
    """Normalization is a planning decision, not just a format one — the byte-cap
    arithmetic in plan_chunks assumes this bitrate."""
    argv = build_extract_argv(job_dir / "source.mp4", job_dir / "audio.flac")
    assert argv[argv.index("-ar") + 1] == "16000"
    assert argv[argv.index("-ac") + 1] == "1"
    assert argv[argv.index("-c:a") + 1] == "flac"
    assert "-vn" in argv


def test_extract_argv_writes_the_destination_last(job_dir: Path) -> None:
    argv = build_extract_argv(job_dir / "source.mp4", job_dir / "audio.flac")
    assert argv[-1] == str((job_dir / "audio.flac").resolve())


# --- path containment ---------------------------------------------------------


def test_path_inside_the_job_dir_resolves(job_dir: Path) -> None:
    assert resolve_inside(job_dir, job_dir / "audio.flac") == (
        job_dir / "audio.flac"
    ).resolve()


def test_traversal_out_of_the_job_dir_is_rejected(job_dir: Path) -> None:
    with pytest.raises(ExtractionFailed, match="outside"):
        resolve_inside(job_dir, job_dir / ".." / ".." / "etc" / "passwd")


def test_sibling_job_dir_is_rejected(job_dir: Path) -> None:
    """One job must never read or write another job's directory."""
    sibling = job_dir.parent / "01BX5ZZKBKACTAV9WEVGEMMVRZ" / "source.mp4"
    with pytest.raises(ExtractionFailed, match="outside"):
        resolve_inside(job_dir, sibling)


def test_absolute_path_elsewhere_is_rejected(job_dir: Path, tmp_path: Path) -> None:
    with pytest.raises(ExtractionFailed, match="outside"):
        resolve_inside(job_dir, tmp_path / "elsewhere.flac")


def test_the_job_dir_itself_is_inside(job_dir: Path) -> None:
    assert resolve_inside(job_dir, job_dir) == job_dir.resolve()


def test_containment_does_not_require_the_path_to_exist(job_dir: Path) -> None:
    """Extraction resolves its destination before ffmpeg has created it."""
    assert not (job_dir / "audio.flac").exists()
    assert resolve_inside(job_dir, job_dir / "audio.flac")


def test_nested_path_inside_the_job_dir_resolves(job_dir: Path) -> None:
    assert resolve_inside(job_dir, job_dir / "chunks" / "0000.flac")


def test_traversal_that_returns_inside_is_allowed(job_dir: Path) -> None:
    """`a/../b` inside the job dir is contained once resolved; only the resolved
    location decides, never the spelling."""
    assert resolve_inside(job_dir, job_dir / "chunks" / ".." / "audio.flac") == (
        job_dir / "audio.flac"
    ).resolve()
