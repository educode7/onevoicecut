# Feature: env test isolation — a composition root must not resurrect a blanked variable

## Problem

The default suite fails 4 tests on any machine whose `.env` configures the
local engine, and those failures are **not** caused by the change under test:

```
FAILED tests/integration/test_worker_entrypoint.py::test_a_build_with_no_engine_configured_says_so
FAILED tests/unit/runtime/test_cloud_engine_wiring.py::TestTheWorkerRefusal::test_it_names_both_variables_now_that_there_are_two_engines
FAILED tests/unit/runtime/test_worker_engine_wiring.py::TestTheWorkerEntrypoint::test_an_unconfigured_build_exits_unusable_naming_the_variable
FAILED tests/unit/runtime/test_worker_engine_wiring.py::TestTheWorkerEntrypoint::test_it_refuses_before_touching_the_job
```

They all assert "this build has no engine configured" and all do it the same
way: `monkeypatch.delenv(LOCAL_MODEL_SIZE_ENV, raising=False)` **before** calling
`worker.main(...)`. The composition root then calls `load_env_file()`, which
reads `Path.cwd()/".env"` with `override=False` and **puts the variable back**.
The test's deletion is undone inside the call it is about to assert on:

```python
k = {'resolver': <EngineResolver>, 'generation': ... model='qwen2.5:7b-instruct' ...}
E   Failed: the job was started without an engine
```

`CLAUDE.md` already documents this exact class of defect for a different
variable — "An empty assignment in `.env` quietly breaks the default test run …
It looks like 27 runtime-test failures that pass in isolation." Same disease,
second vector.

The practical consequence is worse than 4 red tests: **you cannot both run the
app the documented way (fill in `.env`) and have a green suite.**

## Why not something else

