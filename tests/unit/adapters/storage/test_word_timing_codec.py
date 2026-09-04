"""Reading transcripts written before word timing existed.

The asymmetry with `kind` is the whole slice, and the codec already states half
of it: `kind` is read explicitly rather than left to the entity default, because
"a *stored* segment always carries a kind, so an absent one is a broken file, not
an unclassified one".

`words` is the opposite. **Its absence is information.** Every transcript written
before slice 11 has no `"words"` key, and those files are on disk right now — a
job that completed last week is not corrupt, it is old. So absent decodes to `()`
and the record is read normally.

Present-but-wrong is a different answer. A `"words"` key that is not a list of
well-formed entries was written by something that meant to write timings and
failed, and reading past it would put fabricated or partial timings into a
transcript that then renders captions. That raises `CorruptedRecord`, which is
what resume already expects to catch after a crash.

The encoder needs no change: `asdict` recurses into the frozen `WordTiming`
entries on its own. That is asserted rather than assumed, because "no change
needed" is exactly the claim that goes stale silently.
"""

import json
from pathlib import Path

import pytest

from onevoicecut.adapters.storage.filesystem_transcript_storage import (
    FilesystemTranscriptStorage,
)
from onevoicecut.adapters.storage.serialization import decode_transcript, encode_transcript
from onevoicecut.domain.errors import CorruptedRecord
from onevoicecut.domain.ids import make_job_id, make_media_id, make_operator_id
from onevoicecut.domain.jobs import EngineChoice, JobRecord, JobState, SpeakerMode
from onevoicecut.domain.transcript import (
    SegmentKind,
    Transcript,
    TranscriptSegment,
    WordTiming,
)

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")

WORDS = (
    WordTiming(start_s=0.0, end_s=0.4, text="hola "),
    WordTiming(start_s=0.4, end_s=1.0, text="hermanos"),
)


def _payload(**segment_overrides: object) -> str:
    """A transcript payload as slice 1 wrote them: no `words` key anywhere."""
    segment: dict[str, object] = {
        "start_s": 0.0,
        "end_s": 1.0,
        "text": "hola hermanos",
        "speaker": None,
        "confidence": 0.9,
        "kind": "speech",
    }
    segment.update(segment_overrides)
    return json.dumps(
        {
            "job_id": str(JOB_ID),
            "segments": [segment],
            "engine_id": "fake-asr",
            "diarized": False,
            "language": "es",
        }
    )


def _transcript(words: tuple[WordTiming, ...]) -> Transcript:
    return Transcript(
        job_id=JOB_ID,
        segments=(
            TranscriptSegment(
                start_s=0.0,
                end_s=1.0,
                text="hola hermanos",
                speaker=None,
                confidence=0.9,
                kind=SegmentKind.SPEECH,
                words=words,
            ),
        ),
        engine_id="fake-asr",
        diarized=False,
        language="es",
    )


class TestAPreSliceElevenTranscript:
    def test_it_decodes_with_no_words(self) -> None:
        """A job that completed last week is old, not corrupt. Every transcript
        on disk today was written without this key."""
        transcript = decode_transcript(_payload())

        assert transcript.segments[0].words == ()

    def test_everything_else_about_it_is_unchanged(self) -> None:
        """The retrofit must not alter how an existing file reads. A transcript
        whose times or text shifted on re-read would be a silent rewrite of work
        already delivered."""
        segment = decode_transcript(_payload()).segments[0]

        assert (segment.start_s, segment.end_s, segment.text) == (
            0.0,
            1.0,
            "hola hermanos",
        )
        assert segment.kind is SegmentKind.SPEECH


class TestAMalformedWordsKey:
    def test_a_string_is_not_a_list_of_words(self) -> None:
        with pytest.raises(CorruptedRecord):
            decode_transcript(_payload(words="hola"))

    def test_null_is_refused_rather_than_read_as_absent(self) -> None:
        """Absent means "written before this existed". `null` means something
        wrote the key and had nothing to put in it, which is a different fact
        and not one to paper over."""
        with pytest.raises(CorruptedRecord):
            decode_transcript(_payload(words=None))

    def test_a_boolean_timestamp_is_refused(self) -> None:
        """`bool` is an `int` in Python, so without the existing guard a `true`
        would persist as a caption starting at one second."""
        with pytest.raises(CorruptedRecord):
            decode_transcript(
                _payload(words=[{"start_s": True, "end_s": 1.0, "text": "hola"}])
            )

    def test_a_word_missing_its_text_is_refused(self) -> None:
        with pytest.raises(CorruptedRecord):
            decode_transcript(_payload(words=[{"start_s": 0.0, "end_s": 1.0}]))

    def test_a_word_that_is_not_an_object_is_refused(self) -> None:
        with pytest.raises(CorruptedRecord):
            decode_transcript(_payload(words=["hola"]))


class TestTheRoundTrip:
    def test_words_survive_encoding_and_decoding(self) -> None:
        assert decode_transcript(
            encode_transcript(_transcript(WORDS))
        ).segments[0].words == WORDS

    def test_no_words_survives_as_no_words(self) -> None:
        """Not as `None`, and not as a key that then fails to decode."""
        assert decode_transcript(encode_transcript(_transcript(()))).segments[0].words == ()

    def test_the_encoder_needed_no_change(self) -> None:
        """`asdict` recurses into the frozen entries on its own. Asserted rather
        than assumed, because "no change needed" is the claim that goes stale
        silently."""
        payload = json.loads(encode_transcript(_transcript(WORDS)))

        assert payload["segments"][0]["words"][0] == {
            "start_s": 0.0,
            "end_s": 0.4,
            "text": "hola ",
        }

    def test_it_round_trips_through_the_real_filesystem(self, tmp_path: Path) -> None:
        """Through the adapter rather than the codec alone: the encoder is not
        the only thing between a `WordTiming` and the disk."""
        storage = FilesystemTranscriptStorage(tmp_path)
        storage.create_job(
            JobRecord(
                job_id=JOB_ID,
                media_id=make_media_id("01BX5ZZKBKACTAV9WEVGEMMVRZ"),
                state=JobState.COMPLETED,
                speaker_mode=SpeakerMode.SINGLE,
                engine=EngineChoice.LOCAL,
                created_at=1.0,
                updated_at=2.0,
                worker_pid=None,
                error=None,
                owner=make_operator_id("maria"),
            )
        )
        storage.save_transcript(_transcript(WORDS))

        stored = storage.load_transcript(JOB_ID)
        assert stored is not None
        assert stored.segments[0].words == WORDS
