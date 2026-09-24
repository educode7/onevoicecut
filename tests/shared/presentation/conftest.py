"""Request builders for the `CurrentPrincipal` tests.

The resolver reads the Authorization header and nothing else, so a bare ASGI
scope with headers is exactly the input it faces — no transport, no app, no
route. A helper here rather than in each file because AUTH-10 and AUTH-11 both
resolve the same dependency against crafted headers.
"""

from fastapi import Request
from starlette.types import Scope


def request_with_headers(*headers: tuple[bytes, bytes]) -> Request:
    """A request carrying only the given headers.

    Built directly from a scope because that is the entire contract the
    resolver honours: anything not in `headers` cannot influence it, which is
    half of AUTH-11's "only the static map authenticates" claim.
    """
    scope: Scope = {
        "type": "http",
        "headers": list(headers),
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
    }
    return Request(scope)
