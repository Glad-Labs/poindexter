"""Unit tests for the /api/logs HTTP route (Loki proxy)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.logs_routes import router
from utils.route_utils import get_site_config_dependency

SAMPLE = {
    "lines": [
        {
            "ts": "2026-06-27T00:00:00+00:00",
            "service": "poindexter-worker",
            "level": "error",
            "line": "boom",
        }
    ],
    "stats": {"count": 1, "query": '{service=~".+"}'},
}


def _build_app(*, authed=True):
    app = FastAPI()
    app.include_router(router)
    sc = MagicMock()
    sc.get.return_value = "http://loki:3100"
    app.dependency_overrides[get_site_config_dependency] = lambda: sc
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


@pytest.mark.unit
def test_returns_logs_payload():
    app = _build_app()
    with patch("routes.logs_routes.read_logs", new=AsyncMock(return_value=SAMPLE)) as m:
        res = TestClient(app).get("/api/logs?service=poindexter-worker&level=error")
    assert res.status_code == 200
    assert res.json() == SAMPLE
    # query params forwarded to the read-service
    assert m.await_args.kwargs["service"] == "poindexter-worker"
    assert m.await_args.kwargs["level"] == "error"


@pytest.mark.unit
def test_requires_auth():
    app = _build_app(authed=False)
    res = TestClient(app).get("/api/logs")
    assert res.status_code == 401


@pytest.mark.unit
def test_clamps_limit_via_query_validation():
    app = _build_app()
    res = TestClient(app).get("/api/logs?limit=99999")
    assert res.status_code == 422  # Query(le=1000) rejects out-of-range
