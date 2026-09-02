"""Hear what the local engine makes of your own audio, without the HTTP path.

A development tool, not part of the pipeline and not covered by the spec. It
exists because the classification work in slices 7a-iii and 7a-iv can only be
judged against real material: every fixture in the test suite is synthesised with
ffmpeg, and no synthetic signal reproduces a human singing over a sermon — the
case the whole `SegmentKind` axis was built for.

It goes through `local_transcriber`, the same lazily-imported factory the engine
resolver uses in production, so what you see here is what a worker would get.
The worker itself still runs with `resolver=None` and exits 3, which is why this
script talks to the adapter directly.

    python scripts/try_local_asr.py RECORDING.mp4 --model small
    python scripts/try_local_asr.py RECORDING.mp4 --model small --start 42:10 --seconds 90

`--model` is required on purpose, mirroring the adapter's own refusal to invent
one: it decides both transcript quality and hours of runtime.
"""

import argparse
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from onevoicecut.domain.chunking import AudioChunk  # noqa: E402
from onevoicecut.domain.ids import JobId  # noqa: E402
from onevoicecut.domain.jobs import SpeakerMode  # noqa: E402
from onevoicecut.domain.transcript import (  # noqa: E402
    SegmentKind,
    Transcript,
    TranscriptSegment,
    render_message_text,
)
from onevoicecut.ports.transcription import TranscriptionRequest  # noqa: E402
from onevoicecut.runtime.engine_resolver import local_transcriber  # noqa: E402

SAMPLE_RATE = 16000
SCRATCH_JOB_ID = JobId("00000000000000000000000000")


def _timestamp(value: str) -> float:
    """Accept 90, 1:30 or 1:02:30 — reading a clip offset off a player is normal."""
    parts = [float(part) for part in value.split(":")]
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _clock(seconds: float) -> str:
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(int(minutes), 60)
    return f"{hours:d}:{minutes:02d}:{secs:05.2f}"


def _extract(source: Path, start_s: float, seconds: float | None, into: Path) -> Path:
    """Cut the window under test to 16 kHz mono, the rate the engine works at.

    List-form argv with `-nostdin` and an explicit timeout, the same discipline
    `adapters/ffmpeg/argv.py` enforces — a dev script is still a subprocess call
    with a filename in it.
    """
    window = ["-t", str(seconds)] if seconds is not None else []
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-y",
            "-ss", str(start_s),
            "-i", str(source),
            *window,
            "-vn", "-ar", str(SAMPLE_RATE), "-ac", "1",
            str(into),
        ],
        check=True,
        capture_output=True,
        timeout=1800,
    )
    return into


def _duration_s(path: Path) -> float:
    from faster_whisper import decode_audio

    return len(decode_audio(str(path), sampling_rate=SAMPLE_RATE)) / SAMPLE_RATE


def _report(segments: tuple[TranscriptSegment, ...], duration_s: float) -> None:
    counts: Counter[SegmentKind] = Counter(s.kind for s in segments)
    covered = sum(s.end_s - s.start_s for s in segments)

    print(f"\n{len(segments)} segments over {_clock(duration_s)}")
    for kind in SegmentKind:
        seconds = sum(s.end_s - s.start_s for s in segments if s.kind is kind)
        print(f"  {kind.value:<10} {counts[kind]:>4} segments  {_clock(seconds)}")
    # The invariant slice 7a-iii exists to protect: a filtered range is still
    # reported, so a musical passage stays addressable in the source footage.
    print(f"  {'covered':<10} {covered / duration_s:>7.1%} of the window\n")

    for segment in segments:
        window = f"{_clock(segment.start_s)} → {_clock(segment.end_s)}"
        text = segment.text or "—"
        print(f"[{segment.kind.value:^9}] {window}  {text}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="any file ffmpeg can read")
    parser.add_argument("--model", required=True, help="tiny | base | small | medium | large-v3")
    parser.add_argument("--start", type=_timestamp, default=0.0, help="offset, e.g. 42:10")
    parser.add_argument("--seconds", type=float, default=None, help="window length")
    parser.add_argument("--device", default="auto", help="auto | cpu | cuda")
    args = parser.parse_args(argv)

    if not args.source.is_file():
        print(f"no such file: {args.source}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as scratch:
        window = _extract(
            args.source, args.start, args.seconds, Path(scratch) / "window.wav"
        )
        duration_s = _duration_s(window)

        print(f"loading {args.model!r} on {args.device} — first run downloads weights")
        transcriber = local_transcriber(args.model, device=args.device)()
        print(f"engine: {transcriber.capabilities().engine_id}")
        print(f"classification: {transcriber.capabilities().non_speech_classification}")

        segments = transcriber.transcribe(
            AudioChunk(
                job_id=SCRATCH_JOB_ID,
                index=0,
                path=window,
                start_s=args.start,
                end_s=args.start + duration_s,
                size_bytes=window.stat().st_size,
            ),
            TranscriptionRequest(
                language="es", speaker_mode=SpeakerMode.SINGLE, timeout_s=None
            ),
        )

    _report(segments, duration_s)

    # What the operator would actually receive. MUSIC is dropped, UNCERTAIN is
    # kept and marked, and empty ranges render as nothing at all.
    print("\n--- transcript.txt would contain ---")
    print(
        render_message_text(
            Transcript(
                job_id=SCRATCH_JOB_ID,
                segments=segments,
                engine_id=transcriber.capabilities().engine_id,
                diarized=False,
            )
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
