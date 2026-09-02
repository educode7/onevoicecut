"""The shapes of the shared board, pinned before the route exists.

D10's contract: the listing is a wrapper object, so pagination and totals stay
additive where a bare array would not; items are record-derived only; and the
status response gains `owner` without moving a single pre-change field. The
frontend is built against these field names — they are the API.
"""

from onevoicecut.adapters.web.schemas import (
    JobListItem,
    JobListResponse,
    JobStatusResponse,
)
from onevoicecut.domain.jobs import EngineChoice, JobState, SpeakerMode

JOB_ID = "01HQ3M8XKJ7VNPQR2ZYWB4TCFD"

# Every field the status response carried before this change. VIS-06 demands
# each one survive with its name and meaning, so the set is pinned here.
PRE_CHANGE_STATUS_FIELDS = {
    "job_id",
    "state",
    "engine",
    "speaker_mode",
    "error",
    "progress",
}


def an_item(owner: str | None = "operator-a") -> JobListItem:
    return JobListItem(
        job_id=JOB_ID,
        state=JobState.PENDING,
        owner=owner,
        engine=EngineChoice.LOCAL,
        speaker_mode=SpeakerMode.SINGLE,
        created_at=1000.0,
        updated_at=1001.0,
    )


def test_the_listing_is_a_wrapper_object_not_a_bare_array() -> None:
    """D10: pagination, totals, anything later, joins the wrapper additively.
    A bare array would make every addition a shape change for every client."""
    response = JobListResponse(jobs=[an_item()])

    assert response.model_dump() == {"jobs": [an_item().model_dump()]}


def test_a_list_item_carries_exactly_the_record_derived_fields() -> None:
    """The listing performs no per-job plan/results scans — a poll of the shared
    board costs one directory listing, and progress remains the per-job status
    read. The exact field set is the JSON contract; nothing sneaks in."""
    assert set(JobListItem.model_fields) == {
        "job_id",
        "state",
        "owner",
        "engine",
        "speaker_mode",
        "created_at",
        "updated_at",
    }


def test_owned_and_legacy_items_both_serialize() -> None:
    """VIS-04 at the shape level: a legacy record's absent owner is `null` in
    the response, not a missing key and not an error."""
    assert an_item("operator-a").model_dump()["owner"] == "operator-a"
    assert an_item(None).model_dump()["owner"] is None


def test_the_status_response_gains_owner_additively() -> None:
    """VIS-06: every pre-change field keeps its name, and exactly one field is
    added — a client shaped against the pre-change response still parses."""
    assert (
        set(JobStatusResponse.model_fields) == PRE_CHANGE_STATUS_FIELDS | {"owner"}
    )
