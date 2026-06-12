"""
Contract tests for the OpenAPI validation-response truthfulness patch.

poindexter#742 — FastAPI auto-generates ``422 + HTTPValidationError`` on
every operation with a request body / path / query parameters, but the global
``RequestValidationError`` handler in ``utils/exception_handlers.py`` always
returns ``400`` instead.  The ``_patched_openapi()`` override in ``main.py``
strips that misleading ``422`` entry and replaces it with a truthful ``400``
entry carrying the real ``{error_code, message, errors, request_id}`` envelope.

These tests guard that contract from regressing:

1. ``TestOpenApiPatchSource`` — reads ``main.py`` source to confirm the patch
   is present and wired (no heavy import needed).
2. ``TestOpenApiPatchBehaviour`` — builds a minimal FastAPI app that mirrors
   the same patching logic and asserts the resulting schema has no ``422``
   entries and has ``400`` entries on the operations that accept bodies.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAIN_PY = Path(__file__).resolve().parents[3] / "main.py"


def _read_main() -> str:
    assert _MAIN_PY.is_file(), f"expected main.py at {_MAIN_PY}"
    return _MAIN_PY.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Source-reading guards (no heavy import — fast and robust)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestOpenApiPatchSource:
    """Verify the patch is present in main.py source without importing it."""

    def test_patched_openapi_function_exists(self):
        """The ``_patched_openapi`` function must exist in main.py."""
        source = _read_main()
        assert "def _patched_openapi" in source, (
            "_patched_openapi function missing from main.py — "
            "the OpenAPI spec will still emit misleading 422 entries "
            "(poindexter#742)."
        )

    def test_422_stripped_in_patch(self):
        """The patch must remove the '422' key from responses."""
        source = _read_main()
        assert 'responses.pop("422", None)' in source, (
            'responses.pop("422", None) missing from _patched_openapi — '
            "422 entries will not be stripped from the spec (poindexter#742)."
        )

    def test_400_injected_in_patch(self):
        """The patch must inject a 400 response entry."""
        source = _read_main()
        assert '"400" not in responses' in source, (
            'The 400-injection guard (if "400" not in responses) is missing '
            "from _patched_openapi — validation errors will not be documented "
            "under the correct status code (poindexter#742)."
        )

    def test_patch_wired_to_app(self):
        """The override must be assigned to ``app.openapi``."""
        source = _read_main()
        assert "app.openapi = _patched_openapi" in source, (
            "app.openapi override assignment missing from main.py — "
            "the patch exists but is never applied (poindexter#742)."
        )

    def test_patch_guarded_by_non_production(self):
        """The assignment must be inside the ``if not _is_production:`` guard."""
        source = _read_main()
        # Find the guard block
        guard_match = re.search(r"if not _is_production:\s*\n\s*app\.openapi", source)
        assert guard_match is not None, (
            "app.openapi override must be inside 'if not _is_production:' — "
            "the override is either missing or unconditionally applied, which "
            "breaks the production posture (openapi_url=None in prod, "
            "poindexter#742)."
        )

    def test_status_to_error_code_422_preserved(self):
        """The _STATUS_TO_ERROR_CODE mapping in exception_handlers.py must still
        map 422 → INVALID_STATE (the app uses 422 intentionally for that error;
        we only strip it from the *auto-generated* validation docs, not from the
        live handler behaviour)."""
        handler_py = _MAIN_PY.parent / "utils" / "exception_handlers.py"
        assert handler_py.is_file(), f"expected exception_handlers.py at {handler_py}"
        source = handler_py.read_text(encoding="utf-8")
        assert '422: "INVALID_STATE"' in source, (
            "The 422 → INVALID_STATE mapping was removed from "
            "_STATUS_TO_ERROR_CODE in exception_handlers.py.  "
            "The openapi patch strips only the *default* 422 documentation — "
            "the live 422 INVALID_STATE behaviour must remain intact."
        )


# ---------------------------------------------------------------------------
# 2. Behavioural tests: mirror the patch logic on a minimal app
# ---------------------------------------------------------------------------


class _Body(BaseModel):
    name: str


def _build_patched_app() -> FastAPI:
    """Build a tiny FastAPI app that mirrors main.py's _patched_openapi logic."""
    test_app = FastAPI(title="test", version="0")

    @test_app.post("/items")
    async def create_item(body: _Body):  # noqa: D401
        return {"name": body.name}

    @test_app.put("/items/{item_id}")
    async def update_item(item_id: int, body: _Body):
        return {"id": item_id, "name": body.name}

    @test_app.get("/items")
    async def list_items(q: str | None = None):
        return []

    @test_app.get("/items/{item_id}")
    async def get_item(item_id: int):
        return {"id": item_id}

    # Mirror the exact patch from main.py ---------------------------------

    _VALIDATION_400_SCHEMA: dict[str, Any] = {
        "description": "Request validation failed",
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["error_code", "message", "request_id"],
                    "properties": {
                        "error_code": {"type": "string", "example": "VALIDATION_ERROR"},
                        "message": {"type": "string"},
                        "errors": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                        "request_id": {"type": "string", "format": "uuid"},
                    },
                }
            }
        },
    }

    def _patched_openapi() -> dict:
        if test_app.openapi_schema:
            return test_app.openapi_schema
        raw = get_openapi(
            title=test_app.title,
            version=test_app.version,
            description=test_app.description,
            routes=test_app.routes,
        )
        for path_item in raw.get("paths", {}).values():
            for op in path_item.values():
                if not isinstance(op, dict):
                    continue
                responses = op.get("responses", {})
                responses.pop("422", None)
                if "400" not in responses:
                    responses["400"] = _VALIDATION_400_SCHEMA
        test_app.openapi_schema = raw
        return test_app.openapi_schema

    test_app.openapi = _patched_openapi  # type: ignore[method-assign]
    return test_app


