"""The OpenAPI document declares the bearer scheme every route enforces.

A client generated from `/openapi.json` has to learn that
`Authorization: Bearer <token>` is required without reading this repository's
source, and the only way FastAPI ever writes `components.securitySchemes` is
when a `SecurityBase` sits somewhere in the route's dependency tree. Reading
the document rather than the module is the point: the schema is what an
outside consumer actually sees, so a resolver that reads the header by hand
and registers no scheme shows up here as an undeclared credential — the
security is real, the contract is silent.

Every operation carries it too — all eight routes take a `CurrentPrincipal`,
so an operation declaring no `security` is one that forgot the gate, which is
the same failure the generated 401 check catches from the request side.
"""

from pathlib import Path
from typing import Any

from onevoicecut.main import WebDependencies, create_app
from tests.fakes.transcript_storage import FakeTranscriptStoragePort
from tests.unit.adapters.web.conftest import (
    accepting_extractor,
    fake_authenticate,
)

HTTP_METHODS = ("get", "post", "put", "patch", "delete")


def _spec(tmp_path: Path) -> dict[str, Any]:
    """The document the app actually serves, not a model of it."""
    app = create_app(
        WebDependencies(
            storage=FakeTranscriptStoragePort(tmp_path),
            authenticate=fake_authenticate,
            extractor_for=accepting_extractor,
        )
    )
    return app.openapi()


def test_the_document_declares_a_bearer_scheme(tmp_path: Path) -> None:
    """One `http`/`bearer` scheme, present by name in `components`."""
    schemes = _spec(tmp_path).get("components", {}).get("securitySchemes")
    assert schemes, "the document declares no security scheme at all"
    assert {"type": "http", "scheme": "bearer"} in schemes.values()


def test_every_operation_declares_the_scheme_it_is_enforced_with(
    tmp_path: Path,
) -> None:
    """No operation may advertise itself as callable without a credential."""
    spec = _spec(tmp_path)
    names = set(spec.get("components", {}).get("securitySchemes", {}))
    operations = [
        operation
        for path_item in spec["paths"].values()
        for method, operation in path_item.items()
        if method in HTTP_METHODS
    ]
    assert operations, "the document lists no operation"
    for operation in operations:
        declared = {
            name for entry in operation.get("security", ()) for name in entry
        }
        assert declared & names, (
            f"{operation.get('operationId')} declares no security scheme"
        )
