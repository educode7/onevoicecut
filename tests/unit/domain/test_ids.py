import pytest

from onevoicecut.domain.ids import InvalidIdError, make_job_id, make_media_id

VALID_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


class TestJobId:
    def test_accepts_valid_ulid(self) -> None:
        job_id = make_job_id(VALID_ULID)
        assert job_id == VALID_ULID

    def test_rejects_too_short_string(self) -> None:
        with pytest.raises(InvalidIdError):
            make_job_id("TOO-SHORT")

    def test_rejects_lowercase_ulid(self) -> None:
        with pytest.raises(InvalidIdError):
            make_job_id(VALID_ULID.lower())

    def test_rejects_disallowed_characters(self) -> None:
        # ULID Crockford Base32 excludes I, L, O, U
        with pytest.raises(InvalidIdError):
            make_job_id("0" * 25 + "I")


class TestMediaId:
    def test_accepts_valid_ulid(self) -> None:
        media_id = make_media_id(VALID_ULID)
        assert media_id == VALID_ULID

    def test_rejects_invalid_string(self) -> None:
        with pytest.raises(InvalidIdError):
            make_media_id("not-a-ulid")
