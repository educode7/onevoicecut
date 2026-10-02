"""The one authentication dependency FastAPI resolves, and the one 401 shape.

Why a dependency rather than a first statement in every handler: the route
table itself then carries the gate — a route declared without the dependency
is caught by the generated 401 check, not by review discipline — and the
refusal fires before body validation, so no handler and no schema can be
reached by a request that never authenticated.

Deny-by-default in two layers: `WebDependencies` cannot be constructed without
an authenticator, and this factory will not build a resolver without one
either — a wiring slip is a TypeError at composition, never a 500 on the first
request. Nothing here is a second credential channel: the resolver reads the
Authorization header and delegates to the injected static-map authenticator
only, which is why AUTH-11 can say the token map is the sole way in.

`HTTPBearer` rides along as a nested dependency for the document, not for the
decision. FastAPI writes `securitySchemes` only for a `SecurityBase` found in
the route's dependency tree, and a header read by hand is invisible to the
schema generator — so without it every operation in `/openapi.json` advertises
itself as callable with no credential at all, and a client generated from that
document ships without the token requirement.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from onevoicecut.shared.application.principal import (
    Authenticator,
    InvalidCredential,
    Principal,
)
from onevoicecut.shared.domain.ids import OperatorId

# `auto_error=False` is load bearing. With FastAPI's default, this object
# refuses a missing or unparsable header *before* the resolver runs, and its
# detail is "Not authenticated" — capital N — where this project's contract is
# one byte-identical body, "not authenticated", for every cause. Declining to
# let it refuse is what keeps that single 401 shape; it is still registered,
# which is all the schema generator needs from it.
_BEARER = HTTPBearer(auto_error=False)


def make_current_principal(
    authenticate: Authenticator,
) -> Callable[[Request], Principal]:
    """Build the dependency that resolves `Authorization` to a `Principal`.

    Returns the resolver to wrap as `Annotated[Principal, Depends(...)]` at
    the route: the annotation must be written where the route is declared (a
    function-call result cannot serve as a type annotation), so the factory's
    product is the function and the `Annotated` is composed beside it.

    Every credential failure — missing header, malformed header, unknown token
    — becomes the SAME response: one status, one body, one header.
    Distinguishing the causes would tell a caller which operators exist.
    """
    if not callable(authenticate):
        raise TypeError("make_current_principal requires a callable authenticator")

    def resolve_current_principal(
        request: Request,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Depends(_BEARER)
        ] = None,
    ) -> Principal:
        """Refuse, then resolve — with `credentials` deliberately unread.

        It exists so the route's dependency tree carries a `SecurityBase`,
        which is the only way `/openapi.json` can declare the scheme, and it
        is not consulted: AUTH-11 makes the injected map the sole authority
        over the raw header, so a parse that also decided would be a second
        credential check — two opinions about one request, free to disagree.
        """
        try:
            identity: OperatorId = authenticate(request.headers.get("authorization"))
        except InvalidCredential as error:
            raise HTTPException(
                status_code=401,
                detail="not authenticated",
                headers={"WWW-Authenticate": "Bearer"},
            ) from error
        return Principal(identity=identity, roles=frozenset())

    return resolve_current_principal
