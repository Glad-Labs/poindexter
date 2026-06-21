"""Unit tests for ``routes.data_plane_routes`` → ``/api/data-plane/*`` (#1522).

The HTTP mirror of ``services.declarative_config_service``. Thin adapter: every
handler delegates to the service; service calls are patched, no real DB. Follows
the #1491 ``test_operator_http_surfaces.py`` idiom.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from tests.unit.routes.conftest import make_mock_db
from utils.route_utils import get_database_dependency

_SVC = "services.declarative_config_service"


def _app(mock_db=None):
    from routes.data_plane_routes import router

    app = FastAPI()
    app.include_router(router)
    db = mock_db or make_mock_db()
    app.dependency_overrides[verify_api_token] = lambda: "tok"
    app.dependency_overrides[get_database_dependency] = lambda: db
    return app, db


@pytest.mark.unit
class TestDataPlaneRoutes:
    def test_list_returns_envelope(self):
        app, _ = _app()
        rows = [{"name": "rss", "enabled": True}]
        with patch(f"{_SVC}.list_rows", new=AsyncMock(return_value=rows)):
            resp = TestClient(app).get("/api/data-plane/taps")
        assert resp.status_code == 200
        data = resp.json()
        # Canonical offset envelope (poindexter#745): items, not the legacy rows
        # key. Unpaginated full listing → limit == len(items), offset 0.
        assert data["total"] == 1
        assert data["limit"] == 1
        assert data["offset"] == 0
        assert "rows" not in data
        assert data["items"][0]["name"] == "rss"
        assert data["items"][0]["enabled"] is True

    def test_list_unknown_surface_returns_404(self):
        # No patch — the real service's resolve_surface raises
        # UnknownSurfaceError before touching the pool.
        app, _ = _app()
        resp = TestClient(app).get("/api/data-plane/nope")
        assert resp.status_code == 404

    def test_get_found(self):
        app, _ = _app()
        with patch(f"{_SVC}.get_row", new=AsyncMock(return_value={"name": "rss"})):
            resp = TestClient(app).get("/api/data-plane/taps/rss")
        assert resp.status_code == 200
        assert resp.json()["name"] == "rss"

    def test_get_not_found_returns_404(self):
        app, _ = _app()
        with patch(f"{_SVC}.get_row", new=AsyncMock(return_value=None)):
            resp = TestClient(app).get("/api/data-plane/taps/missing")
        assert resp.status_code == 404

    def test_upsert_injects_path_key_and_returns_row(self):
        app, _ = _app()
        captured: dict = {}

        async def _fake_upsert(pool, surface, payload):
            captured.update(payload)
            return payload

        with patch(f"{_SVC}.upsert_row", new=_fake_upsert):
            resp = TestClient(app).put(
                "/api/data-plane/taps/rss", json={"enabled": True}
            )
        assert resp.status_code == 200
        # the path key wins over (or supplies) the body's key column
        assert captured["name"] == "rss"
        assert captured["enabled"] is True

    def test_upsert_validation_error_returns_400(self):
        app, _ = _app()
        from services.declarative_config_service import SurfaceValidationError

        with patch(
            f"{_SVC}.upsert_row",
            new=AsyncMock(side_effect=SurfaceValidationError("bad")),
        ):
            resp = TestClient(app).put("/api/data-plane/taps/rss", json={})
        assert resp.status_code == 400

    def test_delete_success(self):
        app, _ = _app()
        with patch(f"{_SVC}.delete_row", new=AsyncMock(return_value=True)):
            resp = TestClient(app).delete("/api/data-plane/taps/rss")
        assert resp.status_code == 200
        assert resp.json()["deleted"] is True

    def test_delete_miss_returns_404(self):
        app, _ = _app()
        with patch(f"{_SVC}.delete_row", new=AsyncMock(return_value=False)):
            resp = TestClient(app).delete("/api/data-plane/taps/missing")
        assert resp.status_code == 404

    def test_every_route_requires_auth(self):
        # Core to #1340: the HTTP mirror must be OAuth-guarded. Assert every
        # route declares the verify_api_token dependency.
        from routes.data_plane_routes import router

        for route in router.routes:
            calls = {d.call for d in route.dependant.dependencies}
            assert verify_api_token in calls, f"{route.path} missing auth dependency"
