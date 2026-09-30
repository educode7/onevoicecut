"""Bounded pagination and explicit allow-listing on the shared board.

`job-visibility` VIS-09 through VIS-14, restated by the refactor-fca-layout
delta. Two requirements meet in one route: the listing must be bounded
(`limit` 1..100, `offset` 0..10000, both rejected at 422 before any listing
work) and its response must be projected through a schema that enumerates
every permitted field.

The composition order is the load-bearing half of VIS-12. `mine` narrows the
tuple first and the slice is applied to what came back, so the store's
unscoped listing — the same one startup reconcile uses — stays underneath
untouched. Slicing first and filtering after would still return the right
rows for a single page while making the page *union* depend on the filter,
which is exactly the completeness claim VIS-05 was restated to make across
pages.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from onevoicecut.main import create_app
from onevoicecut.shared.domain.ids import (
    JobId,
    OperatorId,
    make_job_id,
    make_media_id,
)
from onevoicecut.shared.domain.speaker import SpeakerMode
from onevoicecut.systems.pipeline.jobs.domain.jobs import (
    EngineChoice,
    JobRecord,
    JobState,
)
from onevoicecut.systems.pipeline.jobs.presentation.schemas.v1.job_schemas import (
    JobListItem,
    JobListResponse,
)
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    OPERATOR_A,
    OPERATOR_B,
    TOKEN_A,
    TOKEN_B,
    auth_headers,
    web_dependencies,
)

LISTING = "/api/v1/jobs"
PAGE_LIMIT = 2
# A union walk needs a stop condition that does not depend on pagination
# working, or the RED run hangs instead of failing.
MAX_PAGES = 50


def _seed_jobs(
    storage: FakeTranscriptStoragePort,
    count: int,
    *,
    owner: OperatorId | None,
    start: int = 0,
) -> list[JobId]:
    """Distinct records straight onto the store, for counts an admission loop
    would only slow down. The 23-char stem plus a 3-digit suffix is a valid
    ULID under `_ULID_PATTERN`, so ids stay generated rather than hand-typed.
    `start` keeps successive seeds disjoint — the counter would otherwise
    restart and hand the second call the first call's ids."""
    ids = [
        make_job_id(f"01HQ3M8XKJ7VNPQR2ZYWB4T{start + i:03d}")
        for i in range(count)
    ]
    for job_id in ids:
        storage.create_job(
            JobRecord(
                job_id=job_id,
                media_id=make_media_id("01HQ3M8XKJ7VNPQR2ZYWB4TCFE"),
                state=JobState.PENDING,
                speaker_mode=SpeakerMode.SINGLE,
                engine=EngineChoice.LOCAL,
                created_at=1000.0,
                updated_at=1000.0,
                worker_pid=None,
                error=None,
                owner=owner,
            )
        )
    return ids


@pytest.fixture
async def client(
    tmp_path: Path,
) -> AsyncIterator[tuple[AsyncClient, FakeTranscriptStoragePort]]:
    deps, storage = web_dependencies(tmp_path)
    app = create_app(deps)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http, storage


