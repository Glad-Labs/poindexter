"""
Unit tests for routes/brain_routes.py

Tests cover:
- GET /api/brain/stats — returns decisions counts + knowledge total + recent decisions
- Route is auth-protected
- _iso helper handles None / naive / aware datetimes
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.brain_routes import router
from services.brain_stats import _iso
from utils.route_utils import get_database_dependency


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestIsoHelper:
    def test_none_returns_none(self):
        assert _iso(None) is None

    def test_aware_datetime(self):
        dt = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
        assert _iso(dt) == "2026-04-12T12:00:00+00:00"

    def test_naive_datetime_gets_utc(self):
        dt = datetime(2026, 4, 12, 12, 0, 0)
        result = _iso(dt)
        assert "+00:00" in result


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


def _make_pool(counts_row, knowledge_count, recent_rows):
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=counts_row)
    pool.fetchval = AsyncMock(return_value=knowledge_count)
    pool.fetch = AsyncMock(return_value=recent_rows)
    db = MagicMock()
    db.pool = pool
    db.cloud_pool = None  # force route to use db.pool path
    return db


def _row(**kwargs):
    r = MagicMock()
    r.__getitem__ = lambda _, k: kwargs[k]
    return r


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"

    now = datetime(2026, 6, 25, 10, 0, 0, tzinfo=timezone.utc)
    counts = _row(
        decisions_24h=3,
        decisions_7d=15,
        avg_confidence_7d=0.87,
        last_cycle_at=now,
    )
    knowledge_count = 1234
    recent = [
        _row(
            id=99,
            decision="Monitor site latency",
            outcome="monitored",
            confidence=0.9,
            created_at=now,
        )
    ]
    db = _make_pool(counts, knowledge_count, recent)

    app.dependency_overrides[get_database_dependency] = lambda: db
    return TestClient(app)


class TestBrainStatsRoute:
    def test_returns_200(self, client):
        resp = client.get("/api/brain/stats")
        assert resp.status_code == 200

    def test_decisions_counts(self, client):
        data = client.get("/api/brain/stats").json()
        assert data["decisions_24h"] == 3
        assert data["decisions_7d"] == 15

    def test_avg_confidence(self, client):
        data = client.get("/api/brain/stats").json()
        assert abs(data["avg_confidence_7d"] - 0.87) < 0.001

    def test_knowledge_total(self, client):
        data = client.get("/api/brain/stats").json()
        assert data["knowledge_total"] == 1234

    def test_recent_decisions_shape(self, client):
        data = client.get("/api/brain/stats").json()
        assert len(data["recent_decisions"]) == 1
        dec = data["recent_decisions"][0]
        assert dec["decision"] == "Monitor site latency"
        assert dec["outcome"] == "monitored"
        assert abs(dec["confidence"] - 0.9) < 0.001

    def test_last_cycle_at_iso(self, client):
        data = client.get("/api/brain/stats").json()
        assert data["last_cycle_at"].startswith("2026-06-25")


class TestBrainRouteRegistration:
    def test_stats_endpoint_registered(self):
        paths = [r.path for r in router.routes]
        assert "/api/brain/stats" in paths


class TestBrainStatsAuthRequired:
    def test_returns_403_without_token(self):
        app = FastAPI()
        app.include_router(router)
        counts = _row(decisions_24h=0, decisions_7d=0, avg_confidence_7d=None, last_cycle_at=None)
        db = _make_pool(counts, 0, [])
        app.dependency_overrides[get_database_dependency] = lambda: db
        tc = TestClient(app, raise_server_exceptions=False)
        resp = tc.get("/api/brain/stats")
        assert resp.status_code in (401, 403)
