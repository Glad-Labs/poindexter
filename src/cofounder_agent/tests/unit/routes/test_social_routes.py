"""
Unit tests for routes/social_routes.py.

Regression: POST /drafts/{id}/approve always returned HTTP 200 with
``{"success": false, "error": ...}`` when SocialDraftsService.approve_draft
blocked the operation (post not published, missing integration UUID, ...).
The console's generic fetch wrapper only inspects the HTTP status code (the
same contract ``approval_routes.py`` uses — see poindexter#743's wrong-state
409 sweep), so a blocked approval silently looked like a success in the
operator console while nothing was pushed to Postiz.

Tests use unit mocks — no real DB or Postiz calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.social_routes as social_routes_module
from middleware.api_token_auth import verify_api_token
from routes.social_routes import router as social_router
from services.social_drafts import SocialDraftRow
from tests.unit.routes.conftest import make_mock_db
from utils.route_utils import get_database_dependency, get_site_config_dependency


def _build_social_app() -> FastAPI:
    app = FastAPI()
    app.include_router(social_router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: make_mock_db()
    app.dependency_overrides[get_site_config_dependency] = lambda: MagicMock()
    return app


@pytest.mark.unit
class TestApproveDraftWrongState409:
    """A blocked approve_draft() result must surface as a non-2xx status —
    not a 200 body the console's status-only error handling can't see."""

    def test_approve_blocked_post_not_published_returns_409(self, monkeypatch):
        mock_svc = MagicMock()
        mock_svc.approve_draft = AsyncMock(
            return_value={
                "success": False,
                "error": (
                    "post abcd1234 is status='approved', not 'published' — "
                    "the promoted link would 404. Wait for the scheduled "
                    "publish (or publish now), then approve"
                ),
            }
        )
        monkeypatch.setattr(social_routes_module, "_svc", mock_svc)
        client = TestClient(_build_social_app())

        resp = client.post("/api/social/drafts/draft-1/approve")

        assert resp.status_code == 409, (
            f"Expected 409 for a blocked approve, got {resp.status_code}: {resp.text}"
        )

    def test_approve_blocked_error_message_in_response_body(self, monkeypatch):
        """The real error must reach the response body — the console needs
        the actual reason, not just a bare status code, to show a useful toast."""
        mock_svc = MagicMock()
        mock_svc.approve_draft = AsyncMock(
            return_value={
                "success": False,
                "error": "no posts row for task xyz — publish the blog post first",
            }
        )
        monkeypatch.setattr(social_routes_module, "_svc", mock_svc)
        client = TestClient(_build_social_app())

        resp = client.post("/api/social/drafts/draft-1/approve")

        assert "publish the blog post first" in resp.text

    def test_approve_success_still_returns_200(self, monkeypatch):
        """Regression: a real successful approve is unaffected by the 409 change."""
        mock_svc = MagicMock()
        mock_svc.approve_draft = AsyncMock(
            return_value={"success": True, "postiz_post_id": "pz-1"}
        )
        monkeypatch.setattr(social_routes_module, "_svc", mock_svc)
        client = TestClient(_build_social_app())

        resp = client.post("/api/social/drafts/draft-1/approve")

        assert resp.status_code == 200, (
            f"Expected 200 for a successful approve, got {resp.status_code}: {resp.text}"
        )
        assert resp.json()["success"] is True
        assert resp.json()["postiz_post_id"] == "pz-1"

    def test_reject_draft_unaffected_returns_200(self, monkeypatch):
        """reject_draft() has no failure path — must keep returning 200."""
        mock_svc = MagicMock()
        mock_svc.reject_draft = AsyncMock(return_value=None)
        monkeypatch.setattr(social_routes_module, "_svc", mock_svc)
        client = TestClient(_build_social_app())

        resp = client.post("/api/social/drafts/draft-1/reject")

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}


@pytest.mark.unit
class TestListDraftsSerialization:
    """GET /drafts must carry post_status — the console's action inbox uses
    it to filter out drafts that would 409 (post not published yet)."""

    def test_list_drafts_includes_post_status(self, monkeypatch):
        mock_svc = MagicMock()
        mock_svc.list_drafts = AsyncMock(
            return_value=[
                SocialDraftRow(
                    id="draft-1", pipeline_task_id="task-1", post_id=None,
                    platform="bluesky", content="c", platform_config={},
                    status="pending", postiz_post_id=None, error=None,
                    retry_count=0, last_retry_at=None,
                    created_at=datetime.now(timezone.utc),
                    approved_at=None, posted_at=None, post_status="published",
                )
            ]
        )
        monkeypatch.setattr(social_routes_module, "_svc", mock_svc)
        client = TestClient(_build_social_app())

        resp = client.get("/api/social/drafts")

        assert resp.status_code == 200
        assert resp.json()["drafts"][0]["post_status"] == "published"
