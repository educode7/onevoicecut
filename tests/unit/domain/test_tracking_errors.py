"""Two failures a tracker can have, and why they are not one.

Both derive from `DomainError`, so every caller already catching domain errors
survives them without knowing they exist — the rule that keeps a vision library's
own exception types out of the use-case layer.

They are separate because the operator's next move differs, which is the same
argument `DetectionSupport` makes with three members instead of two.
`TrackingUnavailable` says *this build cannot track* — install the extras, or
render without a reframe. `DetectionFailed` says *this build can track and this
clip did not work* — retry it, or look at the source. Collapsing them would hand
an operator one message for a setup problem and a bad file alike.
"""

from onevoicecut.domain.errors import DetectionFailed, DomainError, TrackingUnavailable


def test_tracking_unavailable_is_a_domain_error() -> None:
    assert issubclass(TrackingUnavailable, DomainError)


def test_detection_failed_is_a_domain_error() -> None:
    assert issubclass(DetectionFailed, DomainError)


def test_they_are_distinct_types() -> None:
    """Neither derives from the other, so `except DetectionFailed` cannot
    swallow a build that can never track — a caller retrying a failed clip must
    not accidentally retry an install problem forever.

    mypy already rejects `TrackingUnavailable is not DetectionFailed` as a
    non-overlapping comparison, which is a stronger guarantee than a runtime
    assertion could be; what it cannot see is the subclass relation, so that is
    what is asserted here.
    """
    assert not issubclass(TrackingUnavailable, DetectionFailed)
    assert not issubclass(DetectionFailed, TrackingUnavailable)


def test_each_carries_its_own_message() -> None:
    assert str(TrackingUnavailable("no vision extras")) == "no vision extras"
    assert str(DetectionFailed("weights unreadable")) == "weights unreadable"
