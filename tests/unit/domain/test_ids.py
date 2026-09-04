import pytest

from onevoicecut.domain.ids import (
    ClipId,
    InvalidIdError,
    OperatorId,
    generate_clip_id,
    make_clip_id,
    make_job_id,
    make_media_id,
    make_operator_id,
)

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


class TestOperatorId:
    """Operator names are configuration, not minted ids: `[a-z0-9_-]{1,64}`.

    The same validation guards record decoding (an invalid owner string fails
    closed as a corrupt record), so the accepted grammar is proven here once.
    """

    def test_accepts_the_minimal_name(self) -> None:
        assert make_operator_id("a") == "a"

    def test_accepts_the_maximal_name(self) -> None:
        name = "a" * 64
        assert make_operator_id(name) == name

    def test_accepts_digits_underscore_and_hyphen(self) -> None:
        assert make_operator_id("maria-2_x") == "maria-2_x"

    def test_rejects_an_empty_name(self) -> None:
        with pytest.raises(InvalidIdError):
            make_operator_id("")

    def test_rejects_a_name_of_65_characters(self) -> None:
        with pytest.raises(InvalidIdError):
            make_operator_id("a" * 65)

    def test_rejects_uppercase(self) -> None:
        with pytest.raises(InvalidIdError):
            make_operator_id("Maria")

    @pytest.mark.parametrize("bad", [".", ":", ";", " ", "a b", "a\n"])
    def test_rejects_separator_and_whitespace_characters(self, bad: str) -> None:
        # `:` and `;` delimit the token map; a name containing either would
        # parse as structure rather than identity.
        with pytest.raises(InvalidIdError):
            make_operator_id(bad)

    def test_is_a_newtype_over_str(self) -> None:
        assert getattr(OperatorId, "__supertype__") is str


class TestClipId:
    """Clips get server-minted ULIDs for the same reason jobs do.

    A clip id becomes a path component under the job directory and a route
    parameter, so it is validated against the identical pattern rather than a
    looser one — a second, weaker regex for the same class of value is how one
    of them eventually accepts a `..`.
    """

    def test_it_accepts_what_a_job_id_accepts(self) -> None:
        """Compared against the raw string, not against a `JobId`. mypy rejects
        that comparison as non-overlapping — the two are distinct `NewType`s,
        which is the point — so the assertion that carries meaning at runtime is
        that the same value survives both."""
        assert make_clip_id(VALID_ULID) == VALID_ULID

    @pytest.mark.parametrize("bad", ["", "short", "a" * 26, "01ARZ3NDEKTSV4RRFFQ69G5FA!"])
    def test_it_rejects_what_a_job_id_rejects(self, bad: str) -> None:
        with pytest.raises(InvalidIdError):
            make_clip_id(bad)

    def test_it_rejects_the_ambiguous_letters(self) -> None:
        """Crockford base32 omits I, L, O and U so an id read off a screen
        cannot be transcribed into a different valid one."""
        with pytest.raises(InvalidIdError):
            make_clip_id("01ARZ3NDEKTSV4RRFFQ69G5FAI")

    def test_a_generated_id_is_valid(self) -> None:
        assert make_clip_id(generate_clip_id())

    def test_generated_ids_sort_by_creation_time(self) -> None:
        """The property `list_jobs` already leans on, now for clips: a directory
        listing is in creation order with nothing to sort by."""
        first = generate_clip_id()
        second = generate_clip_id()

        assert first < second or first[:10] == second[:10]

    def test_it_is_a_newtype_over_str(self) -> None:
        assert getattr(ClipId, "__supertype__") is str
