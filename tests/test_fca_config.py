"""Pins the FCA version registry at the repo root (api-versioning AV-05).

`fca_config.yaml` is the API-version registry: v1 must be the only active
version, every version entry must carry an explicit status that distinguishes
active from deprecated, and the project/security/tooling shape must match the
design decision that created it. AV-04 later compares the active set against
the shipped route table.
"""

from pathlib import Path
from typing import Any, cast

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "fca_config.yaml"


def _load_config() -> dict[str, Any]:
    raw: Any = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    assert isinstance(raw, dict), "fca_config.yaml must parse to a mapping"
    return cast(dict[str, Any], raw)


def test_registry_declares_project_prefix_and_database() -> None:
    config = _load_config()
    project = config["project"]
    assert project["name"] == "onevoicecut"
    assert project["api_prefix"] == "/api/v1"
    assert project["database"] == "filesystem"


def test_single_pipeline_system_with_three_modules_on_v1() -> None:
    config = _load_config()
    systems = config["systems"]
    assert len(systems) == 1, "exactly one system is declared"
    pipeline = systems[0]
    assert pipeline["name"] == "pipeline"
    modules = {module["name"]: module for module in pipeline["modules"]}
    assert set(modules) == {"jobs", "transcripts", "clips"}
    for name in ("jobs", "transcripts", "clips"):
        versions = modules[name]["api_versions"]
        assert [(v["version"], v["status"]) for v in versions] == [
            ("v1", "active")
        ], f"module {name} must declare exactly v1 active"


def test_statuses_distinguish_active_and_only_v1_is_active() -> None:
    config = _load_config()
    entries = [
        version
        for system in config["systems"]
        for module in system["modules"]
        for version in module["api_versions"]
    ]
    assert entries, "the registry must record version entries"
    statuses = {version["status"] for version in entries}
    assert statuses <= {"active", "deprecated", "retired"}, (
        "each entry's status must be an explicit value that distinguishes "
        "active from deprecated"
    )
    active = {version["version"] for version in entries if version["status"] == "active"}
    deprecated = {
        version["version"] for version in entries if version["status"] == "deprecated"
    }
    assert active == {"v1"}, "v1 is the only active version"
    assert active.isdisjoint(deprecated)


def test_security_and_tooling_declare_bearer_pytest_mypy() -> None:
    config = _load_config()
    assert config["security"]["auth"] == "bearer"
    tooling = config["tooling"]
    assert tooling["tests"] == "pytest"
    assert tooling["type_checker"] == "mypy"