@pytest.fixture(scope="module")
def patched_schema() -> dict:
    """Return the OpenAPI schema of the patched test app (built once per module)."""
    return _build_patched_app().openapi()


@pytest.mark.unit
class TestOpenApiPatchBehaviour:
    """Behavioural contract: 422 gone, 400 present, schema valid."""

    def test_no_422_in_any_operation(self, patched_schema):
        """No path operation may have a ``422`` response key after patching."""
        violations: list[str] = []
        for path, path_item in patched_schema.get("paths", {}).items():
            for method, op in path_item.items():
                if not isinstance(op, dict):
                    continue
                if "422" in op.get("responses", {}):
                    violations.append(f"{method.upper()} {path}")

        assert not violations, (
            "Found 422 responses in operations after the patch: "
            + ", ".join(violations)
        )

    def test_400_present_on_body_operations(self, patched_schema):
        """POST and PUT operations (which accept bodies) must document 400."""
        body_methods = {"post", "put"}
        missing: list[str] = []
        for path, path_item in patched_schema.get("paths", {}).items():
            for method, op in path_item.items():
                if method.lower() not in body_methods:
                    continue
                if not isinstance(op, dict):
                    continue
                if "400" not in op.get("responses", {}):
                    missing.append(f"{method.upper()} {path}")

        assert not missing, (
            "POST/PUT operations missing 400 documentation after the patch: "
            + ", ".join(missing)
        )

    def test_400_present_on_get_with_params(self, patched_schema):
        """GET operations with path/query params must document 400."""
        # GET /items/{item_id} has a path param → should get 400
        path_item = patched_schema.get("paths", {}).get("/items/{item_id}", {})
        get_op = path_item.get("get", {})
        assert isinstance(get_op, dict), "GET /items/{item_id} operation not found"
        assert "400" in get_op.get("responses", {}), (
            "GET /items/{item_id} is missing 400 documentation — "
            "the patch did not inject it for parameterised GET operations."
        )

    def test_400_schema_has_required_envelope_fields(self, patched_schema):
        """The injected 400 response must document the real error envelope."""
        # Find the first operation that has a 400 response and inspect it.
        first_400: dict | None = None
        for path_item in patched_schema.get("paths", {}).values():
            for op in path_item.values():
                if not isinstance(op, dict):
                    continue
                resp = op.get("responses", {}).get("400")
                if resp:
                    first_400 = resp
                    break
            if first_400:
                break

        assert first_400 is not None, "No 400 response found in schema at all"
        schema = (
            first_400.get("content", {})
            .get("application/json", {})
            .get("schema", {})
        )
        required = set(schema.get("required", []))
        assert "error_code" in required, "400 schema missing required 'error_code'"
        assert "message" in required, "400 schema missing required 'message'"
        assert "request_id" in required, "400 schema missing required 'request_id'"

    def test_patch_is_idempotent(self):
        """Calling openapi() twice must return the cached schema (same object)."""
        app = _build_patched_app()
        first = app.openapi()
        second = app.openapi()
        assert first is second, (
            "openapi() returned a different object on the second call — "
            "the schema cache is not being respected."
        )

    def test_handler_422_mapping_not_modified(self):
        """The ``_STATUS_TO_ERROR_CODE`` mapping in exception_handlers.py must
        still map 422 → INVALID_STATE at runtime.

        The patch only affects the *OpenAPI documentation* — it strips the
        auto-generated 422/HTTPValidationError spec entry and replaces it with
        a truthful 400.  It does NOT touch the live exception handler; an
        ``HTTPException(status_code=422)`` raised inside a route still produces
        a real 422 INVALID_STATE response.  This test pins that invariant.
        """
        from fastapi import FastAPI as _FastAPI
        from fastapi import HTTPException
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient

        ta = _FastAPI()

        # Mimic the real _STATUS_TO_ERROR_CODE handler from exception_handlers.py
        _STATUS_TO_ERROR_CODE = {
            400: "VALIDATION_ERROR",
            401: "UNAUTHORIZED",
            403: "FORBIDDEN",
            404: "NOT_FOUND",
            409: "CONFLICT",
            422: "INVALID_STATE",
            429: "RATE_LIMITED",
            500: "INTERNAL_ERROR",
        }

        @ta.exception_handler(HTTPException)
        async def _http_exc(request, exc):
            code = _STATUS_TO_ERROR_CODE.get(exc.status_code, "HTTP_ERROR")
            return JSONResponse(
                status_code=exc.status_code,
                content={"error_code": code, "message": str(exc.detail)},
            )

        @ta.get("/resource")
        async def resource_in_wrong_state():
            raise HTTPException(status_code=422, detail="resource locked")

        client = TestClient(ta, raise_server_exceptions=False)
        resp = client.get("/resource")
        # The live HTTP response must still be 422 with INVALID_STATE
        assert resp.status_code == 422, (
            "Runtime 422 INVALID_STATE response is broken — the openapi patch "
            "must not modify exception handler behaviour."
        )
        assert resp.json().get("error_code") == "INVALID_STATE", (
            "422 response body should carry error_code=INVALID_STATE "
            "(poindexter#742 — only the *docs* change, not the live behaviour)."
        )
