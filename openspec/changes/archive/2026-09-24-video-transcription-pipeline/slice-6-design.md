# Design: Slice 6 — Speaker Mode + Engine Selection + Diarization Rejection

## Technical Approach

Validate engine/speaker-mode compatibility at admission time via a pure helper function, reject incompatible combinations before any storage or work, catch the rejection at the web layer as HTTP 422, and enforce the same invariant at the port level as defense-in-depth. No new domain entities, no new ports, no new adapters — three surgical changes to existing files plus test coverage.

## Architecture Decisions

### Decision: Compatibility helper location

| Option | Tradeoff |
| --- | --- |
| `domain/compatibility.py` | Domain imports `ports.capabilities` — violates the ports-imports-domain-not-reversed constraint. |
| **`usecases/admit_job.py` (chosen)** | Lives where admission logic already lives. Adapters already import usecases (established pattern in `adapters/web/routers/jobs.py`). Pure function, no I/O. |
| `ports/compatibility.py` | Plausible but the helper is orchestration logic (decides rejection), not a port contract. |

**Rationale**: The helper is a validation rule applied by the use case. The admission guard calls it directly; the adapter calls it by importing from usecases — the same import direction the web adapter already uses for `admit_job`. This avoids a new module while staying architecturally honest.

### Decision: Treat REQUIRES_SETUP as rejection

| Option | Tradeoff |
| --- | --- |
| Reject both `UNSUPPORTED` and `REQUIRES_SETUP` | Operator gets a clear message at admission. A job that would fail at chunk time instead fails in milliseconds. |
| Allow `REQUIRES_SETUP` through | The job would hit the adapter and fail with the same error, but after extraction and chunking — wasting time. |

**Rationale**: The spec requires it. A `REQUIRES_SETUP` engine cannot satisfy `MULTI` today. Letting it through would violate the zero-chunks-processed guarantee.

### Decision: `capabilities` parameter as injectable callable

`admit_job` gains a `capabilities: Callable[[], TranscriptionCapabilities]` parameter with a default of `lambda: TranscriptionCapabilities(engine_id="unknown", diarization=DiarizationSupport.UNSUPPORTED, non_speech_classification=ClassificationSupport.UNSUPPORTED, max_chunk_bytes=None, max_chunk_duration_s=None)`. This means existing callers compile without changes, and tests inject fake capabilities without wiring a real engine.

## Data Flow

```
POST /api/jobs {engine: LOCAL, speaker_mode: MULTI}
        │
        ▼
   web route (admit handler)
        │
        ▼
   admit_job(capabilities=capabilities_callable, ...)
        │
        ├── 1. Call capabilities() → TranscriptionCapabilities
        │
        ├── 2. _validate_compatibility(speaker_mode, caps.diarization)
        │       └── MULTI + UNSUPPORTED/REQUIRES_SETUP → raise DiarizationUnsupported
        │
        ├── 3. (only if compatible) mint IDs, create JobRecord, store
        │
        └── return JobRecord
                │
                ▼ (if DiarizationUnsupported raised)
           web route catches → HTTP 422 {detail: "..."}
```

Port-level defense-in-depth (never reached in normal flow):

```
worker → resolver.resolve(engine) → adapter.transcribe(chunk, request)
        │
        └── adapter calls _validate_compatibility(request.speaker_mode, self.capabilities().diarization)
            └── raises DiarizationUnsupported if incompatible
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/onevoicecut/usecases/admit_job.py` | Modify | Add `_validate_compatibility()` helper, add `capabilities` parameter to `admit_job()`, guard before `storage.create_job()` |
| `src/onevoicecut/adapters/web/routers/jobs.py` | Modify | Catch `DiarizationUnsupported` in `admit` handler → HTTP 422 |
| `src/onevoicecut/adapters/web/app.py` | Modify | Add `capabilities` field to `WebDependencies` (injectable callable) |
| `tests/unit/usecases/test_admit_job.py` | New | RED/GREEN tests for admission guard: compatible, incompatible, SINGLE always OK |
| `tests/unit/adapters/web/test_admit_rejection.py` | New | RED/GREEN tests for 422 response on DiarizationUnsupported |
| `tests/fakes/transcription.py` | Confirm | `FakeTranscriptionPort` already raises `DiarizationUnsupported` on `MULTI`; no changes needed |
| `src/onevoicecut/domain/errors.py` | Unchanged | `DiarizationUnsupported` already exists |

## Interfaces / Contracts

### `_validate_compatibility` — pure helper

