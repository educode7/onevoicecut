"""The planner's byte cap, tied to the number the adapter actually declares.

Slice 2a built the byte-cap formula and proved it against a hand-typed
`25_000_000` and a bitrate whose comment says it "sits near this rate". Both were
reasonable guesses and neither was connected to anything. The literal and the
adapter's constant are two independent facts that happen to agree, so the day one
moves the other keeps passing — a planner that produces chunks the engine will
refuse, with a green suite.

This closes that by reading `capabilities()` off the real cloud adapter. No
network: the adapter validates its key at construction and talks to nobody until
`transcribe`, so the declared cap is free to ask for. Marking these `paid` would
have moved the project's byte-cap safety check out of the run that gates every
commit, to buy nothing.

The second thing here is the gap the formula leaves open on purpose. The cap is
computed against the **stride**, but a chunk is `stride + overlap` long, and tail
absorption can grow the last one by nearly `min_chunk_s` more. The 0.9 headroom
is what covers that, and `plan_chunks` says so in a comment — "which the byte
cap's 0.9 headroom already covers at any realistic bitrate". This measures the
word *realistic*, so the margin is a number rather than a belief.
"""

from pathlib import Path

import pytest

from onevoicecut.adapters.asr.cloud.openai_whisper_adapter import (
    OpenAiWhisperTranscriber,
)
from onevoicecut.domain.errors import ChunkTooLarge
from onevoicecut.domain.ids import make_job_id, make_media_id
from onevoicecut.domain.media import AudioTrack
from onevoicecut.ports.capabilities import TranscriptionCapabilities
from onevoicecut.usecases.plan_chunks import (
    DEFAULT_MIN_CHUNK_S,
    DEFAULT_OVERLAP_S,
    plan_chunks,
)

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")
MEDIA_ID = make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ")

# A sermon, which is the job this system exists for rather than a round number.
THREE_HOURS_S = 3 * 60 * 60.0

# Measured, not assumed — see `tests/integration/test_flac_bitrate.py`, which
# encodes through the pipeline's own argv and holds the real rate under this.
#
# It is three times slice 2a's `FLAC_BYTES_PER_SECOND = 16_000`, and the reason
# is worth knowing: **normalization does not pin the FLAC sample format**, so it
# follows whatever the source decodes to. An mp4 with AAC audio — the normal
# input here — decodes to float and lands as 24-bit FLAC at ~48.5 KB/s on
# incompressible audio, against ~15.4 KB/s for a 16-bit source. Speech
# compresses far better than the noise that produces that number; this is the
# ceiling, not the expectation.
FLAC_CEILING_BYTES_PER_S = 60_000


def cloud_capabilities() -> TranscriptionCapabilities:
    """The real declaration, from the real adapter. Constructing it costs no
    network — the key is checked locally and nothing is sent until `transcribe`."""
    return OpenAiWhisperTranscriber("sk-test-not-a-real-key").capabilities()


def _track(duration_s: float, bytes_per_second: float) -> AudioTrack:
    return AudioTrack(
        media_id=MEDIA_ID,
        path=Path("audio.flac"),
        duration_s=duration_s,
        size_bytes=int(duration_s * bytes_per_second),
    )


def _largest_chunk_bytes(duration_s: float, bytes_per_second: float) -> float:
    """What the fattest chunk in the plan would weigh on the wire.

    Derived from the plan rather than from the stride, which is the whole point:
    the overlap tail and the absorbed short tail are both added after the cap has
    been computed, so only the finished plan knows how long a chunk really is.
    """
    plan = plan_chunks(
        JOB_ID, _track(duration_s, bytes_per_second), cloud_capabilities()
    )
    return max((c.end_s - c.start_s) * bytes_per_second for c in plan.chunks)


