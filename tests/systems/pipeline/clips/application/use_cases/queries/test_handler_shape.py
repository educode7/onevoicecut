"""The shape every clips query and handler must have, asserted as a shape.

The three reads land with 4c alongside the commands, per the CQRS classification
note: `render_profiles`, `plan_trajectory` and `build_subtitle_cues` derive, so
they are reads. None of them carries identity — a derivation over spans and
detections does not care who asked — which is why no field is pinned here the
way `operator` is pinned on the command side.

What is pinned is the frozen dispatch pair itself: a query that could be mutated
after dispatch lets the caller and the handler disagree about what was derived,
and `handle()` is the only surface a query is allowed to have. One dependency is
permitted on this side, a registry passed in at construction rather than reached
for as a module global, and it lives on the handler like every other one.
"""

from dataclasses import is_dataclass
from typing import Any

import pytest

from onevoicecut.systems.pipeline.clips.application.use_cases.queries.build_subtitle_cues import (
    BuildSubtitleCuesHandler,
    BuildSubtitleCuesQuery,
)
from onevoicecut.systems.pipeline.clips.application.use_cases.queries.plan_trajectory import (
    PlanTrajectoryHandler,
    PlanTrajectoryQuery,
)
from onevoicecut.systems.pipeline.clips.application.use_cases.queries.render_profiles import (
    RenderProfilesHandler,
    RenderProfilesQuery,
)

QUERIES: list[type[Any]] = [
    RenderProfilesQuery,
    PlanTrajectoryQuery,
    BuildSubtitleCuesQuery,
]
HANDLERS: list[type[Any]] = [
    RenderProfilesHandler,
    PlanTrajectoryHandler,
    BuildSubtitleCuesHandler,
]


@pytest.mark.parametrize("query_type", QUERIES, ids=lambda q: q.__name__)
def test_the_query_is_a_frozen_dataclass(query_type: type[Any]) -> None:
    """Frozen, so a handler cannot be handed a query somebody still mutates."""
    assert is_dataclass(query_type)
    params = getattr(query_type, "__dataclass_params__", None)
    assert params is not None and params.frozen


@pytest.mark.parametrize("handler", HANDLERS, ids=lambda h: h.__name__)
def test_the_handler_exposes_handle(handler: type[Any]) -> None:
    """One entry point per handler; `handle()` is how a query is dispatched."""
    assert callable(getattr(handler, "handle", None))