async def _page_through(
    http: AsyncClient,
    token: str,
    *,
    limit: int,
    extra: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Walk every page of a listing at one page size, returning the union.

    Both bounds are load-bearing. The per-page assertion fails *fast* when
    `limit` is ignored rather than honored, which is what makes this helper
    usable as a RED probe: without it a pre-migration listing returns the full
    board on every call, `len(items) < limit` never holds, and the walk never
    terminates. The page cap is the backstop for the same condition.
    """
    collected: list[dict[str, Any]] = []
    offset = 0
    for _ in range(MAX_PAGES):
        params: dict[str, str] = {"limit": str(limit), "offset": str(offset)}
        params.update(extra or {})
        response = await http.get(LISTING, params=params, headers=auth_headers(token))
        assert response.status_code == 200, response.text
        items = response.json()["jobs"]
        assert len(items) <= limit, (
            f"page at offset {offset} returned {len(items)} items for "
            f"limit={limit}: the bound is not being applied"
        )
        collected.extend(items)
        if len(items) < limit:
            return collected
        offset += limit
    raise AssertionError(
        f"the listing never yielded a short page after {MAX_PAGES} pages of "
        f"{limit} — pagination is not advancing"
    )


async def test_vis10_an_over_maximum_limit_is_rejected_before_any_listing_work(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """VIS-10: `limit=101` is refused at the boundary rather than clamped, and
    the refusal costs nothing — the store is never asked for the listing."""
    http, storage = client
    _seed_jobs(storage, 3, owner=None)
    calls_before = list(storage.calls)

    response = await http.get(
        LISTING, params={"limit": "101"}, headers=auth_headers(TOKEN_A)
    )

    assert response.status_code == 422
    assert storage.calls == calls_before, "a 422 must cost no listing work"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        pytest.param("limit", "abc", id="limit-not-an-integer"),
        pytest.param("limit", "0", id="limit-zero"),
        pytest.param("limit", "-1", id="limit-negative"),
        pytest.param("limit", "101", id="limit-above-maximum"),
        pytest.param("offset", "abc", id="offset-not-an-integer"),
        pytest.param("offset", "-1", id="offset-negative"),
        pytest.param("offset", "10001", id="offset-above-maximum"),
    ],
)
async def test_vis11_malformed_pagination_is_rejected_before_any_listing_work(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
    key: str,
    value: str,
) -> None:
    """VIS-11: every malformed value on either bound answers 422 with no
    listing computed — the parameter is validated, not coerced and clamped."""
    http, storage = client
    _seed_jobs(storage, 3, owner=None)
    calls_before = list(storage.calls)

    response = await http.get(
        LISTING, params={key: value}, headers=auth_headers(TOKEN_A)
    )

    assert response.status_code == 422
    assert storage.calls == calls_before, "a 422 must cost no listing work"


async def test_vis09_the_page_size_is_honored_and_attribution_is_unchanged(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """VIS-09: five jobs, `limit=2&offset=0` returns at most two — and each
    row carries the same owner it would have carried unpaginated, because
    pagination slices projected rows rather than reshaping them.

    The five are seeded with mixed attribution on purpose: comparing a page
    against the full listing only says something if the rows can disagree."""
    http, storage = client
    _seed_jobs(storage, 3, owner=OPERATOR_A)
    _seed_jobs(storage, 2, owner=OPERATOR_B, start=3)

    page = await http.get(
        LISTING,
        params={"limit": "2", "offset": "0"},
        headers=auth_headers(TOKEN_A),
    )
    unpaginated = await http.get(LISTING, headers=auth_headers(TOKEN_A))

    assert page.status_code == 200
    assert unpaginated.status_code == 200
    items = page.json()["jobs"]
    assert len(items) <= 2

    by_id = {item["job_id"]: item for item in unpaginated.json()["jobs"]}
    for item in items:
        assert item["owner"] == by_id[item["job_id"]]["owner"]


async def test_an_omitted_limit_is_a_bounded_default_page_not_the_whole_board(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """The settled design default: an omitted `limit` is a page of 20, never
    the full listing. A board that grows past 20 must not become one
    unbounded response by accident."""
    http, storage = client
    _seed_jobs(storage, 25, owner=None)

    response = await http.get(LISTING, headers=auth_headers(TOKEN_A))

    assert response.status_code == 200
    assert len(response.json()["jobs"]) == 20


async def test_vis12_the_mine_filter_composes_with_pagination(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """VIS-12: three jobs of A and some of B — a filtered page holds only A's
    rows, and the union of filtered pages is exactly A's jobs, nothing of B's
    and nothing dropped."""
    http, storage = client
    a_ids = _seed_jobs(storage, 3, owner=OPERATOR_A)
    b_ids = _seed_jobs(storage, 4, owner=OPERATOR_B, start=3)

    first = await http.get(
        LISTING,
        params={"mine": "true", "limit": str(PAGE_LIMIT), "offset": "0"},
        headers=auth_headers(TOKEN_A),
    )
    union = await _page_through(
        http, TOKEN_A, limit=PAGE_LIMIT, extra={"mine": "true"}
    )

    assert first.status_code == 200
    assert len(first.json()["jobs"]) <= PAGE_LIMIT
    assert {item["job_id"] for item in first.json()["jobs"]} <= set(a_ids)

    union_ids = [item["job_id"] for item in union]
    assert sorted(union_ids) == sorted(a_ids)
    assert not set(union_ids) & set(b_ids), (
        "the page union must be sliced from the already-filtered tuple: "
        "filtering after slicing lets a foreign row into some page"
    )


async def test_vis13_every_serialized_key_is_declared_on_the_response_schema(
    client: tuple[AsyncClient, FakeTranscriptStoragePort],
) -> None:
    """VIS-13: the wrapper and its items expose exactly what the schema
    declares — the response is a projection, never a record."""
    http, storage = client
    _seed_jobs(storage, 2, owner=None)

    response = await http.get(LISTING, headers=auth_headers(TOKEN_A))
    assert response.status_code == 200

    payload = response.json()
    assert set(payload) <= set(JobListResponse.model_fields)
    for item in payload["jobs"]:
        assert set(item) <= set(JobListItem.model_fields), (
            f"undeclared item fields: {sorted(set(item) - set(JobListItem.model_fields))}"
        )


def test_vis14_a_planted_undeclared_field_is_refused_by_name() -> None:
    """VIS-14: the allow-list is enforced at construction, so a field nobody
    declared cannot enter the response at all.

    The plant is the point: `JobListResponse` and `JobListItem` must refuse a
    keyword they do not declare and name it in the refusal, rather than
    silently dropping it the way an unconfigured pydantic model does. Silent
    dropping is the dangerous half — the response would look correct while the
    code that built it believed it had sent something.
    """
    with pytest.raises(ValidationError) as wrapper_plant:
        JobListResponse(jobs=[], planted_total=7)  # type: ignore[call-arg]
    assert "planted_total" in str(wrapper_plant.value)

    with pytest.raises(ValidationError) as item_plant:
        JobListItem(
            job_id="01HQ3M8XKJ7VNPQR2ZYWB4TCFD",
            state=JobState.PENDING,
            owner=None,
            engine=EngineChoice.LOCAL,
            speaker_mode=SpeakerMode.SINGLE,
            created_at=1.0,
            updated_at=1.0,
            planted_secret="token-value",  # type: ignore[call-arg]
        )
    assert "planted_secret" in str(item_plant.value)