class TestThePlannerReadsTheDeclaredCap:
    def test_the_cap_the_planner_sees_is_the_one_the_adapter_declares(self) -> None:
        """The binding this unit exists to create. Slice 2a asserted a literal;
        if the adapter's constant ever moves, that literal keeps agreeing with
        itself while production plans chunks the engine refuses."""
        assert cloud_capabilities().max_chunk_bytes == 25_000_000

    def test_at_worst_case_flac_rates_the_byte_cap_is_what_binds(self) -> None:
        """Corrects a claim slice 2a made against a guessed bitrate.

        `test_realistic_flac_bitrate_is_not_constrained_by_the_cap` asserts the
        600 s duration target wins, and at its assumed 16 KB/s it does. At the
        rate the pipeline actually produces for incompressible audio it does
        not: the cap binds and the stride shortens. Both are true, and the
        difference is entirely the bitrate — which is why it is now measured.

        Nothing is broken by this. A shorter stride is the formula working.
        """
        plan = plan_chunks(
            JOB_ID,
            _track(THREE_HOURS_S, FLAC_CEILING_BYTES_PER_S),
            cloud_capabilities(),
        )

        assert plan.stride_s < 600.0

    def test_no_planned_chunk_exceeds_the_declared_cap(self) -> None:
        """The scenario in its own words: a plan sized against the real cap never
        exceeds it on submission."""
        cap = cloud_capabilities().max_chunk_bytes
        assert cap is not None

        assert _largest_chunk_bytes(THREE_HOURS_S, FLAC_CEILING_BYTES_PER_S) <= cap


class TestTheStrideReservesWhatIsAppendedAfterIt:
    """No chunk is one stride long, and the byte cap used to be sized as if it
    were.

    Every chunk carries the overlap tail; the chunk that absorbs a short final
    one grows by nearly `min_chunk_s` instead. Both are appended *after* the
    stride has been chosen, so the difference was charged to the 0.9 headroom —
    which covered it only below roughly 71 KB/s, a limit stated nowhere and
    enforced by nothing. The stride now reserves those seconds outright.
    """

    def test_a_track_ending_just_short_of_a_stride_still_fits(self) -> None:
        """The case that found the defect. The final chunk is deleted and its
        predecessor grows to swallow it — the longest chunk any plan produces,
        and the one the old arithmetic never accounted for."""
        cap = cloud_capabilities().max_chunk_bytes
        assert cap is not None
        binding_rate = 100_000
        stride_s = 195.0
        duration_s = stride_s * 4 + (DEFAULT_MIN_CHUNK_S - 1.0)

        assert _largest_chunk_bytes(duration_s, binding_rate) <= cap

    @pytest.mark.parametrize(
        "bytes_per_second", [16_000, 48_500, 100_000, 250_000, 500_000]
    )
    def test_the_cap_holds_at_every_bitrate_the_planner_accepts(
        self, bytes_per_second: int
    ) -> None:
        """The property, rather than two well-chosen inputs.

        Spanning from slice 2a's assumed rate through the measured worst case to
        rates this pipeline could not produce: wherever the planner returns a
        plan at all, no chunk in it exceeds the cap. The rates where it should
        instead refuse are the subject of the class below.
        """
        cap = cloud_capabilities().max_chunk_bytes
        assert cap is not None
        # Just short of a stride boundary, so tail absorption is in play at
        # every rate rather than only at the ones where it happens to trigger.
        duration_s = THREE_HOURS_S + (DEFAULT_MIN_CHUNK_S - 1.0)

        assert _largest_chunk_bytes(duration_s, bytes_per_second) <= cap

    def test_the_overlap_alone_is_reserved_when_it_is_the_larger_of_the_two(
        self,
    ) -> None:
        """A chunk never pays for both. One that absorbed a tail clamps to the
        end of the track and carries no overlap past it, so reserving their sum
        would shorten every stride to buy a case that cannot happen."""
        cap = cloud_capabilities().max_chunk_bytes
        assert cap is not None
        wide_overlap_s = DEFAULT_MIN_CHUNK_S * 3

        plan = plan_chunks(
            JOB_ID,
            _track(THREE_HOURS_S, 100_000),
            cloud_capabilities(),
            overlap_s=wide_overlap_s,
        )

        assert max((c.end_s - c.start_s) * 100_000 for c in plan.chunks) <= cap


class TestARateTheCapCannotHold:
    def test_a_bitrate_too_high_for_a_one_second_chunk_is_refused(self) -> None:
        """Not a hypothetical for a cloud engine with a hard per-request cap.
        Refusing at planning time is the point — the alternative is discovering
        it per chunk, three hours in, one rejected upload at a time."""
        absurd_rate = 30_000_000

        with pytest.raises(ChunkTooLarge, match="openai-whisper"):
            plan_chunks(
                JOB_ID, _track(3600.0, absurd_rate), cloud_capabilities()
            )
