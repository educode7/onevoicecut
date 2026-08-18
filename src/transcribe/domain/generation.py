"""Map-reduce summarization output entities. No rendering — script text only."""

from dataclasses import dataclass

from transcribe.domain.ids import JobId


@dataclass(frozen=True, slots=True)
class ScriptVariant:
    target: str
    format: str
    body: str
    duration_target_s: float


@dataclass(frozen=True, slots=True)
class ClipCandidate:
    start_s: float
    end_s: float
    hook: str
    quote: str
    rationale: str
    score: float
    variants: tuple[ScriptVariant, ...]


@dataclass(frozen=True, slots=True)
class GenerationResult:
    job_id: JobId
    summary: str
    clip_candidates: tuple[ClipCandidate, ...]
