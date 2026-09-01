"""The one ownership rule, shared by every mutating path.

Upload, cancellation, and any future purge all gate on this — one rule, one
domain error, one HTTP mapping, so the policy is auditable in a single place.
Identity resolution stays in the web adapter; use cases receive the resolved
`OperatorId` as an argument and never see a token.
"""

from onevoicecut.domain.errors import JobNotOwned
from onevoicecut.domain.ids import OperatorId
from onevoicecut.domain.jobs import JobRecord


def require_owner(job: JobRecord, operator: OperatorId) -> None:
    """Raise `JobNotOwned` unless `operator` owns `job`.

    `owner=None` fails for every operator: a legacy job is readable by all and
    mutable by nobody, with no special case anywhere in the authorization code.
    """
    if job.owner != operator:
        raise JobNotOwned(f"job {job.job_id} is not owned by operator {operator!s}")
