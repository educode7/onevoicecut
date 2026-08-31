from dataclasses import FrozenInstanceError

import pytest

from onevoicecut.domain.generation import ClipCandidate, GenerationResult, ScriptVariant
from onevoicecut.domain.ids import make_job_id

JOB_ID = make_job_id("01ARZ3NDEKTSV4RRFFQ69G5FAV")


def test_script_variant_holds_fields() -> None:
    variant = ScriptVariant(
        target="generic", format="short", body="hook...", duration_target_s=30.0
    )
    assert variant.target == "generic"


def test_script_variant_is_frozen() -> None:
    variant = ScriptVariant(
        target="generic", format="short", body="hook...", duration_target_s=30.0
    )
    with pytest.raises(FrozenInstanceError):
        variant.body = "other"  # type: ignore[misc]


def test_clip_candidate_holds_variants() -> None:
    candidate = ClipCandidate(
        start_s=10.0,
        end_s=40.0,
        hook="hook",
        quote="quote",
        rationale="why",
        score=0.9,
        variants=(),
    )
    assert candidate.score == 0.9


def test_clip_candidate_is_frozen() -> None:
    candidate = ClipCandidate(
        start_s=10.0,
        end_s=40.0,
        hook="hook",
        quote="quote",
        rationale="why",
        score=0.9,
        variants=(),
    )
    with pytest.raises(FrozenInstanceError):
        candidate.score = 0.1  # type: ignore[misc]


def test_generation_result_is_frozen() -> None:
    result = GenerationResult(job_id=JOB_ID, summary="summary", clip_candidates=())
    with pytest.raises(FrozenInstanceError):
        result.summary = "other"  # type: ignore[misc]
