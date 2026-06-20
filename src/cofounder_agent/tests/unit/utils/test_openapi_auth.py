"""Unit tests for ``utils.openapi_auth.register_authed_openapi`` (poindexter#745).

In production ``main.py`` sets ``openapi_url=None``, which removes the
machine-readable catalog exactly where LLM/API consumers need it. The ADR
(`docs/architecture/2026-06-20-api-response-contracts.md`) requires the spec to
stay reachable — but **behind auth** rather than anonymously enumerable. This
helper registers an auth-gated ``GET /api/openapi.json`` in production and is a
no-op otherwise (FastAPI's built-in public route handles non-prod).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from utils.openapi_auth import register_authed_openapi

pytestmark = pytest.mark.unit


def _find_openapi_route(app: FastAPI) -> APIRoute | None:
    for route in app.routes:
        if isinstance(route, APIRoute) and route.path == "/api/openapi.json":
            return route
    return None


def _route_requires_auth(route: APIRoute) -> bool:
    """True if ``verify_api_token`` is anywhere in the route's dependency tree
    (param/route/router level), matching test_operator_routers_require_auth."""
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return False
    seen: set[int] = set()
    stack = [dependant]
    while stack:
        dep = stack.pop()
        if id(dep) in seen:
            continue
        seen.add(id(dep))
        if getattr(dep, "call", None) is verify_api_token:
            return True
        stack.extend(getattr(dep, "dependencies", ()))
    return False


def test_production_registers_authed_openapi_route() -> None:
    """In production the helper adds GET /api/openapi.json behind verify_api_token."""
    app = FastAPI(openapi_url=None)
    register_authed_openapi(app, is_production=True)

    route = _find_openapi_route(app)
    assert route is not None, "no /api/openapi.json route registered in production"
    assert "GET" in (route.methods or set())
    assert _route_requires_auth(route), (
        "/api/openapi.json must have verify_api_token in its dependency tree — "
        "the production spec must not be anonymously enumerable (#745)."
    )


def test_non_production_registers_nothing() -> None:
    """Outside production FastAPI's built-in public openapi route is used, so the
    helper must NOT add a second /api/openapi.json route."""
    app = FastAPI(openapi_url="/api/openapi.json")
    register_authed_openapi(app, is_production=False)

    # The only routes touching the path are FastAPI's own; the helper adds none.
    custom = [
        r
        for r in app.routes
        if isinstance(r, APIRoute)
        and r.path == "/api/openapi.json"
        and r.name == "authed_openapi"
    ]
    assert custom == [], "helper added a custom route outside production"


def test_authed_openapi_returns_spec_when_authorized() -> None:
    """With auth satisfied, the route returns the real OpenAPI document."""
    app = FastAPI(openapi_url=None, title="Probe API")

    @app.get("/api/things")
    async def _things() -> dict[str, str]:
        return {"ok": "yes"}

    register_authed_openapi(app, is_production=True)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"

    client = TestClient(app)
    resp = client.get("/api/openapi.json")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("openapi"), "response is not an OpenAPI document"
    assert body["info"]["title"] == "Probe API"
    # The generated spec reflects the app's real routes.
    assert "/api/things" in body["paths"]


def test_authed_openapi_rejects_without_auth() -> None:
    """Without a token the route is refused (401), not served anonymously."""
    app = FastAPI(openapi_url=None)
    register_authed_openapi(app, is_production=True)

    client = TestClient(app)
    resp = client.get("/api/openapi.json")
    assert resp.status_code == 401, (
        f"expected 401 without auth, got {resp.status_code} — the production "
        "spec must require a token."
    )
