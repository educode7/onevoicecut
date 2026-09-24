# Proposal: Slice 6 — Speaker Mode + Engine Selection + Diarization Rejection

## Intent

Validate engine/speaker-mode compatibility at admission time. Reject incompatible combinations before any work starts — zero chunks processed, no billable API calls, no local model loads.

The gap today: `admit_job.py` records `engine` and `speaker_mode` on the `JobRecord` but never checks whether the selected engine can satisfy the requested speaker mode. A job requesting `speaker_mode=MULTI` against an engine with `diarization=UNSUPPORTED` would be admitted, dispatched to the worker, and fail at the first chunk's `transcribe()` call — after extraction, after chunk planning, potentially hours into a run. The design requires this to fail at admission instead.

## Scope

### In Scope

- Admission-time capability check: resolve the engine, query its `TranscriptionCapabilities.diarization`, reject `MULTI` when `UNSUPPORTED` or `REQUIRES_SETUP`
- Domain error `DiarizationUnsupported` already exists — reused, not created
- Web route catches `DiarizationUnsupported` → HTTP 422 with actionable message
- Defense-in-depth guard in `TranscriptionPort.transcribe()` — a fake-adapter check that real adapters inherit in slices 7a/8a
- Extracted compatibility-check helper in `usecases/admit_job.py` shared by admission and port-level guard
- Speaker-mode default (`SINGLE`) and engine-required validation already exist in the Pydantic schema — confirm propagation, do not rebuild

### Out of Scope

- Real ASR adapters (slices 7a, 8a) — fakes only
- Diarization itself (slices 9a, 9b) — the rejection path, not the capability flip
- Engine resolver construction with real secrets — fakes today, real in 7a/8a
- Any change to `JobRecord`, `SpeakerMode`, `EngineChoice`, or `TranscriptionCapabilities` types

## Capabilities

### New Capabilities

None. This slice closes scenarios within existing capabilities.

### Modified Capabilities

- `media-ingest`: closes "Per-Job Speaker Mode Input" (default propagation), "Per-Job ASR Engine Selection" (required field), "Reject Incompatible Engine/Speaker-Mode Combination at Admission" (the core of this slice)
- `speech-transcription`: closes "Reject Speaker-Mode Jobs the Adapter Cannot Satisfy" (defense-in-depth half only — the port-level guard)

## Approach

### Layer 1: Admission-time check (use case)

`admit_job.py` gains a `capabilities` parameter — a callable returning `TranscriptionCapabilities` for the resolved engine. Before minting IDs or creating the `JobRecord`:

1. Call `capabilities()` to get the engine's diarization support
2. If `speaker_mode == MULTI` and `diarization != AVAILABLE`, raise `DiarizationUnsupported` with a message naming the engine, the requested mode, and the suggestion (switch engine or drop to single)
3. No job is created, no IDs minted, no storage touched

The check is **strictly before** `storage.create_job()`. This is the zero-chunks-processed guarantee.

### Layer 2: Web route error handling

The `POST /api/jobs` handler wraps the `admit_job()` call in a try/except for `DiarizationUnsupported`, returning HTTP 422 with the error detail. This follows the existing pattern for `UploadTooLarge` (413) and `UnsupportedContainer` (415).

### Layer 3: Defense-in-depth at the port

Every `TranscriptionPort.transcribe()` implementation guards at the top: if `request.speaker_mode == MULTI` and `capabilities().diarization != AVAILABLE`, raise `DiarizationUnsupported`. This is the same check at the port level — it should never fire in normal operation (admission catches it first), but it prevents silent degradation if admission is bypassed (e.g. a direct worker invocation, a future admin tool, a test that constructs a job directly).

### Layer 4: Extracted helper

The compatibility logic is a pure function: `(EngineChoice, SpeakerMode, TranscriptionCapabilities) -> None | raise`. Extracted into `usecases/admit_job.py` as `_validate_compatibility()`, reused by both the admission check and the port-level guard.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `usecases/admit_job.py` | Modified | Add `capabilities` parameter, guard clause before `create_job`, extract helper |
| `adapters/web/routers/jobs.py` | Modified | Catch `DiarizationUnsupported` → 422 in `admit` handler |
| `tests/unit/usecases/test_admit_job.py` | New | RED/GREEN tests for default propagation, engine-required, incompatible rejection, compatible admission |
| `tests/fakes/transcription.py` | Modified | Confirm `FakeTranscriptionPort` and `DiarizingFakeTranscriptionPort` already raise correctly (they do) |
| `domain/errors.py` | Unchanged | `DiarizationUnsupported` already exists |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Admission check queries capabilities through the resolver, coupling admit_job to the port layer | Low | The `capabilities` parameter is a callable, not a port import — the use case stays port-agnostic; the caller injects the resolution |
| Defense-in-depth guard duplicates admission logic | Low | Extracted helper ensures one definition; the port guard is a one-liner calling the same function |
| Existing tests break if `admit_job` signature changes | Med | The `capabilities` parameter has a default (lambda returning UNSUPPORTED) so existing callers compile; tests are updated in RED/GREEN pairs |
| The 4x estimation multiplier pushes this over the 400-line budget | Low | Pure-logic slice, no new dataclasses, no new ports, no I/O — directly comparable to slice 1's use-case ratio; tasks.md estimates ~300 lines, calibrated ×4 = ~1,200 worst case, but the actual work is ~17 task pairs of small RED/GREEN cycles |

## Rollback Plan

Revert the single commit. The guard clause in `admit_job.py` is the rollback boundary — removing it restores the previous behavior where incompatible jobs are admitted and fail at chunk-dispatch time. No external state is mutated; any in-flight jobs are unaffected.

## Dependencies

- **Upstream**: slices 1–5c (all complete). `DiarizationUnsupported` error exists. `FakeTranscriptionPort` already raises it on `MULTI`. `EngineResolver` exists with fakes wired.
- **Downstream**: slices 7a/8a (real adapters) inherit the port-level guard. Slice 9a/9b flips diarization to `AVAILABLE` on the adapters — the admission check then passes instead of rejecting.

## Success Criteria

- [ ] `speaker_mode` defaults to `SINGLE` when omitted; job record stores it
- [ ] `engine` is required; missing engine returns 422 at the schema level
- [ ] `speaker_mode=MULTI` + `diarization=UNSUPPORTED` rejects at admission with 422, no job created
- [ ] `speaker_mode=MULTI` + `diarization=AVAILABLE` admits normally
- [ ] Zero chunks are dispatched for an incompatible combination
- [ ] Port-level guard raises `DiarizationUnsupported` if `transcribe()` is called with `MULTI` on a non-diarizing adapter
- [ ] All existing tests remain green; `mypy src tests` clean
- [ ] Default test suite passes: `pytest tests/unit/usecases/test_admit_job.py -m "not paid and not localmodel"`
