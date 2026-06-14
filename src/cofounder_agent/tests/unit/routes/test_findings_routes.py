"""Unit tests for the findings-triage HTTP route (Phase 7 operator console).

Covers the read surface added in ``routes/findings_routes.py``:

- GET /api/findings — probe-findings summary (counts, by-kind/severity,
  per-finding routed/pending status + delivery policy)

This mirrors the ``findings_list`` MCP tool, which reads ``audit_log`` rows
where ``event_type='finding'``. The read function
(``services.findings_read.read_findings``) is mocked here so the tests pin the
HTTP contract (verb, path, query-param forwarding, status, delegation) without a
live DB. Auth uses the real ``verify_api_token`` dependency (no override) so an
unauthenticated request 401s. The read function's SQL is covered separately by
``tests/integration_db/test_findings_read.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.findings_routes import router
from utils.route_utils import get_database_dependency

SAMPLE = {
    "findings": [
        {
            "id": 5,
            "timestamp": "2026-06-14T00:00:00",
            "source": "audit_published_quality",
            "severity": "warn",
            "kind": "broken_external_link",
            "title": "Dead link in post #79",
            "status": "PENDING",
            "delivery": "discord",
        }
    ],
    "counts": {"emitted": 3, "pending": 1},
    "by_kind": [{"kind": "broken_external_link", "count": 2}],
    "by_severity": [{"severity": "warn", "count": 2}],
    "delivery_by_kind": {"broken_external_link": "discord"},
    "watermark": 4,
    "hours": 168,
}


def _make_db():
    db = MagicMock()
    # The route builds the read from db_service.pool; read_findings is patched
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
class TestListFindings:
    def test_returns_structured_summary(self):
        with patch(
            "routes.findings_routes.read_findings",
            new=AsyncMock(return_value=SAMPLE),
        ) as m:
            app = _build_app()
            resp = TestClient(app).get("/api/findings")
        assert resp.status_code == 200
        body = resp.json()
        assert body["counts"] == {"emitted": 3, "pending": 1}
        assert body["findings"][0]["kind"] == "broken_external_link"
        assert body["findings"][0]["status"] == "PENDING"
        assert body["delivery_by_kind"] == {"broken_external_link": "discord"}
        assert body["watermark"] == 4
        m.assert_awaited_once()

    def test_forwards_query_params(self):
        with patch(
            "routes.findings_routes.read_findings",
            new=AsyncMock(return_value=SAMPLE),
        ) as m:
            app = _build_app()
            TestClient(app).get(
                "/api/findings?kind=anomaly&severity=critical&hours=24&pending_only=true&limit=10"
            )
        assert m.await_args is not None
        kwargs = m.await_args.kwargs
        assert kwargs["kind"] == "anomaly"
        assert kwargs["severity"] == "critical"
        assert kwargs["hours"] == 24
        assert kwargs["pending_only"] is True
        assert kwargs["limit"] == 10

    def test_defaults_when_no_query_params(self):
        with patch(
            "routes.findings_routes.read_findings",
            new=AsyncMock(return_value=SAMPLE),
        ) as m:
            app = _build_app()
            TestClient(app).get("/api/findings")
        assert m.await_args is not None
        kwargs = m.await_args.kwargs
        assert kwargs["kind"] == ""
        assert kwargs["severity"] == ""
        assert kwargs["hours"] == 168
        assert kwargs["pending_only"] is False
        assert kwargs["limit"] == 30

    def test_rejects_out_of_range_hours(self):
        # hours is clamped by the route's Query bounds (1..720); 0 -> 422.
        with patch(
            "routes.findings_routes.read_findings",
            new=AsyncMock(return_value=SAMPLE),
        ):
            app = _build_app()
            resp = TestClient(app).get("/api/findings?hours=0")
        assert resp.status_code == 422

    def test_requires_auth(self):
        app = _build_app(authed=False)
        resp = TestClient(app).get("/api/findings")
        assert resp.status_code == 401
