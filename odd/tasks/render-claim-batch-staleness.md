# render-claim-batch-staleness

## Objective

Scale the render drain's claim-staleness budget by the profiles a batch still
owes, so a legitimately working multi-profile render worker is never mistaken
for an abandoned one and relaunched against the same output files.

## Problem / Why

`_render_group_is_live` (`src/onevoicecut/runtime/app.py`) bounds a
`RENDERING` group's claim with a **single** `render_timeout_for(span)`, while
`render_pending_exports` (`src/onevoicecut/runtime/render_worker.py`) writes
that claim **once** and then renders every pending profile sequentially in one
process. For a clip with 2+ profiles, the cumulative time legitimately exceeds
one profile's timeout, so the next sweep reads the claim as stale, classifies
the group as an abandoned pickup and launches a **second** worker against the
same destination paths: concurrent ffmpeg writers on one MP4, duplicate
`save_clip_export`/`write_render_claim`, and load past
`max_concurrent_renders`.

This is finding **R4-render-claim-multiprofile-staleness** (CRITICAL,
corroborated by the refuter) from lineage `review-09cbd135702e616d`, whose
sibling finding R3-1 was already fixed in `e21e1c0`. The frozen transaction can
no longer be re-entered (`applicability: unrelated`,
`forbidden_unrelated_target`), so this fix ships as its own work unit with a
fresh native review candidate.

## Approach (decision taken by the operator: option A)

- Budget = `remaining_profiles * render_timeout_for(span)`, where
  `remaining_profiles` counts group rows still in a non-terminal state. That is
  the exact sum of per-call caps for N sequential ffmpeg calls, each itself
  capped by `render_timeout_for`.
- Counting **remaining** rows (not `len(group)`) makes the budget shrink as
  profiles finish, so a dead worker does not hold its slot longer than needed.
- The state set moves to the clips domain as one shared constant so the count
  and `render_worker`'s claim cannot drift apart (the same drift hazard the
  readability lens flagged as R2-002 for duplicated interval literals).

## Authorized scope

- `src/onevoicecut/systems/pipeline/clips/domain/rendering.py`
- `src/onevoicecut/runtime/app.py`
- `src/onevoicecut/runtime/render_worker.py`
- `tests/unit/runtime/test_render_drain_once.py`
- `odd/tasks/render-claim-batch-staleness.md`

## Constraints (repo conventions)

- Strict TDD: observed RED for the new failing tests, then GREEN, then
  refactor while green. Default suite must stay green with 0 skips.
- `mypy src tests` must stay clean (strict).
- Use-case/runtime code reads env only through composition roots; no new
  settings, no new dependencies.
- English for code, comments, docstrings, tests and commits; conventional
  commits; no AI attribution.

## Tasks

- [x] T1 (writer, RED): add the multi-profile staleness tests to
      `tests/unit/runtime/test_render_drain_once.py` — a 2-profile `RENDERING`
      group whose claim is older than one profile's budget but inside the
      whole batch's budget must be treated as **live** (today it relaunches:
      this is the RED), a group past the whole batch budget must still be
      relaunched, and a group with one `RENDERING` + one `DONE` row must keep
      the single-profile budget (proves "remaining", not `len(group)`).
- [x] T2 (writer, GREEN): introduce the shared clips-domain state constant,
      count the remaining batch in `_render_group_is_live` and multiply the
      timeout; update the docstring that currently claims one profile's span
      speaks for the batch; point `render_worker`'s claim at the same constant.
- [x] T3 (writer): full default suite + `mypy src tests` observed green, then
      one work-unit commit on `fix/render-claim-batch-staleness`.

## Acceptance criteria

- The three new tests are green and their RED was observed before the fix.
- A dead multi-profile worker still frees its slot (no over-hold past the
  batch budget).
- Default suite green, 0 skips; `mypy src tests` clean.
- One work-unit Conventional Commit; record its identity below.

## Route and forecast

- Route: delegated direct (writer trigger: 4 non-trivial files).
- TDD: strict (project config). Runner: `python -m pytest`.
- Forecast authored lines: ~80 (additions + deletions, tests included) — well
  under the 400-line delivery budget, single work unit, delivery strategy
  `ask-on-risk`.

## Progress log

- 2026-10-05: feature document created after diagnosis; branch
  `fix/render-claim-batch-staleness` cut from `6a9547d`. Next: delegate T1-T3
  to one writer.
- 2026-10-05 (writer): T1-T3 done, strict RED -> GREEN.
  RED observed: `python -m pytest tests/unit/runtime/test_render_drain_once.py
  -q -k "budget"` -> `1 failed, 2 passed` with
  `assert [('01HQ3M8XKJ7VNPQR2ZYWB4TCA1', '01HQ3M8XKJ7VNPQR2ZYWB4TCB1')] == []`
  (the batch was relaunched while still inside its budget). GREEN after the
  fix: `3 passed`. Verification (all observed):
  `tests/unit/runtime/test_render_drain_once.py`: `23 passed`;
  `python -m pytest -m "not paid and not localmodel"`: `2262 passed, 44
  deselected, 0 skips`; bare `python -m pytest -q`: `2296 passed, 10 skipped`
  (the 10 are the `paid` cloud-contract tests skipping on an unset
  `CLOUD_ASR_API_KEY`, untouched by this diff); `mypy src tests`: `Success: no
  issues found in 375 source files`. Work-unit commit: `08e9d25`.
  Route: delegated direct (writer trigger: 4 non-trivial files).
- 2026-10-05: parent spot check re-ran `tests/unit/runtime/test_render_drain_once.py`
  -> `23 passed`. RDD assessment for `6a9547d..08e9d25`
  (`--committed-only`): risk `medium` (`executable_change` on
  `src/onevoicecut/runtime/app.py`), `review_due: false`,
  `review_due_reason: under_budget` — the slice stays pending (206 of ~400
  authored changed lines) and the reviewed boundary remains `6a9547d`.
- 2026-10-05: T1-T3 done by the writer — RED observed (the in-budget
  two-profile group was relaunched), then `RENDERABLE_STATES` shared between
  the worker's claim and the drain's `remaining * render_timeout_for(span)`
  budget; target file 23 passed, default suite green with 0 skips under
  `pytest -q -m "not paid and not localmodel"`, `mypy src tests` clean.
