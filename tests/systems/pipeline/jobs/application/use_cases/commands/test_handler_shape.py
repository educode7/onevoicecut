"""The shape every jobs request and handler must have, asserted as a shape.

CQRS is only worth the ceremony if the split stays mechanical: dependencies are
constructed once and live on the handler, while identity travels on an immutable
request. A handler that reaches for a global instead of its own construction, or
a request that can be mutated after it has been dispatched, both break the
property slice 2c is buying — that a principal can be recorded as an audit
event rather than smuggled through a function parameter.

The three commands land with 2c; the two queries join them with 2d. Identity
travels only where a use case actually reads it: `ListJobsQuery` filters `mine`
against the caller, so it carries a `Principal`, while `GetJobQuery`
deliberately does not — reading is shared (VIS-01/VIS-02 — a foreign job is
readable by everybody), and a field nothing consumes is the kind of drift this
test exists to stop.
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
from onevoicecut.systems.pipeline.jobs.application.use_cases.queries.get_job import (
    GetJobHandler,
    GetJobQuery,
)
from onevoicecut.systems.pipeline.jobs.application.use_cases.queries.list_jobs import (
    ListJobsHandler,
    ListJobsQuery,
)

COMMANDS: list[type[Any]] = [AdmitJobCommand, CancelJobCommand, IngestMediaCommand]
QUERIES: list[type[Any]] = [GetJobQuery, ListJobsQuery]
REQUESTS: list[type[Any]] = [*COMMANDS, *QUERIES]
# The requests whose use case branches on who called: a principal field is
# required here and nowhere else.
IDENTITY_BEARING: list[type[Any]] = [*COMMANDS, ListJobsQuery]
HANDLERS: list[type[Any]] = [
    AdmitJobHandler,
    CancelJobHandler,
    IngestMediaHandler,
    GetJobHandler,
    ListJobsHandler,
]


@pytest.mark.parametrize("request_type", REQUESTS, ids=lambda c: c.__name__)
def test_the_request_is_a_frozen_dataclass(request_type: type[Any]) -> None:
    """Frozen, so a handler cannot be handed a request somebody still mutates."""
    assert is_dataclass(request_type)
    params = getattr(request_type, "__dataclass_params__", None)
    assert params is not None and params.frozen


@pytest.mark.parametrize("request_type", IDENTITY_BEARING, ids=lambda c: c.__name__)
def test_identity_travels_on_the_request(request_type: type[Any]) -> None:
    """The caller's `Principal` is a field of the request, not a handler argument."""
    assert "principal" in {field.name for field in fields(request_type)}
    assert get_type_hints(request_type)["principal"] is Principal


@pytest.mark.parametrize("handler", HANDLERS, ids=lambda h: h.__name__)
def test_the_handler_exposes_handle(handler: type[Any]) -> None:
    """One entry point per handler; `handle()` is how a request is dispatched."""
    assert callable(getattr(handler, "handle", None))
