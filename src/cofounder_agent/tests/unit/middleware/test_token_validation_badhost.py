"""
Regression test for CVE-2026-48710 ("BadHost" — Starlette host-header
auth bypass).

The vulnerability: Starlette < 1.0.1 reconstructs ``request.url`` from the
raw HTTP ``Host`` header without validating it against RFC 9112 §3.2 /
RFC 3986 §3.2.2. A crafted ``Host: target/public-prefix`` makes
``request.url.path`` start with ``/public-prefix`` while the ASGI router
still dispatches the original path. Middleware that gates auth on
``request.url.path`` (vs. the raw ``scope["path"]``) can be bypassed.

Our fix (token_validation.py) reads ``request.scope["path"]`` — the raw
ASGI path the server routed against — for every auth-bypass decision.
This test asserts the fix holds end-to-end through a real ASGI request.
"""

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.token_validation import TokenValidationMiddleware

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_app() -> FastAPI:
    """Minimal FastAPI app with TokenValidationMiddleware installed.

    Mounts a protected ``/api/tasks/{task_id}`` route and a public
    ``/health`` route — the same shape as production.
    """
    app = FastAPI()

    # DI seam (glad-labs-stack#330): middleware reads site_config off
    # app.state. Tests just need a stand-in that returns ``""`` for every
    # key so the dev-bypass branch never triggers.
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": default
    app.state.site_config = sc

    app.add_middleware(TokenValidationMiddleware)

    @app.get("/api/tasks/{task_id}")
    async def protected_handler(task_id: str):
        # If the middleware bypass is fooled, this 200 leaks through.
        return {"task_id": task_id, "reached_protected_handler": True}

    @app.get("/health")
    async def health_handler():
        return {"status": "ok"}

    return app


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestBadHostAuthBypass:
    """CVE-2026-48710 regression coverage."""

    def test_badhost_health_prefix_does_not_bypass_protected_route(self):
        """Host header injecting a public prefix must NOT bypass auth.

        Exploit shape:
        - ASGI router dispatches ``GET /api/tasks/123`` (the actual path)
        - request.url.path on Starlette < 1.0.1 reconstructs to
          ``/health/api/tasks/123`` because the Host header carries
          ``target.com/health`` — startswith("/health") would be True.
        - Middleware reading scope["path"] sees the raw ``/api/tasks/123``
          and correctly demands a Bearer token.
        """
        client = TestClient(_build_app())
        response = client.get(
            "/api/tasks/123",
            headers={"Host": "target.com/health"},
        )
        assert response.status_code == 401, (
            f"BadHost bypass succeeded — got {response.status_code} "
            f"{response.text!r}. Middleware must gate on scope['path'], "
            f"not request.url.path."
        )
        assert "reached_protected_handler" not in response.text

    def test_badhost_api_public_prefix_does_not_bypass_protected_route(self):
        """Same exploit with ``/api/public`` as the injected prefix."""
        client = TestClient(_build_app())
        response = client.get(
            "/api/tasks/123",
            headers={"Host": "target.com/api/public"},
        )
        assert response.status_code == 401, (
            f"BadHost bypass via /api/public prefix succeeded — got "
            f"{response.status_code} {response.text!r}."
        )
        assert "reached_protected_handler" not in response.text

    def test_badhost_docs_prefix_does_not_bypass_protected_route(self):
        """``/docs`` is a public prefix — verify injection via Host fails."""
        client = TestClient(_build_app())
        response = client.get(
            "/api/tasks/123",
            headers={"Host": "target.com/docs"},
        )
        assert response.status_code == 401, (
            f"BadHost bypass via /docs prefix succeeded — got "
            f"{response.status_code} {response.text!r}."
        )

    def test_clean_health_request_still_200s(self):
        """Negative regression: legitimate public-path traffic still works.

        The fix must not break the actual public-path bypass — a normal
        ``GET /health`` with a clean Host header should still reach the
        ``/health`` handler without auth.
        """
        client = TestClient(_build_app())
        response = client.get("/health", headers={"Host": "target.com"})
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_clean_protected_request_with_token_still_200s(self):
        """Negative regression: a properly authed request still works."""
        client = TestClient(_build_app())
        response = client.get(
            "/api/tasks/123",
            headers={
                "Host": "target.com",
                "Authorization": "Bearer fake-but-well-formed-token",
            },
        )
        # 200 = middleware lets the request through (full JWT validation
        # happens downstream in get_current_user, which is not wired into
        # this minimal test app).
        assert response.status_code == 200
        assert response.json()["reached_protected_handler"] is True

    def test_clean_protected_request_without_token_returns_401(self):
        """Negative regression: protected route still requires a token."""
        client = TestClient(_build_app())
        response = client.get("/api/tasks/123", headers={"Host": "target.com"})
        assert response.status_code == 401
