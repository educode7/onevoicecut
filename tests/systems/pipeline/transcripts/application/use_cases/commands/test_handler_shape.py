"""The shape every transcripts command and handler must have, asserted as a shape.

CQRS is only worth the ceremony if the split stays mechanical: dependencies are
constructed once and live on the handler, while identity — whichever job, which
track, which results — travels on an immutable command. A handler that reaches
for a global instead of its own construction, or a command that can be mutated
after dispatch, both break the property the split is buying: that the loop's
order can be proven once, against a record, without the inputs shifting
underneath it.

Transcripts commands carry no `Principal`, deliberately: nothing here branches
on who called — every job is worker-driven — so unlike the jobs suite there is
no identity-bearing field to pin. What is pinned is the frozen dispatch pair
itself, for all three.
"""

from dataclasses import is_dataclass
from typing import Any

import pytest

from onevoicecut.systems.pipeline.transcripts.application.use_cases.commands.plan_chunks import (
    PlanChunksCommand,
    PlanChunksHandler,
)
from onevoicecut.systems.pipeline.transcripts.application.use_cases.commands.stitch_transcript import (
    StitchTranscriptCommand,
    StitchTranscriptHandler,
)
from onevoicecut.systems.pipeline.transcripts.application.use_cases.commands.transcribe_job import (
    TranscribeJobCommand,
    TranscribeJobHandler,
)

COMMANDS: list[type[Any]] = [
    TranscribeJobCommand,
    PlanChunksCommand,
    StitchTranscriptCommand,
]
HANDLERS: list[type[Any]] = [
    TranscribeJobHandler,
    PlanChunksHandler,
    StitchTranscriptHandler,
]


@pytest.mark.parametrize("command_type", COMMANDS, ids=lambda c: c.__name__)
def test_the_command_is_a_frozen_dataclass(command_type: type[Any]) -> None:
    """Frozen, so a handler cannot be handed a command somebody still mutates."""
    assert is_dataclass(command_type)
    params = getattr(command_type, "__dataclass_params__", None)
    assert params is not None and params.frozen


@pytest.mark.parametrize("handler", HANDLERS, ids=lambda h: h.__name__)
def test_the_handler_exposes_handle(handler: type[Any]) -> None:
    """One entry point per handler; `handle()` is how a command is dispatched."""
    assert callable(getattr(handler, "handle", None))
