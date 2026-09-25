"""The shape every jobs command and handler must have, asserted as a shape.

CQRS is only worth the ceremony if the split stays mechanical: dependencies are
constructed once and live on the handler, while identity travels on an immutable
command. A handler that reaches for a global instead of its own construction, or
a command that can be mutated after it has been dispatched, both break the
property slice 2c is buying — that a principal can be recorded as an audit
event rather than smuggled through a function parameter.

Three handlers land here and one more arrives with 2d. This is the test that
stops any of them quietly drifting.
"""

from dataclasses import fields, is_dataclass
from typing import Any, get_type_hints

import pytest

from onevoicecut.shared.application.principal import Principal
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.admit_job import (
    AdmitJobCommand,
    AdmitJobHandler,
)
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.cancel_job import (
    CancelJobCommand,
    CancelJobHandler,
)
from onevoicecut.systems.pipeline.jobs.application.use_cases.commands.ingest_media import (
    IngestMediaCommand,
    IngestMediaHandler,
)

COMMANDS: list[type[Any]] = [AdmitJobCommand, CancelJobCommand, IngestMediaCommand]
HANDLERS: list[type[Any]] = [AdmitJobHandler, CancelJobHandler, IngestMediaHandler]


@pytest.mark.parametrize("command", COMMANDS, ids=lambda c: c.__name__)
def test_the_command_is_a_frozen_dataclass(command: type[Any]) -> None:
    """Frozen, so a handler cannot be handed a command somebody still mutates."""
    assert is_dataclass(command)
    params = getattr(command, "__dataclass_params__", None)
    assert params is not None and params.frozen


@pytest.mark.parametrize("command", COMMANDS, ids=lambda c: c.__name__)
def test_identity_travels_on_the_command(command: type[Any]) -> None:
    """The caller's `Principal` is a field of the command, not a handler argument."""
    assert "principal" in {field.name for field in fields(command)}
    assert get_type_hints(command)["principal"] is Principal


@pytest.mark.parametrize("handler", HANDLERS, ids=lambda h: h.__name__)
def test_the_handler_exposes_handle(handler: type[Any]) -> None:
    """One entry point per handler; `handle()` is how a command is dispatched."""
    assert callable(getattr(handler, "handle", None))
