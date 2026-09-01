"""The operator token map, from configuration text to authenticated identity.

One env var, read once at the composition root: `name:token;name:token`. The
parser is pure and refuses every malformed form, because a map that parsed
ambiguously would authenticate the wrong operator — and the authenticator it
feeds compares in constant time over the whole map, so a request never reveals
how close a wrong token came.
"""

import pytest

from onevoicecut.adapters.web.auth import (
    InvalidCredential,
    InvalidTokenMap,
    build_authenticator,
    parse_operator_tokens,
)
from onevoicecut.domain.ids import make_operator_id

TWO_OPERATORS = {
    make_operator_id("maria"): "tok-maria",
    make_operator_id("jose"): "tok-jose",
}


def test_a_well_formed_map_becomes_an_operator_to_token_mapping() -> None:
    mapping = parse_operator_tokens("maria:tok;jose:tok2")

    assert mapping == {
        make_operator_id("maria"): "tok",
        make_operator_id("jose"): "tok2",
    }


def test_surrounding_whitespace_per_pair_is_stripped() -> None:
    mapping = parse_operator_tokens("  maria : tok ; jose:tok2  ")

    assert mapping == {
        make_operator_id("maria"): "tok",
        make_operator_id("jose"): "tok2",
    }


def test_a_pair_splits_at_its_first_colon_so_tokens_may_contain_colons() -> None:
    mapping = parse_operator_tokens("maria:tok:with:colons")

    assert mapping == {make_operator_id("maria"): "tok:with:colons"}


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(None, id="absent"),
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
    ],
)
def test_zero_operators_refuses_to_parse(raw: str | None) -> None:
    """AUTH-07 at parse level: an empty map means nobody can authenticate, and a
    server like that must never come up."""
    with pytest.raises(InvalidTokenMap):
        parse_operator_tokens(raw)


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("maria", id="pair-with-no-colon"),
        pytest.param("maria:tok;jose", id="second-pair-with-no-colon"),
        pytest.param(":tok", id="empty-name"),
        pytest.param("Maria:tok", id="name-with-uppercase"),
        pytest.param("mar ia:tok", id="name-with-space"),
        pytest.param("maria:", id="empty-token"),
        pytest.param("maria:t1;maria:t2", id="duplicate-name"),
        pytest.param("maria:same;jose:same", id="duplicate-token"),
    ],
)
def test_every_malformed_form_refuses_to_parse(raw: str) -> None:
    """AUTH-08 at parse level: each malformed shape fails closed with its own
    error rather than producing an ambiguous or partial map."""
    with pytest.raises(InvalidTokenMap):
        parse_operator_tokens(raw)


def test_refusal_messages_never_contain_a_token_value() -> None:
    """AUTH-09 boot half: the secret discipline starts at configuration time —
    an error about the map may name an operator or a position, never a token."""
    cases = [
        "maria:sekrit-value;jose",
        "maria:sekrit-value;maria:other",
        "maria:sekrit-value;jose:sekrit-value",
        "Maria:sekrit-value",
    ]

    for raw in cases:
        with pytest.raises(InvalidTokenMap) as excinfo:
            parse_operator_tokens(raw)
        assert "sekrit-value" not in str(excinfo.value)


def test_a_duplicate_token_refusal_names_the_ambiguity_not_the_token() -> None:
    with pytest.raises(InvalidTokenMap) as excinfo:
        parse_operator_tokens("maria:same;jose:same")

    assert "same" not in str(excinfo.value)


def test_a_configured_token_resolves_its_operator() -> None:
    """AUTH-01: the token is the only thing that establishes identity."""
    authenticate = build_authenticator(TWO_OPERATORS)

    assert authenticate("Bearer tok-maria") == make_operator_id("maria")
    assert authenticate("Bearer tok-jose") == make_operator_id("jose")


def test_a_token_matching_no_operator_is_a_credential_failure() -> None:
    """AUTH-03: a well-formed credential for nobody is still a failure."""
    authenticate = build_authenticator(TWO_OPERATORS)

    with pytest.raises(InvalidCredential):
        authenticate("Bearer tok-nobody")


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(None, id="missing-header"),
        pytest.param("", id="empty-header"),
        pytest.param("Basic dXNlcjpwYXNz", id="wrong-scheme"),
        pytest.param("Bearer", id="bare-scheme"),
        pytest.param("Bearer ", id="scheme-no-token"),
        pytest.param("not-a-scheme-at-all", id="unparsable"),
    ],
)
def test_every_unresolvable_header_fails_the_same_way(raw: str | None) -> None:
    """AUTH-04: missing, malformed, and unparsable are one failure. The caller
    learns a single bit — authenticate — and nothing that distinguishes forms."""
    authenticate = build_authenticator(TWO_OPERATORS)

    with pytest.raises(InvalidCredential):
        authenticate(raw)


@pytest.mark.parametrize("scheme", ["bearer", "BEARER", "bEaReR"])
def test_the_scheme_is_case_insensitive(scheme: str) -> None:
    authenticate = build_authenticator(TWO_OPERATORS)

    assert authenticate(f"{scheme} tok-maria") == make_operator_id("maria")


def test_the_scan_is_full_with_no_early_exit() -> None:
    """A token equal to a LATER pair's token resolves even when an earlier pair
    shares its prefix — the scan compares every pair exactly, never exits early,
    and an `startswith` shortcut would misresolve this."""
    authenticate = build_authenticator(
        {
            make_operator_id("maria"): "tok",
            make_operator_id("jose"): "tok-jose",
        }
    )

    assert authenticate("Bearer tok-jose") == make_operator_id("jose")


def test_wrong_token_for_an_operator_is_indistinguishable_from_unknown() -> None:
    """AUTH-05 authenticator half: a structurally valid token for an existing
    operator and a token matching nobody are the SAME failure — no response may
    reveal which operators are configured."""
    authenticate = build_authenticator(TWO_OPERATORS)

    with pytest.raises(InvalidCredential) as wrong_for_operator:
        authenticate("Bearer tok-maria-but-wrong")
    with pytest.raises(InvalidCredential) as matching_none:
        authenticate("Bearer tok-nobody")

    assert type(wrong_for_operator.value) is type(matching_none.value)
