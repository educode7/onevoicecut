"""The concurrency cap: one global integer, validated at boot.

Default 1 is not timidity, it is the only number any measurement supports. The
premise is multi-hour, CPU-bound local ASR on one shared server, and local
transcription saturates a machine by itself — two concurrent jobs mostly
time-slice and make both slower. A default of 2 would bake an unmeasured guess
into configuration; an operator with headroom raises one env var.

Validation is fail-closed for the same reason the token map is: a cap of 0 means
a queue that never drains, and the operator would find out by watching jobs sit
QUEUED forever with every response reporting success.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from onevoicecut.runtime.settings import Settings


def _settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    monkeypatch.setenv("ONEVOICECUT_DATA_DIR", str(tmp_path))
    return Settings()  # type: ignore[call-arg]


def test_the_cap_defaults_to_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("ONEVOICECUT_MAX_CONCURRENT_JOBS", raising=False)

    assert _settings(monkeypatch, tmp_path).max_concurrent_jobs == 1


def test_an_operator_with_headroom_raises_it_by_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ONEVOICECUT_MAX_CONCURRENT_JOBS", "3")

    assert _settings(monkeypatch, tmp_path).max_concurrent_jobs == 3


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("0", id="zero-drains-nothing"),
        pytest.param("-1", id="negative"),
        pytest.param("1.5", id="fractional"),
        pytest.param("many", id="not-a-number"),
        pytest.param("", id="empty"),
    ],
)
def test_an_unusable_cap_refuses_boot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, raw: str
) -> None:
    """The server refuses to come up rather than coming up wrong.

    Zero is the one worth naming: it is not "no limit", it is a queue with no
    exit. Every upload would answer 204 and every job would stay QUEUED, which
    is exactly the shape of failure this whole change exists to remove.
    """
    monkeypatch.setenv("ONEVOICECUT_MAX_CONCURRENT_JOBS", raw)

    with pytest.raises(ValidationError):
        _settings(monkeypatch, tmp_path)


def test_the_cap_is_global_not_per_engine_or_per_operator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One integer, and the field list is the proof.

    Per-engine or per-operator slots would need engine-aware drain bookkeeping
    for a benefit nobody has measured. This pins the decision so the next
    person adds a second field on purpose rather than by drift.
    """
    monkeypatch.delenv("ONEVOICECUT_MAX_CONCURRENT_JOBS", raising=False)
    fields = Settings.model_fields

    assert [name for name in fields if "concurrent" in name] == [
        "max_concurrent_jobs"
    ]
