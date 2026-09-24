"""The orchestrator: five built pieces, chained in the only order that works.

Each step already has its own test file; what is proven here is the chaining
itself — that `run_generation` feeds each piece the previous one's output,
spends the right token budget on the right phase, and carries the job id from
the transcript into the artifact. And the one edge the worker can actually
hit on real material: a transcript with no confirmed speech at all, which must
come back an honest empty result rather than a crash or an invented summary.
"""

import json

import pytest

from onevoicecut.domain.errors import GenerationFailed
from onevoicecut.domain.ids import make_job_id
from onevoicecut.domain.transcript import SegmentKind, Transcript, TranscriptSegment
from onevoicecut.usecases.generate_artifacts import (
    MAX_MAP_OUTPUT_TOKENS,
    MAX_REDUCE_OUTPUT_TOKENS,
    MAX_VARIANT_OUTPUT_TOKENS,
    resolve_script_targets,
    run_generation,
)
from tests.fakes.text_generation import FakeTextGenerationPort

JOB_ID = make_job_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFD")

def _map_reply(summary: str, ids: list[int]) -> str:
    return json.dumps(
        {
            "summary": summary,
            "moments": [
                {
                    "segment_ids": ids,
                    "hook": "gancho",
                    "quote": "cita",
                    "rationale": "motivo",
                    "score": 0.9,
                }
            ],
        }
    )


MAP_REPLY = _map_reply("el resumen de la ventana", [0, 1])


def _segment(
    start_s: float, end_s: float, kind: SegmentKind = SegmentKind.SPEECH
) -> TranscriptSegment:
    return TranscriptSegment(
        start_s=start_s,
        end_s=end_s,
        text="una frase de la predicacion",
        speaker=None,
        confidence=0.9,
        kind=kind,
    )


def _transcript(segments: tuple[TranscriptSegment, ...]) -> Transcript:
    return Transcript(
        job_id=JOB_ID,
        segments=segments,
        engine_id="fake-asr",
        diarized=False,
    )


class TestTheChain:
    def test_it_produces_a_full_result(self) -> None:
        transcript = _transcript((_segment(0.0, 5.0), _segment(5.0, 9.0)))
        fake = FakeTextGenerationPort(replies=(MAP_REPLY, "el guion del clip"))

        result = run_generation(
            transcript, generate=fake, targets=resolve_script_targets("tiktok")
        )

        assert result.job_id == JOB_ID
        # One window, one partial: reduce returns a single partial untouched,
        # so the summary is the map answer itself and no fold call was paid.
        assert result.summary == "el resumen de la ventana"
        assert len(result.clip_candidates) == 1

        candidate = result.clip_candidates[0]
        # The times come from the transcript, never from the model.
        assert (candidate.start_s, candidate.end_s) == (0.0, 9.0)

        assert len(candidate.variants) == 1
        variant = candidate.variants[0]
        assert variant.body == "el guion del clip"
        assert variant.target == "tiktok"
        assert variant.format == "plain"
        assert variant.duration_target_s == 45.0

    def test_it_spends_one_call_per_phase_in_order(self) -> None:
        """One map call (one window), no fold call (one partial), one variant
        call (one candidate x one target): the shape of the cheapest real run."""
        transcript = _transcript((_segment(0.0, 5.0), _segment(5.0, 9.0)))
        fake = FakeTextGenerationPort(replies=(MAP_REPLY, "guion"))

        run_generation(
            transcript, generate=fake, targets=resolve_script_targets("tiktok")
        )

        assert len(fake.calls) == 2
        assert "Resume" in fake.prompts[0]
        assert "guion corto" in fake.prompts[1]

    def test_each_phase_gets_its_own_token_budget(self) -> None:
        """The budgets are named constants, and a phase fed the wrong one is a
        cost nobody chose: variants on a map budget would pay for prose that
        gets thrown away, a map on a variant budget would truncate the JSON."""
        transcript = _transcript((_segment(0.0, 5.0), _segment(5.0, 9.0)))
        fake = FakeTextGenerationPort(replies=(MAP_REPLY, "guion"))

        run_generation(
            transcript, generate=fake, targets=resolve_script_targets("tiktok")
        )

        assert fake.calls[0].max_output_tokens == MAX_MAP_OUTPUT_TOKENS
        assert fake.calls[1].max_output_tokens == MAX_VARIANT_OUTPUT_TOKENS

    def test_a_fold_gets_the_reduce_budget(self) -> None:
        """Two windows force one fold call, the only place the reduce budget
        is spent; forced here with a tiny window rather than a long sermon.
        Each window cites only its own id — a citation crossing windows is
        exactly what `parse_map_response` refuses."""
        segments = (_segment(0.0, 5.0), _segment(5.0, 9.0))
        transcript = _transcript(segments)
        replies = (
            _map_reply("primera mitad", [0]),
            _map_reply("segunda mitad", [1]),
            "el resumen plegado",
            "guion",
        )
        fake = FakeTextGenerationPort(replies=replies)

        result = run_generation(
            transcript,
            generate=fake,
            targets=resolve_script_targets("tiktok"),
            window_tokens=12,
        )

        assert result.summary == "el resumen plegado"
        assert any(
            call.max_output_tokens == MAX_REDUCE_OUTPUT_TOKENS for call in fake.calls
        )

    def test_a_provider_failure_crosses_the_boundary_as_a_domain_error(self) -> None:
        """The worker catches `DomainError` and leaves the job COMPLETED; that
        contract only holds if nothing else can escape from here."""
        transcript = _transcript((_segment(0.0, 5.0),))
        fake = FakeTextGenerationPort(fail_with=GenerationFailed("provider down"))

        with pytest.raises(GenerationFailed):
            run_generation(
                transcript, generate=fake, targets=resolve_script_targets("tiktok")
            )


class TestTheZeroSpeechEdge:
    def test_an_all_music_transcript_is_an_honest_empty_result(self) -> None:
        """`speech_windows` produces no windows for no speech, and every piece
        downstream already answers emptiness with emptiness: the summary is
        blank, there are no candidates, and — the part that matters — the
        generator is never called, so nothing can invent a message the sermon
        never contained."""
        transcript = _transcript(
            (_segment(0.0, 60.0, SegmentKind.MUSIC), _segment(60.0, 90.0, SegmentKind.MUSIC))
        )
        fake = FakeTextGenerationPort()

        result = run_generation(
            transcript, generate=fake, targets=resolve_script_targets("tiktok")
        )

        assert result.summary == ""
        assert result.clip_candidates == ()
        assert fake.calls == []

    def test_an_all_uncertain_transcript_is_also_empty(self) -> None:
        """Every cloud transcript is all-UNCERTAIN; admission refuses those
        jobs, but the orchestrator must not depend on that guard having run."""
        transcript = _transcript((_segment(0.0, 60.0, SegmentKind.UNCERTAIN),))
        fake = FakeTextGenerationPort()

        result = run_generation(
            transcript, generate=fake, targets=resolve_script_targets("tiktok")
        )

        assert result.summary == ""
        assert result.clip_candidates == ()
        assert fake.calls == []
