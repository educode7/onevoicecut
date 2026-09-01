"""Operator authentication for the web adapter: token map and authenticator.

Identity lives at the adapter boundary. The composition root parses the token
map once and injects the authenticator into `WebDependencies`; nothing below
the router ever sees a header or a token — use cases receive the resolved
`OperatorId` as an argument.

Token values never leave this module's comparison loop: they are not logged,
not persisted, and not carried in error messages.
"""

import hmac
from collections.abc import Callable, Mapping

from onevoicecut.domain.ids import InvalidIdError, OperatorId, make_operator_id


class InvalidCredential(Exception):
    """Raised whenever a request cannot be resolved to an operator.

    Deliberately one error for every cause — missing header, malformed header,
    unknown token. The web adapter translates all of them to the same 401, so a
    caller learns exactly one bit: authenticate. Distinguishing the causes would
    be an enumeration channel.
    """


class InvalidTokenMap(ValueError):
    """Raised when the configured operator token map cannot be used.

    The composition root refuses to boot on it: a server with an ambiguous or
    empty map would be a server that authenticates the wrong operator or nobody.
    Messages may name an operator or a pair position, never a token value.
    """


def parse_operator_tokens(raw: str | None) -> Mapping[OperatorId, str]:
    """Parse `name:token;name:token` into an operator-to-token map.

    Each pair splits at its FIRST colon, so tokens may contain colons; any
    character except the pair separator `;` is legal in a token. Surrounding
    whitespace is stripped per pair. Every malformed shape refuses, because a
    partial map at boot would silently change who can authenticate.
    """
    if raw is None or not raw.strip():
        raise InvalidTokenMap(
            "operator token map is empty: configure ONEVOICECUT_OPERATOR_TOKENS "
            "with at least one name:token pair"
        )

    mapping: dict[OperatorId, str] = {}
    seen_tokens: dict[str, int] = {}
    for position, raw_pair in enumerate(raw.split(";"), start=1):
        pair = raw_pair.strip()
        if not pair:
            raise InvalidTokenMap(f"operator token map pair {position} is empty")
        name_part, separator, token = pair.partition(":")
        if not separator:
            raise InvalidTokenMap(
                f"operator token map pair {position} has no ':' separator"
            )
        name = name_part.strip()
        token = token.strip()
        if not name:
            raise InvalidTokenMap(f"operator token map pair {position} has an empty name")
        try:
            operator = make_operator_id(name)
        except InvalidIdError as error:
            raise InvalidTokenMap(
                f"operator name {name!r} in token map pair {position} is not a "
                f"valid operator id"
            ) from error
        if not token:
            raise InvalidTokenMap(
                f"operator {name!r} in token map pair {position} has an empty token"
            )
        if operator in mapping:
            raise InvalidTokenMap(
                f"duplicate operator name {name!r} in token map pair {position}"
            )
        if token in seen_tokens:
            raise InvalidTokenMap(
                f"duplicate token in token map pair {position}: two operators "
                f"sharing one token makes identity ambiguous"
            )
        seen_tokens[token] = position
        mapping[operator] = token

    return mapping


def build_authenticator(
    token_map: Mapping[OperatorId, str],
) -> Callable[[str | None], OperatorId]:
    """Resolve a raw `Authorization` header to the operator it belongs to.

    The scheme is `Bearer`, case-insensitive; anything else is a credential
    failure. The scan compares the candidate against EVERY configured token
    with `hmac.compare_digest` and never exits early: an early exit would leak,
    by timing, how far the scan got. Exactly one match resolves (boot rejects
    duplicate tokens, so two matches cannot occur); zero matches fail with the
    same error as every other cause.
    """
    encoded = {operator: token.encode("utf-8") for operator, token in token_map.items()}

    def authenticate(raw: str | None) -> OperatorId:
        if raw is None:
            raise InvalidCredential("no authorization header")
        parts = raw.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            raise InvalidCredential("malformed authorization header")
        candidate = parts[1].encode("utf-8")

        resolved: OperatorId | None = None
        for operator, token in encoded.items():
            if hmac.compare_digest(candidate, token):
                resolved = operator
        if resolved is None:
            raise InvalidCredential("token matches no operator")
        return resolved

    return authenticate
