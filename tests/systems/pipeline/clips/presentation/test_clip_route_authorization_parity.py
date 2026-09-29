"""`13b.32c`/`32d`: the clip routes join the generated 401 and 403 checks.

Confirms two things, not one. First, that `POST .../clips`,
`GET .../clips/{clip_id}` and `GET .../clips/{clip_id}/{profile}` are *seen*
by `_registered_route_cases` and `_mutating_job_routes` -- the generators
`test_auth_gate.py` and `test_mutation_ownership_matrix.py` already build
from `app.routes`, so a route joining the table is enough for those files'
own parametrized tests to cover it without a line changing here.

Second, and this is the finding the task asked to verify rather than assume:
those two generators sent every POST route the *same* canned body
(`{"engine": "local"}` for JSON, `b"x"` for everything else). That body
satisfies `AdmitJobRequest` but not `ClipExportRequest`, and ownership is
decided inside the handler, after FastAPI resolves a declared body model --
so a non-owner's malformed body came back `422`, not `403`, and the generator
was "seeing" the route while refusing it for the wrong reason. (At the time,
authentication was also handler-first and an unauthenticated caller saw `422`
before `401`; since slice 1c the principal resolves as a route dependency, so
the 401 gate refuses whatever body arrives -- the body shapes below matter to
the 403 matrix.) Fixed in `conftest.py`'s `route_request_body`, keyed by
route rather than by method, so both generators reach each route's own
authorization step before that route's own body validation could intercept
the request -- no change to the routes themselves.
"""

import pytest
from pydantic import ValidationError

from onevoicecut.systems.pipeline.clips.presentation.schemas.v1.clip_schemas import (
    ClipExportRequest,
)
from tests.unit.adapters.web.conftest import route_request_body
from tests.unit.adapters.web.test_auth_gate import PROBE_JOB_ID, _registered_route_cases
from tests.unit.adapters.web.test_mutation_ownership_matrix import _mutating_job_routes


def test_the_post_clips_route_is_seen_by_the_401_gate() -> None:
    cases = _registered_route_cases()

    assert ("POST", f"/api/jobs/{PROBE_JOB_ID}/clips") in cases


def test_both_get_clip_routes_are_seen_by_the_401_gate() -> None:
    cases = _registered_route_cases()

    assert ("GET", f"/api/jobs/{PROBE_JOB_ID}/clips/{{clip_id}}") in cases
    assert ("GET", f"/api/jobs/{PROBE_JOB_ID}/clips/{{clip_id}}/{{profile}}") in cases


def test_the_post_clips_route_is_seen_by_the_403_matrix() -> None:
    cases = _mutating_job_routes()

    assert ("POST", "/api/jobs/{job_id}/clips") in cases


def test_neither_get_clip_route_is_in_the_403_matrix() -> None:
    """Reading is shared (AUTH invariant): a `GET` names a job and still must
    not be gated on ownership, so it belongs outside the mutation class."""
    cases = _mutating_job_routes()

    assert not any(
        path.startswith("/api/jobs/{job_id}/clips") for method, path in cases if method == "GET"
    )


def test_a_body_shaped_for_the_admit_route_would_have_failed_before_auth() -> None:
    """The regression this file exists to pin: the old single canned body was
    valid for `AdmitJobRequest` and invalid for `ClipExportRequest`, so it
    proved nothing about `POST .../clips` reaching authentication at all."""
    with pytest.raises(ValidationError):
        ClipExportRequest.model_validate({"engine": "local"})


def test_route_request_body_gives_the_clips_route_a_body_it_accepts() -> None:
    _content, json_body = route_request_body("POST", f"/api/jobs/{PROBE_JOB_ID}/clips")

    ClipExportRequest.model_validate(json_body)