- **Do not change `load_env_file`.** Its `override=False` invariant is the
  documented contract ("a real exported variable always wins, so the file is a
  floor and not a mask"), and `worker.main` loading `.env` inside the
  `resolver is None` branch is deliberate — see the comment at `worker.py:432`.
- **Do not blanket-rewrite all 26 `delenv` sites.** `tests/unit/runtime/
  test_env_file_loading.py` uses `delenv` to assert the file *does* arrive;
  replacing those would invert the assertion.
- **Do not weaken the 4 tests.** They must still refuse a configured build.

## Approach

Express "not configured" the way the product already defines it:
`_configured()` is `os.environ.get(name, "").strip() or None`, so **present-but-
empty IS unconfigured**, and `load_dotenv(override=False)` never overwrites a
key that already exists — even one holding `""`. Verified empirically before
writing any code:

```
ONEVOICECUT_LOCAL_MODEL_SIZE: present=True value=''
ONEVOICECUT_LLM_MODEL:        present=True value=''
_configured(LOCAL_MODEL_SIZE) -> None
_configured(LLM_MODEL)        -> None
```

So the fix is `monkeypatch.setenv(VAR, "")` in place of `delenv`. It is not a
cosmetic swap: it makes the test's intent explicit *and* adds an assertion the
old form could not make — if `override` ever flipped to `True`, or the root
stopped honouring a blank value, these tests now fail.

## Scope

- [x] T1 — Fix the 4 observed failures (`delenv` → `setenv(..., "")`).
  *Observed: baseline re-confirmed `4 failed, 37 passed in 9.07s`, then
  `41 passed in 1.32s` after the edits. Assertions unchanged — the 4 tests
  still demand `EXIT_UNUSABLE` / refusal-before-`run_job` / both variable
  names in stderr.*
- [x] T2 — Audit the sibling composition-root tests for the same fragility.
  **Finding: `test_worker_generation_wiring.py:343` is genuinely safe**, and
  for a reason worth recording — it calls `configured_generation()`
  *directly* (`worker.py:306`), a pure environment reader that never invokes
  `load_env_file()`; only `worker.main` (`worker.py:441`, inside the
  `resolver is None` branch) and `get_app` (`main.py:513`) load the file.
  The two tests in that file that *do* reach `worker.main` inject a resolver
  or a generation and so skip the load branch entirely. Not changed.
  **Two latent defects were found and fixed**: `_only_cloud` and `_nothing`
  in `test_cloud_engine_wiring.py`. `_only_cloud`'s `test_a_cloud_only_build_is_usable`
  passed only by accident — its assertion is `!= EXIT_UNUSABLE`, and the
  resurrected local engine made the "cloud-only" build actually local+cloud,
  so it never noticed. A full audit of all 26 `delenv` sites confirmed every
  remaining one is safe (they either inject a resolver, or call
  `Settings()`/`build_dependencies` directly rather than a root, or live in
  the deliberately-protected `test_env_file_loading.py`).
- [x] T3 — Add the missing regression test.
  *`tests/unit/runtime/test_env_isolation.py` (new, 54 lines): crafts its own
  `.env` (`ONEVOICECUT_LOCAL_MODEL_SIZE=small`) in `tmp_path`, `chdir`s into
  it, blanks both engine variables, calls `worker.main`, and asserts
  `EXIT_UNUSABLE` + the variable is still `""` + stderr names it — identical
  on every machine. Observed `1 passed in 0.50s`.*
- [x] T4 — Prove T3 has teeth by mutation.
  *Flipped `settings.py:60` to `override=True` → **`1 failed`**:
  `assert 1 == 3`, stderr `transcribe-worker: no job stored under '01HQ…'` —
  the file refilled `small`, the build became configured, and the refusal
  vanished inside the asserted call. Restored → **`1 passed in 0.49s`**.
  Restore verified three ways: `git diff -- src` empty; sha256 of the working
  file equals `git show HEAD:…` (`952f0266…`, byte-identical); `override=False`
  present, `override=True` absent.*
- [x] T5 — Full suite + `mypy` green.
  *`pytest -m "not paid and not localmodel"` → **2257 passed, 44 deselected,
  0 failed** (was `4 failed, 2249 passed`). `mypy src tests` → **Success: no
  issues found in 374 source files**. Parent spot check independently re-ran
  both and reproduced them exactly. Commit follows this entry.*

## Authorized scope

Test files only. No production source change is authorized by this feature:
the product behaviour under test is correct by design. If a fix appears to
require touching `src/`, STOP and report instead of proceeding.

## Acceptance criteria

- The 4 named failures pass on a machine whose `.env` sets
  `ONEVOICECUT_LOCAL_MODEL_SIZE` (the current machine — no `.env` edit).
- No test that asserts `load_env_file` *does* populate a variable is changed
  in a way that weakens it.
- Default suite: `0 failed`. `mypy src tests`: clean.
- T3 fails when `override=False` is mutated to `True`, passes when restored.

## Checks

```powershell
.venv\Scripts\python.exe -m pytest -m "not paid and not localmodel"
.venv\Scripts\python.exe -m mypy src tests
```

## Route and forecast

- Route: **delegated direct** (writer trigger — 4+ test files touched).
- Authored-line forecast: ~120, inside the 400-line heuristic.
- Delivery: one commit on the current branch.

## Review status

Native RDD review **attempted and blocked by the environment, not by the
change.** Lineage `review-c2361d33622b7dad`, medium tier, one lens
(`review-reliability`), consent granted by the operator. The lens sub-agent
returned, before producing any output:

```
Error from provider (Console): OpenCode's free tier can only be used from within OpenCode
```

That is the **9th identical occurrence** (8 across the previous lineage
`review-09539367570b1f7f`, plus this one). A fresh exact-lineage STATUS
re-offered the same bound slot, so a relaunch was *permitted*; it was not
taken, because the failure is deterministic — 100% identical across every
attempt, with the root cause already read from the config rather than
guessed — and repeating it would add cost but no information. The contract's
"never retry indefinitely" governs here.

The cause is an **upstream OpenCode client/provider defect, not Gentle AI**:
`plugins/opencode-review-transport.ts` materialises the prompt through
`gentle-ai review opencode-transport` and replaces the child's system prompt
with a one-line isolation string, after which the provider refuses the
request. `general` and `review-*` share the same model and no explaining
deny rule. It therefore earns no provider-defect report, and no reviewer
result may be synthesised.

The lineage is left in `reviewing` — releasing it (`gentle-ai review abandon`
with `operator_disposition`) is an operator decision that is still open.
