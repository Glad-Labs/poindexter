"""Unit tests for the /api/traces HTTP route (Langfuse proxy)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.traces_routes import router
from services.traces_read import LangfuseNotConfigured
from utils.route_utils import get_site_config_dependency

SAMPLE = {
    "traces": [
        {
            "id": "abc",
            "name": "qa_pass",
            "model": "gemma-4-31b",
            "latency_ms": 2500,
            "cost_usd": 0.01,
            "qa_score": 87,
            "task_id": "t9",
            "timestamp": "2026-06-27T10:00:00Z",
            "web_url": "http://localhost:3010/trace/abc",
        }
    ],
    "stats": {"count": 1},
}


def _build_app(*, authed=True):
    app = FastAPI()
    app.include_router(router)
    sc = MagicMock()
    sc.get.return_value = "http://localhost:3010"
    sc.get_secret = AsyncMock(return_value="key")
    app.dependency_overrides[get_site_config_dependency] = lambda: sc
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app, sc


@pytest.mark.unit
def test_returns_traces_payload():
    app, _ = _build_app()
    with patch("routes.traces_routes.read_traces", new=AsyncMock(return_value=SAMPLE)):
        res = TestClient(app).get("/api/traces?hours=12&limit=10")
    assert res.status_code == 200
    assert res.json() == SAMPLE


@pytest.mark.unit
def test_unconfigured_returns_503():
    app, _ = _build_app()
    with patch(
        "routes.traces_routes.read_traces",
        new=AsyncMock(side_effect=LangfuseNotConfigured("set langfuse keys")),
    ):
        res = TestClient(app).get("/api/traces")
    assert res.status_code == 503
    assert "langfuse" in res.json()["detail"].lower()


@pytest.mark.unit
def test_requires_auth():
    app, _ = _build_app(authed=False)
    res = TestClient(app).get("/api/traces")
    assert res.status_code == 401
