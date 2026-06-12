"""Unit tests for /api/health status-code contracts and /api/metrics auth contract.

Covers poindexter#749:
  1. /api/health must return HTTP 503 when status is "unhealthy" or "degraded",
     HTTP 200 when "healthy" or "starting".
  2. /api/metrics must require auth (401 without token) and return 503 when DB
     is unavailable or raises.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Minimal stubs mirroring the real handler implementations
# ---------------------------------------------------------------------------

def _build_health_app() -> FastAPI:
    """Build a minimal FastAPI app that mirrors the /api/health return logic."""
    app = FastAPI()

    @app.get("/api/health")
    async def api_health(degraded: bool = False, starting: bool = False, unhealthy: bool = False):
        """Mirror of the real handler — accepts query params to force a status."""
        health_data: dict = {
            "status": "healthy",
            "service": "poindexter",
        }
        if degraded:
            health_data["status"] = "degraded"
        elif unhealthy:
            health_data["status"] = "unhealthy"
        elif starting:
            health_data["status"] = "starting"

        status_code = (
            503
            if health_data.get("status") in ("unhealthy", "degraded")
            else 200
        )
        return JSONResponse(content=health_data, status_code=status_code)

    return app


def _verify_token(token: str = ""):
    """Minimal token verifier: empty token raises 401."""
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return token


def _build_metrics_app(db_available: bool = True, db_raises: bool = False) -> FastAPI:
    """Build a minimal FastAPI app that mirrors the /api/metrics handler logic."""
    app = FastAPI()

    class FakeDB:
        async def get_metrics(self):
            if db_raises:
                raise RuntimeError("DB boom")
            return {"total_tasks": 5}

    @app.get("/api/metrics")
    async def get_metrics(token: str = Depends(_verify_token)):
        database_service = FakeDB() if db_available else None
        if not database_service:
            raise HTTPException(status_code=503, detail="metrics_unavailable")
        try:
            return await database_service.get_metrics()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=503, detail="metrics_unavailable") from e

    return app


# ---------------------------------------------------------------------------
# /api/health status-code tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestHealthStatusCodes:
    """poindexter#749 — /api/health must return 503 for degraded/unhealthy."""

    def test_healthy_returns_200(self):
        client = TestClient(_build_health_app())
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"

    def test_starting_returns_200(self):
        client = TestClient(_build_health_app())
        resp = client.get("/api/health?starting=true")
        assert resp.status_code == 200
        assert resp.json()["status"] == "starting"

    def test_degraded_returns_503(self):
        client = TestClient(_build_health_app())
        resp = client.get("/api/health?degraded=true")
        assert resp.status_code == 503
        assert resp.json()["status"] == "degraded"

    def test_unhealthy_returns_503(self):
        client = TestClient(_build_health_app())
        resp = client.get("/api/health?unhealthy=true")
        assert resp.status_code == 503
        assert resp.json()["status"] == "unhealthy"


@pytest.mark.unit
class TestHealthSourceContractGuards:
    """Source-level guards that fail loudly if main.py drifts from the contract."""

    def _health_handler_body(self) -> str:
        import re
        from pathlib import Path

        main_py = Path(__file__).resolve().parent.parent.parent / "main.py"
        assert main_py.is_file(), f"expected main.py at {main_py}"
        source = main_py.read_text(encoding="utf-8")

        start = source.index("async def api_health(")
        rest = source[start + 1:]
        m = re.search(r"\n@app\.|\nasync def ", rest)
        end = (start + 1 + m.start()) if m else len(source)
        return source[start:end]

    def test_health_uses_json_response(self):
        body = self._health_handler_body()
        assert "JSONResponse" in body, (
            "/api/health must return a JSONResponse so status_code is honoured "
            "(poindexter#749)."
        )

    def test_health_returns_503_for_degraded_or_unhealthy(self):
        body = self._health_handler_body()
        assert "503" in body, (
            "/api/health must return HTTP 503 when status is degraded/unhealthy "
            "(poindexter#749)."
        )

    def test_health_outer_except_returns_503(self):
        body = self._health_handler_body()
        # The outer except block must also use JSONResponse with 503
        assert body.count("503") >= 2, (
            "Both the normal return and the outer except block in /api/health "
            "must return 503 for error states (poindexter#749)."
        )

    def test_health_outer_except_uses_json_response(self):
        body = self._health_handler_body()
        assert body.count("JSONResponse") >= 2, (
            "Both return sites in /api/health must use JSONResponse so the "
            "status code is propagated correctly (poindexter#749)."
        )


# ---------------------------------------------------------------------------
# /api/metrics auth + DB unavailable tests
# ---------------------------------------------------------------------------

@pytest.mark.unit
class TestMetricsAuth:
    """poindexter#749 — /api/metrics must require authentication."""

    def test_no_token_returns_401(self):
        client = TestClient(_build_metrics_app(db_available=True), raise_server_exceptions=False)
        resp = client.get("/api/metrics")
        assert resp.status_code == 401

    def test_valid_token_returns_200(self):
        client = TestClient(_build_metrics_app(db_available=True))
        resp = client.get("/api/metrics", params={"token": "secret"})
        assert resp.status_code == 200
        assert resp.json()["total_tasks"] == 5


@pytest.mark.unit
class TestMetricsDBUnavailable:
    """poindexter#749 — /api/metrics must return 503 when DB is down, not zeroed data."""

    def test_db_unavailable_returns_503(self):
        client = TestClient(_build_metrics_app(db_available=False), raise_server_exceptions=False)
        resp = client.get("/api/metrics", params={"token": "secret"})
        assert resp.status_code == 503
        assert resp.json()["detail"] == "metrics_unavailable"

    def test_db_raises_returns_503(self):
        client = TestClient(
            _build_metrics_app(db_available=True, db_raises=True),
            raise_server_exceptions=False,
        )
        resp = client.get("/api/metrics", params={"token": "secret"})
        assert resp.status_code == 503
        assert resp.json()["detail"] == "metrics_unavailable"


@pytest.mark.unit
class TestMetricsSourceContractGuards:
    """Source-level guards that fail loudly if main.py drifts from the contract."""

    def _metrics_handler_body(self) -> str:
        import re
        from pathlib import Path

        main_py = Path(__file__).resolve().parent.parent.parent / "main.py"
        assert main_py.is_file(), f"expected main.py at {main_py}"
        source = main_py.read_text(encoding="utf-8")

        start = source.index("async def get_metrics_endpoint(")
        rest = source[start + 1:]
        m = re.search(r"\n@app\.|\nasync def ", rest)
        end = (start + 1 + m.start()) if m else len(source)
        return source[start:end]

    def test_metrics_has_verify_api_token(self):
        body = self._metrics_handler_body()
        assert "verify_api_token" in body, (
            "/api/metrics must use Depends(verify_api_token) for auth "
            "(poindexter#749)."
        )

    def test_metrics_raises_503_not_returns_zeros(self):
        body = self._metrics_handler_body()
        assert "503" in body, (
            "/api/metrics must raise HTTPException(503) on DB failure, not "
            "return zeroed data (poindexter#749)."
        )
        # Must NOT contain the old zeroed-data fallback dict
        assert '"total_tasks": 0' not in body and "total_tasks: 0" not in body, (
            "/api/metrics must not return a zeroed dict on DB failure "
            "(poindexter#749)."
        )

    def test_metrics_reraises_http_exception(self):
        body = self._metrics_handler_body()
        assert "except HTTPException:" in body, (
            "/api/metrics must re-raise HTTPException before the broad except "
            "so its own 503s are not caught and re-wrapped (poindexter#749)."
        )
