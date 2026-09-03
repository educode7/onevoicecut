"""What the model is allowed to read, and what it must never be handed.

The `.txt` export keeps `UNCERTAIN` and marks it, because a human reading `[?]`
knows what they are looking at and an all-uncertain transcript rendered as zero
bytes would throw away a three-hour run. The model gets the stricter rule: a
window is built from `SPEECH` only.

The difference is not inconsistency, it is the whole reason `speech_segments` and
`render_message_text` are two functions. **A model will not honour an inline
marker the way a reader does.** Hand it a marked chorus and it may summarise the
worship set as the preacher's argument, and what comes back is fluent, confident
and about the wrong thing — with nothing in the artifact saying so.

`MUSIC` is dropped for a plainer reason: sung lyrics are not the message. The
range stays addressable in the transcript for clip material (10b-ii), but it is
not summary input.

The last test here is the one that matters most on this material. When most of a
recording is music — a service with more singing than preaching, which is normal
input rather than an edge case — the summary must derive from the remaining
speech and **nothing must be substituted to fill it out**. A short honest summary
beats a full invented one.
"""

from onevoicecut.domain.transcript import SegmentKind, TranscriptSegment
from onevoicecut.usecases.generate_artifacts import (
    map_windows,
    speech_windows,
)

WINDOW = 100
OVERLAP = 20


def _segment(index: int, kind: SegmentKind, *, text: str | None = None) -> TranscriptSegment:
    return TranscriptSegment(
        start_s=float(index * 10),
        end_s=float(index * 10 + 10),
        text=" ".join(f"palabra{index:03d}" for _ in range(10)) if text is None else text,
        speaker=None,
        confidence=0.9,
        kind=kind,
    )


def _windows(segments: tuple[TranscriptSegment, ...]) -> tuple[object, ...]:
    return speech_windows(segments, window_tokens=WINDOW, overlap_tokens=OVERLAP)


def _text_of(segments: tuple[TranscriptSegment, ...]) -> str:
    return "\n".join(w.text for w in _windows(segments))  # type: ignore[attr-defined]


class TestOnlySpeechReachesTheModel:
    def test_music_never_appears_in_a_window(self) -> None:
        """Sung lyrics are not the message. Summarising them as if they were is
        the failure this project names as its normal case, not its edge case."""
        segments = (
            _segment(0, SegmentKind.SPEECH),
            _segment(1, SegmentKind.MUSIC, text="aleluya aleluya aleluya"),
            _segment(2, SegmentKind.SPEECH),
        )

        assert "aleluya" not in _text_of(segments)

    def test_uncertain_never_appears_in_a_window(self) -> None:
        """The stricter half, and the one that diverges from the `.txt` export.

        A reader seeing `[?]` knows what it means. A model does not honour an
        inline marker — hand it a marked chorus and it may summarise the worship
        set as the preacher's argument, fluently and confidently.
        """
        segments = (
            _segment(0, SegmentKind.SPEECH),
            _segment(1, SegmentKind.UNCERTAIN, text="quizas cantado quizas no"),
            _segment(2, SegmentKind.SPEECH),
        )

        assert "quizas" not in _text_of(segments)

    def test_the_surviving_speech_is_all_there(self) -> None:
        segments = (
            _segment(0, SegmentKind.SPEECH),
            _segment(1, SegmentKind.MUSIC),
            _segment(2, SegmentKind.SPEECH),
        )

        text = _text_of(segments)
        assert "palabra000" in text
        assert "palabra002" in text


class TestIdsStillResolveAgainstTheRealTranscript:
    def test_an_id_is_the_index_in_the_untouched_transcript(self) -> None:
        """The filter must not renumber. Ids are resolved against the real
        `Transcript` — including its music — so a window numbering its own
        surviving segments 0,1,2 would point every citation at the wrong moment
        of the sermon, which is the exact failure ids exist to prevent.
        """
        segments = (
            _segment(0, SegmentKind.MUSIC),
            _segment(1, SegmentKind.MUSIC),
            _segment(2, SegmentKind.SPEECH),
        )

        window = _windows(segments)[0]

        assert window.segment_ids == (2,)  # type: ignore[attr-defined]
        assert "[s0002]" in window.text  # type: ignore[attr-defined]

    def test_ids_stay_sparse_rather_than_being_compacted(self) -> None:
        segments = (
            _segment(0, SegmentKind.SPEECH),
            _segment(1, SegmentKind.MUSIC),
            _segment(2, SegmentKind.SPEECH),
        )

        covered = [i for w in _windows(segments) for i in w.segment_ids]  # type: ignore[attr-defined]

        assert covered == [0, 2]


class TestAMusicHeavyRecording:
    def test_the_summary_input_is_only_the_remaining_speech(self) -> None:
        """A service with more singing than preaching is normal input here."""
        segments = tuple(
            _segment(i, SegmentKind.SPEECH if i % 10 == 0 else SegmentKind.MUSIC)
            for i in range(60)
        )

        covered = [i for w in _windows(segments) for i in w.segment_ids]  # type: ignore[attr-defined]

        assert set(covered) == {0, 10, 20, 30, 40, 50}

    def test_nothing_is_substituted_to_fill_the_windows_out(self) -> None:
        """Six sentences of preaching in an hour of worship produce a short
        summary. A short honest one beats a full invented one, and there is no
        mechanism here that could pad it."""
        segments = tuple(
            _segment(i, SegmentKind.SPEECH if i % 10 == 0 else SegmentKind.MUSIC)
            for i in range(60)
        )

        assert len(_windows(segments)) < len(
            map_windows(segments, window_tokens=WINDOW, overlap_tokens=OVERLAP)
        )

    def test_a_transcript_with_no_speech_produces_no_windows(self) -> None:
        """Not one empty window — a model asked to summarise nothing answers
        anyway, and that answer would become the summary.

        Reaching this state is not hypothetical: the cloud adapter declares
        `non_speech_classification=UNSUPPORTED` and marks every segment
        `UNCERTAIN`, so its transcripts filter to nothing here. The refusal that
        stops such a job before it runs is the admission guard next door; this
        is the floor underneath it.
        """
        segments = tuple(_segment(i, SegmentKind.UNCERTAIN) for i in range(20))

        assert _windows(segments) == ()


def test_speech_windows_and_map_windows_agree_on_pure_speech() -> None:
    """The filter is the only difference between them. A transcript with nothing
    to filter must window identically, or the two have drifted into two
    windowing algorithms."""
    segments = tuple(_segment(i, SegmentKind.SPEECH) for i in range(40))

    assert _windows(segments) == map_windows(
        segments, window_tokens=WINDOW, overlap_tokens=OVERLAP
    )
