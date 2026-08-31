"""Generating the ids, not just validating them.

Two properties carry weight here and neither is cosmetic. The id becomes a
directory name, so it must satisfy the same pattern the validator enforces on the
way back in — a generator that could emit something `make_job_id` rejects would
create jobs nobody can load. And `list_jobs` returns jobs in directory-name order
and calls that creation order, which is only true if these actually sort by time.
"""

import re

import pytest

from onevoicecut.domain.ids import _ULID_PATTERN, make_job_id, new_ulid

# 2026-08-31T12:00:00Z, and one millisecond later.
NOON_MS = 1788177600000
LATER_MS = NOON_MS + 1


def test_a_generated_id_satisfies_the_validator() -> None:
    """The generator and the validator must agree, or a job is unloadable the
    moment after it is created."""
    assert make_job_id(new_ulid(now_ms=NOON_MS, randomness=b"\x00" * 10))


def test_an_id_is_twenty_six_characters() -> None:
    assert len(new_ulid(now_ms=NOON_MS, randomness=b"\xff" * 10)) == 26


@pytest.mark.parametrize("byte", [0x00, 0x7F, 0xFF])
def test_every_randomness_extreme_still_matches_the_pattern(byte: int) -> None:
    assert _ULID_PATTERN.match(new_ulid(now_ms=NOON_MS, randomness=bytes([byte] * 10)))


def test_ids_sort_by_time() -> None:
    """`list_jobs` sorts directory names and calls the result creation order.
    That claim is only true because of this."""
    earlier = new_ulid(now_ms=NOON_MS, randomness=b"\xff" * 10)
    later = new_ulid(now_ms=LATER_MS, randomness=b"\x00" * 10)

    assert earlier < later


def test_ids_sort_by_time_across_a_wide_span() -> None:
    stamps = [NOON_MS - 86_400_000, NOON_MS, NOON_MS + 86_400_000]
    ids = [new_ulid(now_ms=ms, randomness=b"\x88" * 10) for ms in stamps]

    assert ids == sorted(ids)


def test_two_ids_in_the_same_millisecond_differ() -> None:
    """A collision would make one job overwrite another's directory."""
    first = new_ulid(now_ms=NOON_MS, randomness=b"\x01" * 10)
    second = new_ulid(now_ms=NOON_MS, randomness=b"\x02" * 10)

    assert first != second
    assert first[:10] == second[:10]  # same timestamp prefix


def test_the_alphabet_excludes_the_ambiguous_letters() -> None:
    """Crockford base32 drops I, L, O and U so an operator reading an id off a
    screen cannot turn it into a different one."""
    generated = "".join(
        new_ulid(now_ms=NOON_MS + i, randomness=bytes([i % 256] * 10))
        for i in range(200)
    )

    assert not re.search(r"[ILOU]", generated)


def test_randomness_of_the_wrong_length_is_refused() -> None:
    """80 bits exactly. Silently padding would make ids collide in ways the
    caller never asked for."""
    with pytest.raises(ValueError):
        new_ulid(now_ms=NOON_MS, randomness=b"\x00" * 9)


def test_a_timestamp_beyond_the_48_bit_field_is_refused() -> None:
    """The year 10889 problem. Truncating would silently break time ordering."""
    with pytest.raises(ValueError):
        new_ulid(now_ms=2**48, randomness=b"\x00" * 10)


def test_a_negative_timestamp_is_refused() -> None:
    with pytest.raises(ValueError):
        new_ulid(now_ms=-1, randomness=b"\x00" * 10)
