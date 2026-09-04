"""Two render failures, and why they are not one.

`RenderFailed` is the engine's answer: ffmpeg ran and did not produce a usable
file. `ClipRangeInvalid` is the caller's — a range that cannot be cut from this
source at all, checked before a process is spawned.

Keeping them apart is what lets the render worker classify without reading a
message. A range error is the operator's to fix and will fail identically on
every retry; a render failure may be transient, and retrying it is reasonable.
Collapsing both into one type would make "retry or refuse" a decision nobody
downstream can take.
"""

import pytest

from onevoicecut.domain.errors import ClipRangeInvalid, DomainError, RenderFailed


@pytest.mark.parametrize("error", [RenderFailed, ClipRangeInvalid])
def test_it_crosses_the_port_as_a_domain_error(error: type[Exception]) -> None:
    """Every failure crossing a port is already a domain error, so a caller
    never has to catch a provider exception to survive."""
    assert issubclass(error, DomainError)


def test_they_are_distinct_types() -> None:
    """Not an alias and not a subclass of one another: the worker classifies on
    the type, and a range error will fail identically on every retry while a
    render failure may not."""
    assert not issubclass(RenderFailed, ClipRangeInvalid)
    assert not issubclass(ClipRangeInvalid, RenderFailed)


def test_each_carries_its_own_message() -> None:
    assert str(RenderFailed("ffmpeg exited 1")) == "ffmpeg exited 1"
    assert str(ClipRangeInvalid("ends past the source")) == "ends past the source"
