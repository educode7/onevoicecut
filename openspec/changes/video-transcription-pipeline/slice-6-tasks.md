# Tasks: Slice 6 — Speaker Mode + Engine Selection + Diarization Rejection

> Phase: `sdd-tasks` · Artifact store: hybrid (openspec + Engram `sdd/video-transcription-pipeline/slice-6/tasks`)
> Design reference: `design.md` decisions on `TranscriptionCapabilities`, `AdmitJob` capability check,
> and port-level defense-in-depth. Spec reference: `specs/slice6-speaker-mode/spec.md`.
> **Stays a single, un-split slice** — almost entirely validation logic reusing existing pieces (fakes
> already raise `DiarizationUnsupported` on `speaker_mode=MULTI`). No uncertainty margin applied.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 350–530 (tests 65% / prod 30% / config 5%) |
| 400-line budget risk | Medium — likely within budget but close; monitor during apply |
| Chained PRs recommended | No — single PR, under budget |
| Suggested split | Single PR (PR 15 per existing tasks.md numbering) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: stacked-to-main
400-line budget risk: Medium
```

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|----------------------|-----------------|-------------------|
| 1 | Incompatibility rejection + web 422 + port defense | PR 15 | `pytest tests/unit/usecases/test_admit_job.py tests/unit/adapters/web/test_admit_job_validation.py -m "not paid and not localmodel"` | N/A — pure validation, no I/O | `usecases/admit_job.py`, `adapters/web/routers/jobs.py`, `tests/fakes/transcription.py` |

---

## Phase 1: Pure Compatibility Helper (foundation — no dependencies)

- [x] 6.1 RED: `tests/unit/usecases/test_admit_job.py` — create file. Test `_validate_compatibility(
      DiarizationSupport.UNSUPPORTED, SpeakerMode.MULTI)` raises `DiarizationUnsupported`; test
      `_validate_compatibility(DiarizationSupport.REQUIRES_SETUP, SpeakerMode.MULTI)` raises same;
      test `_validate_compatibility(DiarizationSupport.AVAILABLE, SpeakerMode.MULTI)` returns
      without error; test `_validate_compatibility(any_diarization, SpeakerMode.SINGLE)` returns
      without error. Four parametrized cases.
- [x] 6.2 GREEN: `usecases/admit_job.py` — add `_validate_compatibility(diarization:
      DiarizationSupport, speaker_mode: SpeakerMode) -> None` as a module-level pure function.
      Raises `DiarizationUnsupported` with actionable message (names capability, suggests
      switch-engine-or-drop-mode) when `speaker_mode is MULTI` and `diarization is not AVAILABLE`.
      No imports from `adapters/` or `runtime/`.

## Phase 2: Use-Case Integration (depends on Phase 1)

- [x] 6.3 RED: `tests/unit/usecases/test_admit_job.py` — add test: `admit_job(engine=LOCAL,
      speaker_mode=MULTI, capabilities=lambda e: fake_caps(diarization=UNSUPPORTED), storage=fake)`
      raises `DiarizationUnsupported`; `storage.create_job` was never called (assert via
      `storage.list_jobs() == ()`). This is the zero-chunks-processed invariant proven at the
      use-case level.
- [x] 6.4 GREEN: `usecases/admit_job.py` — add `capabilities: Callable[[EngineChoice],
      TranscriptionCapabilities] | None = None` parameter to `admit_job()`. When not `None`, call
      `capabilities(engine)` and pass `result.diarization` to `_validate_compatibility()` *before*
      `storage.create_job()`. When `None`, skip validation (backward compatibility for existing
      callers and tests that do not supply an engine resolver).
- [x] 6.5 RED: `tests/unit/usecases/test_admit_job.py` — compatible combination test:
      `admit_job(engine=LOCAL, speaker_mode=MULTI, capabilities=lambda e: fake_caps(
      diarization=AVAILABLE), storage=fake)` succeeds, job stored with `speaker_mode=MULTI`.
- [x] 6.6 GREEN: confirm the existing path is unaffected — no code change needed if 6.4 guarded
      correctly; test passes.
- [x] 6.7 RED: `tests/unit/usecases/test_admit_job.py` — SINGLE mode always compatible: parameterize
      over all three `DiarizationSupport` values, confirm `admit_job(speaker_mode=SINGLE,
      capabilities=..., storage=fake)` never raises.
- [x] 6.8 GREEN: confirm `_validate_compatibility` returns early for `SINGLE` — already handled by
      6.2 implementation.

## Phase 3: Web Layer — DiarizationUnsupported → 422 (depends on Phase 2)

- [x] 6.9 RED: `tests/unit/adapters/web/test_admit_job_validation.py` — create file. HTTP test:
      `POST /api/jobs` with `{"engine": "local", "speaker_mode": "multi"}` when the route's
      capabilities callable returns `diarization=UNSUPPORTED` → response is `422`; body contains
      `"diarization"` and a remediation hint. Uses a `WebDependencies` with a `capabilities` field
      wired to a fake that returns the unsupported capabilities.
- [x] 6.10 GREEN: `adapters/web/routers/jobs.py` — in the `admit` handler, wrap the `admit_job`
      call in `try/except DiarizationUnsupported as e: raise HTTPException(422, detail=str(e))`.
      Also pass `capabilities=deps.capabilities` to `admit_job()`.
- [x] 6.11 RED: `tests/unit/adapters/web/test_admit_job_validation.py` — existing error paths
      (`JobNotFound` → 404, `UnsupportedContainer` → 415, `UploadTooLarge` → 413) still work:
      regression test calling the status/upload routes (reuse existing fixtures or import from
      `test_admit_job_route.py`).
- [x] 6.12 GREEN: confirm no change to existing error handlers — the new `except` clause is
      additive.

## Phase 4: Port-Level Defense-in-Depth (depends on Phase 1)

- [x] 6.13 RED: `tests/unit/usecases/test_admit_job.py` — port-level defense test: a fake
      `TranscriptionPort` whose `capabilities().diarization` is `UNSUPPORTED` raises
      `DiarizationUnsupported` when `transcribe(chunk, TranscriptionRequest(speaker_mode=MULTI,
      ...))` is called. This simulates an admission bypass. Also test `REQUIRES_SETUP` raises.
      Also test `AVAILABLE` does not raise.
- [x] 6.14 GREEN: update `tests/fakes/transcription.py` — make `FakeTranscriptionPort` and
      `NonClassifyingFakeTranscriptionPort` call `_validate_compatibility()` (imported from
      `usecases.admit_job`) in their `transcribe()` method instead of the inline `if` check.
      This makes the fakes use the *same* compatibility definition as admission, closing the
      single-definition-of-compatibility requirement from the spec. `DiarizingFakeTranscriptionPort`
      and `FlakyFakeTranscriptionPort` remain unchanged (they already handle MULTI correctly).
- [x] 6.15 RED: `tests/unit/usecases/test_admit_job.py` — SINGLE mode always accepted at port
      level: parameterize over all fake adapters, confirm `transcribe(chunk,
      TranscriptionRequest(speaker_mode=SINGLE, ...))` never raises.
- [x] 6.16 GREEN: confirm port-level tests pass with the updated fakes.

## Phase 5: Extract Helper (verification — depends on Phases 2 + 4)

- [x] 6.17 REFACTOR: verify `_validate_compatibility` is used by both `admit_job()` (admission
      guard) and the updated fakes (port-level guard). Run full default suite: `.venv\Scripts\
      python.exe -m pytest -m "not paid and not localmodel"`. Confirm `mypy src tests` clean.
      No code change expected — the helper was extracted in Phase 1 and integrated in Phases 2+4.
