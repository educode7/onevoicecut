# Archive Report: Slice 6 — Speaker Mode + Engine Selection + Diarization Rejection

> Archived: 2026-08-31 | Change: `video-transcription-pipeline` | Slice: 6 of 23

## What Was Built

Per-job speaker mode and engine selection validated at admission time, plus a port-level defense-in-depth guard. An incompatible engine/speaker-mode combination now fails in milliseconds — before any chunk is dispatched, before any audio is extracted, and before any billable API call or local model invocation occurs.

The implementation extracts `_validate_compatibility()` as one pure helper shared by both the use-case admission guard and the port-level defense, ensuring a single definition of compatibility across the system.

### Files Modified/Created

| File | Role |
|------|------|
| `usecases/admit_job.py` | `_validate_compatibility()` pure helper + `capabilities` parameter on `admit_job()` |
| `adapters/web/app.py` | `capabilities` field added to `WebDependencies` |
| `adapters/web/routers/jobs.py` | Catches `DiarizationUnsupported` → HTTP 422 |
| `tests/fakes/transcription.py` | Shared helper integration; fakes call `_validate_compatibility()` in `transcribe()` |
| `tests/unit/usecases/test_admit_job.py` | 17 tests covering helper, use-case integration, port-level defense |
| `tests/unit/adapters/web/test_admit_job_validation.py` | 4 HTTP validation tests |
| `slice-6-tasks.md` | All 17 tasks marked [x] |

## Decisions Made

1. **`_validate_compatibility` is a module-level pure function** — no side effects, no I/O, no port calls. Used by both admission guard and port-level defense, satisfying the spec's "single definition of compatibility" requirement.

2. **`capabilities` parameter is optional (default `None`)** — backward compatibility with existing callers and tests that do not supply an engine resolver. When `None`, validation is skipped.

3. **`DiarizationUnsupported` maps to HTTP 422** — matches existing error patterns in the web adapter; response body names the missing capability and provides remediation text.

4. **Fakes use the same compatibility definition as admission** — `FakeTranscriptionPort` and `NonClassifyingFakeTranscriptionPort` call `_validate_compatibility()` in their `transcribe()` method, closing the defense-in-depth invariant.

5. **Stale checkbox reconciliation** — main `tasks.md` had slice 6 tasks (6.1–6.17) unchecked despite `slice-6-tasks.md` showing all complete. Reconciled backed by `apply-progress` observation #899 and `slice-6-tasks.md` proof.

## Lessons Learned

- **Stale checkbox reconciliation is mechanical, not a defect** — `sdd-apply` marks tasks in the slice-specific task file, but the main `tasks.md` fell behind. The apply-progress snapshot provides sufficient proof.
- **Defense-in-depth at two layers is worth the duplication** — admission catches the common case; port-level guard catches bypasses. Both use the same helper, so maintenance cost is near zero.
- **Optional parameters enable incremental rollout** — making `capabilities` optional meant no existing test or caller broke, and the feature activates only when an engine resolver is wired.

## Final State

| Metric | Value |
|--------|-------|
| Tests passing | 531 (497 unit + 34 integration) |
| mypy clean | 106 files |
| Spec scenarios | 17/18 compliant (scenario 14 covered indirectly — not a blocker) |
| Design decisions | 5/5 followed |
| Commits | 4 (one per phase + refactor) |

## Archive Contents

- `proposal.md` ✅
- `specs/slice6-speaker-mode/spec.md` ✅ (synced to `openspec/specs/slice6-speaker-mode/spec.md`)
- `design.md` ✅
- `tasks.md` ✅ (17/17 tasks complete)
- `slice-6-tasks.md` ✅
- `slice-6-design.md` ✅
- `slice-6-proposal.md` ✅
- `exploration.md` ✅

## Source of Truth Updated

- `openspec/specs/slice6-speaker-mode/spec.md` — created (no prior main spec existed)

## Next Steps

Slices 7–10b remain:
- **Slice 7a**: Local ASR adapter + contract test (first real engine)
- **Slice 7b**: Supervisory watchdog
- **Slice 8a**: Cloud ASR adapter + real byte cap
- **Slice 8b**: `ChunkTooLarge` split-and-retry
- **Slice 9a**: Local diarization
- **Slice 9b**: Cloud diarization + `SpeakerResolver` seam
- **Slice 10a**: Map-reduce summarization
- **Slice 10b**: Clip candidates + N script variants

## Key Learnings

1. Stale checkbox reconciliation between slice-specific and main tasks files requires apply-progress proof, not assumption.
2. Extracting a shared pure helper for compatibility checking eliminated duplication between admission and port-level defense at near-zero maintenance cost.
3. Optional parameters on use-case functions enable incremental feature rollout without breaking existing callers or tests.
4. Defense-in-depth at two architectural layers catches both common cases and bypasses when using the same underlying validation logic.
5. The archive mechanical copy contract (shell-only copy + hash verification) prevents silent byte corruption during spec sync.