```python
def _validate_compatibility(
    speaker_mode: SpeakerMode,
    diarization: DiarizationSupport,
) -> None:
    """Raise DiarizationUnsupported when MULTI is requested but unavailable.

    Treats both UNSUPPORTED and REQUIRES_SETUP as rejection — the engine
    cannot satisfy the request today, and admitting the job would process
    chunks before failing.
    """
    if speaker_mode is SpeakerMode.MULTI and diarization is not DiarizationSupport.AVAILABLE:
        raise DiarizationUnsupported(
            f"the {diarization.value} engine does not support speaker_mode=multi; "
            f"switch to an engine with diarization support or drop to speaker_mode=single"
        )
```

### Modified `admit_job` signature

```python
def admit_job(
    *,
    engine: EngineChoice,
    speaker_mode: SpeakerMode,
    storage: TranscriptStoragePort,
    capabilities: Callable[[], TranscriptionCapabilities] | None = None,
    now: Callable[[], float] = time.time,
    new_job_id: Callable[[], JobId] = generate_job_id,
    new_media_id: Callable[[], MediaId] = generate_media_id,
) -> JobRecord:
```

When `capabilities` is `None`, the guard is skipped (backward-compatible default). When provided, the guard runs **before** `new_job_id()` — IDs are not minted on rejection.

### WebDependencies extension

```python
@dataclass(frozen=True, slots=True)
class WebDependencies:
    storage: TranscriptStoragePort
    capabilities: Callable[[], TranscriptionCapabilities]  # NEW — engine capabilities
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    # ... rest unchanged
```

### Web route error handling

```python
@router.post("", status_code=201, response_model=AdmitJobResponse)
def admit(body: AdmitJobRequest) -> AdmitJobResponse:
    try:
        job = admit_job(
            engine=body.engine,
            speaker_mode=body.speaker_mode,
            storage=deps.storage,
            capabilities=deps.capabilities,
            now=deps.now,
            new_job_id=deps.new_job_id,
            new_media_id=deps.new_media_id,
        )
    except DiarizationUnsupported as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return AdmitJobResponse(job_id=job.job_id, state=job.state)
```

## Defense-in-Depth

Every `TranscriptionPort.transcribe()` implementation MUST guard at the top:

```python
def transcribe(self, chunk: AudioChunk, request: TranscriptionRequest) -> ...:
    _validate_compatibility(request.speaker_mode, self.capabilities().diarization)
    # ... actual transcription
```

This uses the same `_validate_compatibility` helper imported from `usecases.admit_job`. The guard should never fire in normal operation (admission catches it first), but prevents silent degradation if admission is bypassed — a direct worker invocation, a future admin tool, or a test that constructs a job record directly.

The existing fakes already implement this partially: `FakeTranscriptionPort.transcribe()` checks `request.speaker_mode is SpeakerMode.MULTI` and raises. The defense-in-depth refactors this to use the shared helper, ensuring `REQUIRES_SETUP` is also caught.

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit: `_validate_compatibility` | SINGLE always passes; MULTI + AVAILABLE passes; MULTI + UNSUPPORTED raises; MULTI + REQUIRES_SETUP raises | Pure function, no fixtures needed |
| Unit: `admit_job` guard | Compatible combo creates job; incompatible raises before IDs minted; `capabilities=None` skips guard | Fake storage, inject callable returning fake capabilities |
| Unit: web route 422 | DiarizationUnsupported → 422 with "diarization" in body; other errors unchanged | TestClient with mock `admit_job` or real `admit_job` + fake storage |
| Unit: port defense-in-depth | Non-diarizing fake refuses MULTI; diarizing fake accepts; SINGLE always accepted | Existing fakes, no new infrastructure |
| Architecture | No new violations in domain/ports/usecases | Existing `test_architecture.py` — no changes needed |

## Threat Matrix

N/A — no routing, shell, subprocess, VCS/PR automation, executable-file classification, or process-integration boundary in this slice.

## Migration / Rollout

No migration required. The `capabilities` parameter defaults to `None`, which skips the guard — existing callers and in-flight jobs are unaffected. The web route catches a new error type but existing error paths are untouched.

The defense-in-depth guard in fakes replaces an ad-hoc `if request.speaker_mode is SpeakerMode.MULTI` check with the shared helper, which also catches `REQUIRES_SETUP`. This is a behavioral tightening: `FlakyFakeTranscriptionPort` currently does not raise on `MULTI` despite declaring `UNSUPPORTED` diarization. The fix adds the guard to `FlakyFakeTranscriptionPort.transcribe()` as well.

## Open Questions

- None. All decisions resolved by the proposal and spec.
