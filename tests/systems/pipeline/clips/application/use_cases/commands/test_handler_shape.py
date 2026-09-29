"""The shape every clips command and handler must have, asserted as a shape.

CQRS is worth the ceremony only if the split stays mechanical: dependencies are
constructed once and live on the handler, while identity — which job, which
candidate, which rendering — travels on an immutable command. A handler that
reaches for a global instead of its own construction, or a command that can be
mutated after dispatch, both break the property the split is buying: that what
the caller dispatched is what the handler ran.

The four commands land with 4c. Only `PurgeJobArtifactsCommand` carries identity:
its `operator` is the authenticated caller, the same identity the other mutations
gate on, so the eventual route needs no signature surgery (OWN-06). The other
three carry no identity field on purpose — ownership is the shared
`require_owner` domain rule rather than a handler argument (design.md's CQRS
decision), and a field nothing consumes is the kind of drift this test exists
to stop.
"""

from dataclasses import fields, is_dataclass
from typing import Any, get_type_hints

import pytest

from onevoicecut.shared.domain.ids import OperatorId
from onevoicecut.systems.pipeline.clips.application.use_cases.commands.generate_artifacts import (
    GenerateArtifactsCommand,
    GenerateArtifactsHandler,
)
from onevoicecut.systems.pipeline.clips.application.use_cases.commands.purge_job_artifacts import (
    PurgeJobArtifactsCommand,
    PurgeJobArtifactsHandler,
)
from onevoicecut.systems.pipeline.clips.application.use_cases.commands.render_clip import (
    RenderClipCommand,
    RenderClipHandler,
)
from onevoicecut.systems.pipeline.clips.application.use_cases.commands.request_clip_export import (
    RequestClipExportCommand,
    RequestClipExportHandler,
)

COMMANDS: list[type[Any]] = [
    GenerateArtifactsCommand,
    RequestClipExportCommand,
    RenderClipCommand,
    PurgeJobArtifactsCommand,
]
# The command whose use case names who asked: an operator field is required
# here and nowhere else in the clips suite.
IDENTITY_BEARING: list[type[Any]] = [PurgeJobArtifactsCommand]
HANDLERS: list[type[Any]] = [
    GenerateArtifactsHandler,
    RequestClipExportHandler,
    RenderClipHandler,
    PurgeJobArtifactsHandler,
]


@pytest.mark.parametrize("command_type", COMMANDS, ids=lambda c: c.__name__)
def test_the_command_is_a_frozen_dataclass(command_type: type[Any]) -> None:
    """Frozen, so a handler cannot be handed a command somebody still mutates."""
    assert is_dataclass(command_type)
    params = getattr(command_type, "__dataclass_params__", None)
    assert params is not None and params.frozen


@pytest.mark.parametrize("command_type", IDENTITY_BEARING, ids=lambda c: c.__name__)
def test_identity_travels_on_the_command(command_type: type[Any]) -> None:
    """The caller's `OperatorId` is a field of the command, not a handler argument."""
    assert "operator" in {field.name for field in fields(command_type)}
    assert get_type_hints(command_type)["operator"] is OperatorId


@pytest.mark.parametrize("handler", HANDLERS, ids=lambda h: h.__name__)
def test_the_handler_exposes_handle(handler: type[Any]) -> None:
    """One entry point per handler; `handle()` is how a command is dispatched."""
    assert callable(getattr(handler, "handle", None))
