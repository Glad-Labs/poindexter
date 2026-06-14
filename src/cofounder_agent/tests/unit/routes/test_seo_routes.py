"""Unit tests for the SEO-refresh HTTP route (Phase 11 operator console).

Covers the read surface added in ``routes/seo_routes.py``:

- GET /api/seo — SEO-refresh summary (actionable queue, recent refreshes
  with a baseline→outcome delta, by-status/by-tier rollups)

The read function (``services.seo_read.read_seo``) is mocked here so the
tests pin the HTTP contract (verb, path, limit forwarding, status,
delegation) without a live DB. Auth uses the real ``verify_api_token``
dependency (no override) so an unauthenticated request 401s. The read
function's SQL is covered separately by
``tests/unit/services/test_seo_read.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.seo_routes import router
from utils.route_utils import get_database_dependency

SAMPLE = {
    "queue": [
        {
            "id": "1",
            "slug": "foo-post",
            "target_query": "best gpu 2026",
            "tier": "quick_win",
            "status": "open",
            "position": 8.2,
            "impressions": 1200,
            "gap_score": 120.5,
            "detected_at": "2026-06-12T00:00:00",
        }
    ],
    "refreshes": [
        {
            "id": "2",
            "slug": "baz-post",
            "target_query": "tailscale setup",
            "tier": "high_value",
            "status": "refreshed",
            "baseline_position": 12.0,
            "outcome_position": 6.0,
            "delta": 6.0,
            "measured_at": "2026-06-12T01:00:00",
        }
    ],
    "by_status": [{"status": "open", "count": 5}],
    "by_tier": [{"tier": "quick_win", "count": 3}],
}


def _make_db():
    db = MagicMock()
    # The route builds the read from db_service.pool; read_seo is patched
    # per-test, so the pool only needs to be *passable*.
    db.pool = MagicMock(name="pool")
    return db


def _build_app(db=None, *, authed=True):
    db = db if db is not None else _make_db()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database_dependency] = lambda: db
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


@pytest.mark.unit
class TestSeoSummary:
    def test_returns_structured_summary(self):
        with patch("routes.seo_routes.read_seo", new=AsyncMock(return_value=SAMPLE)) as m:
            app = _build_app()
            resp = TestClient(app).get("/api/seo")
        assert resp.status_code == 200
        body = resp.json()
        assert body["queue"][0]["slug"] == "foo-post"
        assert body["queue"][0]["gap_score"] == 120.5
        assert body["refreshes"][0]["delta"] == 6.0
        assert body["by_status"] == [{"status": "open", "count": 5}]
        m.assert_awaited_once()

    def test_forwards_limit(self):
        with patch("routes.seo_routes.read_seo", new=AsyncMock(return_value=SAMPLE)) as m:
            app = _build_app()
            TestClient(app).get("/api/seo?limit=10")
        assert m.await_args is not None
        assert m.await_args.kwargs["limit"] == 10

    def test_default_limit(self):
        with patch("routes.seo_routes.read_seo", new=AsyncMock(return_value=SAMPLE)) as m:
            app = _build_app()
            TestClient(app).get("/api/seo")
        assert m.await_args is not None
        assert m.await_args.kwargs["limit"] == 30

    def test_rejects_out_of_range_limit(self):
        # limit is clamped by the route's Query bounds (1..100); 0 -> 422.
        with patch("routes.seo_routes.read_seo", new=AsyncMock(return_value=SAMPLE)):
            app = _build_app()
            resp = TestClient(app).get("/api/seo?limit=0")
        assert resp.status_code == 422

    def test_requires_auth(self):
        app = _build_app(authed=False)
        resp = TestClient(app).get("/api/seo")
        assert resp.status_code == 401
