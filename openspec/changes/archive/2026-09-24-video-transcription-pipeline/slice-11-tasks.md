# Tasks: Slice 11 — Frame Dimensions + Word-Level Timing Retrofit

> Phase: `sdd-tasks` (rev 4 — clip rendering) · Artifact store: hybrid (openspec + Engram
> `sdd/video-transcription-pipeline/slice-11/tasks`)
> Design reference: `design.md` "Decision: `WordTiming` is an additive, defaulted, never-nullable field",
> "Decision: `MediaProbe.frame` is one nullable pair, not two nullable ints", "Rev-4 Slice Ordering —
> three units become seven" (units 11a, 11b). Spec reference: `specs/transcript-artifacts/spec.md`
> (Word-Level Timing, Word-Level Timing Is Consistent With Overlap Stitching), `specs/audio-extraction/spec.md`
> (Media Probe Reports Frame Dimensions).
> **11a and 11b share zero files, zero types, and zero tests** — bundled in the original proposal only
> because both are "domain gaps rendering exposed". 11a is small, needs no storage/codec/migration work,
> and is the unit that unblocks the entire slice-12 geometry track, so it lands first and independently.
> **11b is split three ways** (domain/capability/fakes · stitcher · codec) because it is the highest-risk
> item in rev 4 and the three concerns are independently revertible — a stitcher regression must never
> force reverting the codec's backward-compatible decode, and vice versa.
> Full RED/GREEN task detail lives inline in `tasks.md` under "Slice 11a" / "Slice 11b-i/ii/iii"; this file
> carries the review-workload contract for those same task IDs.

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | ~2,025 (11a ~525 · 11b-i ~625 · 11b-ii ~475 · 11b-iii ~400) |
| Test share expectation | 65–80%, per the measured pattern on every slice since 4a (68–81%, never the original 56%) |
| 800-line budget risk | Low per-unit — every unit individually estimated 400–650 lines, none at the ceiling; High in aggregate across all four |
| Chained PRs recommended | Yes |
| Suggested split | 4 work units, PR 41 → PR 44 (shifted from PR 24–27 by the rev-5 re-baseline of slices 7a–10b) |
| Delivery strategy | auto-chain |
| Chain strategy | stacked-to-main |

```text
Decision needed before apply: No
Chained PRs recommended: Yes
Chain strategy: stacked-to-main
800-line budget risk: Low
```

### Suggested Work Units

| Unit | Goal | PR | Focused test command | Runtime harness | Rollback boundary |
|------|------|----|-----------------------|------------------|--------------------|
| 11a | `MediaProbe.frame`: `FrameSize`, two ffprobe guards (attached cover art, ±90° rotation), `FrameGeometryUnavailable` | PR 41 | `pytest tests/unit/domain/test_media.py tests/unit/adapters/ffmpeg/test_probe_frame.py -m "not paid and not localmodel"` | `pytest -m integration` — real ffmpeg-synthesized fixture confirms the two guards against the real binary | `domain/media.py` (`FrameSize`, `MediaProbe.frame`), `adapters/ffmpeg/extractor.py` (`probe()` frame parsing) |
| 11b-i | `WordTiming` domain + `WordTimingSupport` capability + word-timing-aware fake + contract invariant + `AdmitJob` warning | PR 42 | `pytest tests/unit/domain/test_transcript.py tests/unit/ports/test_capabilities.py tests/unit/usecases/test_admit_job.py tests/contract -m "not paid and not localmodel"` | N/A — fakes only, no real ASR adapter is modified this unit | `domain/transcript.py` (`WordTiming`, `words` field), `ports/capabilities.py` (`WordTimingSupport`), `tests/fakes/transcription.py`, `usecases/admit_job.py` warning branch |
| 11b-ii | Stitcher word-timing lockstep: `_shift`/`_split_words`, boundary dedup, byte-identical regression for word-less transcripts | PR 43 | `pytest tests/unit/usecases/test_stitch_transcript.py -m "not paid and not localmodel"` | N/A — pure functions | `usecases/stitch_transcript.py` (`_shift`, `_split_words`, `_clip_before`/`_clip_after` word-aware branches) |
| 11b-iii | Storage codec backward-compatible decode: absent `words` → `()`, malformed `words` → `CorruptedRecord`, round-trip | PR 44 | `pytest tests/unit/adapters/storage/test_filesystem_transcript_storage.py -m "not paid and not localmodel"` | `pytest -m integration` — round-trip against real files on disk | `adapters/storage/serialization.py` (`_word_timings()` decode helper) |

## Dependency Notes

- **11a has no dependency on 11b** and no 11b unit depends on 11a. Both may be worked in parallel; 11a is
  ordered first only because it is smaller and unblocks slice 12a immediately.
- **11b-i must land before 11b-ii and 11b-iii** — both need the `WordTiming`/`words` field and the
  `WordTimingSupport` capability to exist. 11b-ii and 11b-iii do not depend on each other and may proceed
  in parallel once 11b-i is merged.
- **11b-i also gates slice 13a-ii**, the subtitle half of slice 13: cue building reads
  `TranscriptSegment.words`. No other slice-12 or slice-13 unit depends on the 11b track — slice 12 and
  13a-i/13a-iii depend on 11a instead.
