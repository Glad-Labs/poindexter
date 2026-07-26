"""Unit tests for routes/service_restart_routes.py (poindexter#909).

HTTP-layer contract: status codes, response shape, and that a malformed
container name never reaches the DB layer. The console's wire-shape contract
test (console/js/__tests__/api.restart.test.js) pins the exact POST body/URL
shape from the frontend side — this file is the backend half.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.service_restart_routes import router
from utils.route_utils import get_database_dependency

pytestmark = pytest.mark.unit


def _build_app(*, authed: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    db = MagicMock()
    db.pool = MagicMock(name="pool")
    app.dependency_overrides[get_database_dependency] = lambda: db
    if authed:
        app.dependency_overrides[verify_api_token] = lambda: "test-token"
    return app


class TestPostRestart:
    def test_queues_and_returns_202(self):
        row = {
            "id": "11111111-1111-1111-1111-111111111111",
            "container": "poindexter-pyroscope",
            "status": "pending",
            "requested_by": "console",
            "detail": None,
            "requested_at": datetime.now(timezone.utc),
            "claimed_at": None,
            "completed_at": None,
        }
        with patch(
            "routes.service_restart_routes.create_restart_request",
            new=AsyncMock(return_value=row),
        ):
            resp = TestClient(_build_app()).post(
                "/api/services/poindexter-pyroscope/restart"
            )
        assert resp.status_code == 202
        body = resp.json()
        assert body["id"] == row["id"]
        assert body["container"] == "poindexter-pyroscope"
        assert body["status"] == "pending"

    def test_invalid_container_name_returns_400(self):
        # A single-path-segment name with no poindexter- prefix — reaches the
        # handler (unlike a path-traversal shape, which Starlette's own
        # routing rejects as a 404 before any handler code runs) and is
        # rejected by create_restart_request's shape check.
        from services.service_restart_requests import InvalidContainerName

        with patch(
            "routes.service_restart_routes.create_restart_request",
            new=AsyncMock(side_effect=InvalidContainerName("not-a-real-container")),
        ):
            resp = TestClient(_build_app()).post(
                "/api/services/not-a-real-container/restart"
            )
        assert resp.status_code == 400

    def test_requires_auth(self):
        resp = TestClient(_build_app(authed=False)).post(
            "/api/services/poindexter-worker/restart"
        )
        assert resp.status_code in (401, 403)


class TestGetRestartStatus:
    def test_returns_current_status(self):
        row = {
            "id": "22222222-2222-2222-2222-222222222222",
            "container": "poindexter-worker",
            "status": "done",
            "requested_by": "console",
            "detail": "restarted poindexter-worker",
            "requested_at": datetime.now(timezone.utc),
            "claimed_at": datetime.now(timezone.utc),
            "completed_at": datetime.now(timezone.utc),
        }
        with patch(
            "routes.service_restart_routes.get_restart_request",
            new=AsyncMock(return_value=row),
        ):
            resp = TestClient(_build_app()).get(
                f"/api/services/restart/{row['id']}"
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "done"

    def test_unknown_id_returns_404(self):
        with patch(
            "routes.service_restart_routes.get_restart_request",
            new=AsyncMock(return_value=None),
        ):
            resp = TestClient(_build_app()).get(
                "/api/services/restart/33333333-3333-3333-3333-333333333333"
            )
        assert resp.status_code == 404

    def test_requires_auth(self):
        resp = TestClient(_build_app(authed=False)).get(
            "/api/services/restart/33333333-3333-3333-3333-333333333333"
        )
        assert resp.status_code in (401, 403)
