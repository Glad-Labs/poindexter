"""Auth-gate regression tests for the formerly-unauthenticated routes.

The 2026-05-12 security audit (docs/security/audit-2026-05-12.md P0 #4)
caught the following endpoints serving sensitive operator data without
any auth dependency:

- ``GET /api/pipeline/events``
- ``GET /api/pipeline/events/task/{task_id}``
- ``GET /pipeline`` (HTML operator dashboard)
- ``GET /api/memory/stats``
- ``GET /api/memory/search``
- ``GET /memory`` (HTML operator dashboard)

The hotfix added ``Depends(verify_api_token)`` to all six. These tests
pin that contract so a future refactor can't silently un-gate them.

Test strategy: drive the routes via ``TestClient`` and confirm the
unauthenticated request lands at HTTP 401 (the standard "Missing
authentication" response). We deliberately don't supply a valid token
because the auth dependency is the contract under test.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def app_with_routes():
    """Build a minimal FastAPI app that just mounts the two routers
    under test, with the verify_api_token dependency intact so we can
    assert the 401 lands."""
    from routes.memory_dashboard_routes import router as memory_router
    from routes.pipeline_events_routes import router as pipeline_router

    app = FastAPI()
    app.include_router(pipeline_router)
    app.include_router(memory_router)
    return app


@pytest.fixture(scope="module")
def client(app_with_routes):
    return TestClient(app_with_routes, raise_server_exceptions=False)


@pytest.mark.unit
class TestPipelineEventsAuth:
    """``pipeline_events_routes.py`` — three endpoints."""

    def test_list_pipeline_events_requires_auth(self, client):
        r = client.get("/api/pipeline/events")
        assert r.status_code == 401, (
            f"Expected 401 from unauthenticated /api/pipeline/events, got "
            f"{r.status_code}. The audit fix (Depends(verify_api_token)) "
            f"was reverted or bypassed."
        )

    def test_task_pipeline_events_requires_auth(self, client):
        r = client.get(
            "/api/pipeline/events/task/a03de6d6-5dcb-4400-8b41-3ee33702479e",
        )
        assert r.status_code == 401

    def test_pipeline_dashboard_html_requires_auth(self, client):
        r = client.get("/pipeline")
        assert r.status_code == 401


@pytest.mark.unit
class TestMemoryDashboardAuth:
    """``memory_dashboard_routes.py`` — three endpoints."""

    def test_memory_stats_requires_auth(self, client):
        r = client.get("/api/memory/stats")
        assert r.status_code == 401

    def test_memory_search_requires_auth(self, client):
        r = client.get("/api/memory/search?q=anything")
        assert r.status_code == 401

    def test_memory_dashboard_html_requires_auth(self, client):
        r = client.get("/memory")
        assert r.status_code == 401
