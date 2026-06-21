"""Unit tests for the 5 new operator HTTP-surface route modules (#1343).

Surfaces under test:
  - routes.gates_routes          → /api/gates/*
  - routes.posts_approval_routes → /api/posts-approval/*
  - routes.scheduling_routes     → /api/scheduling/*
  - routes.topic_batch_routes    → /api/topic-batches/*
  - routes.media_approval_routes → /api/media-approval/*

All service calls are patched; no real DB connection is required.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from tests.unit.routes.conftest import make_mock_db
from utils.route_utils import get_database_dependency, get_site_config_dependency

NOW = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)


def _mock_site_config() -> MagicMock:
    """Zero-arg dependency override that returns a bare MagicMock site config.

    Using a named function instead of `lambda: MagicMock()` avoids the
    "unnecessary lambda" lint rule while also avoiding passing `MagicMock`
    (the class) directly — FastAPI would inspect its complex __init__
    signature and incorrectly inject kwargs as request parameters on POST
    endpoints (causing 422 validation errors).
    """
    return MagicMock()


# ---------------------------------------------------------------------------
# App builders — one per route module
# ---------------------------------------------------------------------------


def _app_gates(mock_db=None):
    from routes.gates_routes import router

    app = FastAPI()
    app.include_router(router)
    db = mock_db or make_mock_db()
    app.dependency_overrides[verify_api_token] = lambda: "tok"
    app.dependency_overrides[get_database_dependency] = lambda: db
    app.dependency_overrides[get_site_config_dependency] = _mock_site_config
    return app, db


def _app_posts_approval(mock_db=None):
    from routes.posts_approval_routes import router

    app = FastAPI()
    app.include_router(router)
    db = mock_db or make_mock_db()
    app.dependency_overrides[verify_api_token] = lambda: "tok"
    app.dependency_overrides[get_database_dependency] = lambda: db
    app.dependency_overrides[get_site_config_dependency] = _mock_site_config
    return app, db


def _app_scheduling(mock_db=None):
    from routes.scheduling_routes import router

    app = FastAPI()
    app.include_router(router)
    db = mock_db or make_mock_db()
    app.dependency_overrides[verify_api_token] = lambda: "tok"
    app.dependency_overrides[get_database_dependency] = lambda: db
    app.dependency_overrides[get_site_config_dependency] = _mock_site_config
    return app, db


def _app_topic_batch(mock_db=None):
    from routes.topic_batch_routes import router

    app = FastAPI()
    app.include_router(router)
    db = mock_db or make_mock_db()
    app.dependency_overrides[verify_api_token] = lambda: "tok"
    app.dependency_overrides[get_database_dependency] = lambda: db
    app.dependency_overrides[get_site_config_dependency] = _mock_site_config
    return app, db


def _app_media_approval(mock_db=None):
    from routes.media_approval_routes import router

    app = FastAPI()
    app.include_router(router)
    db = mock_db or make_mock_db()
    app.dependency_overrides[verify_api_token] = lambda: "tok"
    app.dependency_overrides[get_database_dependency] = lambda: db
    return app, db


# ---------------------------------------------------------------------------
# Helpers — dataclass stubs for service return types
# ---------------------------------------------------------------------------

def _schedule_result(ok=True, detail="ok", rows=None, count=0):
    """Minimal ScheduleResult-like dataclass."""
    from services.scheduling_service import ScheduleResult

    return ScheduleResult(ok=ok, detail=detail, rows=rows or [], count=count)


# ---------------------------------------------------------------------------
# Gates routes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGatesRoutes:
    def test_list_gates_returns_envelope(self):
        app, _ = _app_gates()
        gates = [{"gate_name": "draft", "enabled": True, "pending_count": 2}]
        with patch("services.approval_service.list_gates", new=AsyncMock(return_value=gates)):
            resp = TestClient(app).get("/api/gates")
        assert resp.status_code == 200
        data = resp.json()
        # Canonical offset envelope (poindexter#745): items, not the legacy gates key.
        assert data["total"] == 1
        assert data["limit"] == 1  # full unpaginated list → limit == len(items)
        assert data["offset"] == 0
        assert "gates" not in data
        assert data["items"][0]["gate_name"] == "draft"
        assert data["items"][0]["pending_count"] == 2

    def test_list_gates_empty(self):
        app, _ = _app_gates()
        with patch("services.approval_service.list_gates", new=AsyncMock(return_value=[])):
            resp = TestClient(app).get("/api/gates")
        assert resp.json() == {"items": [], "total": 0, "limit": 0, "offset": 0}

    def test_set_gate_enabled_true(self):
        app, _ = _app_gates()
        result = {"ok": True, "gate_name": "draft", "enabled": True, "key": "pipeline_gate_draft"}
        with patch("services.approval_service.set_gate_enabled", new=AsyncMock(return_value=result)):
            resp = TestClient(app).patch("/api/gates/draft", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

    def test_list_pending_gates(self):
        app, _ = _app_gates()
        tasks = [{"task_id": "t-1", "gate_name": "draft"}]
        with patch("services.approval_service.list_pending", new=AsyncMock(return_value=tasks)):
            resp = TestClient(app).get("/api/gates/pending")
        assert resp.status_code == 200
        data = resp.json()
        # Canonical offset envelope (poindexter#745): items, not the legacy tasks key.
        assert data["total"] == 1
        assert data["limit"] == 100
        assert data["offset"] == 0
        assert "tasks" not in data
        assert data["items"][0]["task_id"] == "t-1"

    def test_show_pending_found(self):
        app, _ = _app_gates()
        detail = {"task_id": "t-1", "gate_name": "draft", "artifact": {}}
        with patch("services.approval_service.show_pending", new=AsyncMock(return_value=detail)):
            resp = TestClient(app).get("/api/gates/pending/t-1")
        assert resp.status_code == 200
        assert resp.json()["task_id"] == "t-1"

    def test_show_pending_not_found_returns_404(self):
        app, _ = _app_gates()
        from services.approval_service import TaskNotFoundError

        with patch(
            "services.approval_service.show_pending",
            new=AsyncMock(side_effect=TaskNotFoundError("Task t-x not found")),
        ):
            resp = TestClient(app).get("/api/gates/pending/t-x")
        assert resp.status_code == 404

    def test_show_pending_not_paused_returns_409(self):
        app, _ = _app_gates()
        from services.approval_service import TaskNotPausedError

        with patch(
            "services.approval_service.show_pending",
            new=AsyncMock(side_effect=TaskNotPausedError("not paused")),
        ):
            resp = TestClient(app).get("/api/gates/pending/t-1")
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Posts-approval routes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPostsApprovalRoutes:
    def test_list_pending_publish_returns_envelope(self):
        app, _ = _app_posts_approval()
        posts = [{"post_id": "p-1", "gate_name": "final_publish_approval"}]
        with patch("services.posts_approval_service.list_pending_publish", new=AsyncMock(return_value=posts)):
            resp = TestClient(app).get("/api/posts-approval/pending")
        assert resp.status_code == 200
        data = resp.json()
        # Canonical offset envelope (poindexter#745): items, not the legacy posts key.
        assert data["total"] == 1
        assert data["limit"] == 100
        assert data["offset"] == 0
        assert "posts" not in data
        assert data["items"][0]["post_id"] == "p-1"

    def test_approve_publish_success(self):
        app, _ = _app_posts_approval()
        result = {"ok": True, "post_id": "p-1", "gate_name": "final_publish_approval", "feedback": ""}
        with patch("services.posts_approval_service.approve_publish", new=AsyncMock(return_value=result)):
            resp = TestClient(app).post("/api/posts-approval/p-1/approve", json={})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_approve_publish_not_found_returns_404(self):
        app, _ = _app_posts_approval()
        from services.posts_approval_service import PostNotFoundError

        with patch(
            "services.posts_approval_service.approve_publish",
            new=AsyncMock(side_effect=PostNotFoundError("Post p-x not found")),
        ):
            resp = TestClient(app).post("/api/posts-approval/p-x/approve", json={})
        assert resp.status_code == 404

    def test_reject_publish_success(self):
        app, _ = _app_posts_approval()
        result = {"ok": True, "post_id": "p-1", "gate_name": "final_publish_approval", "new_status": "rejected", "reason": "off-brand"}
        with patch("services.posts_approval_service.reject_publish", new=AsyncMock(return_value=result)):
            resp = TestClient(app).post("/api/posts-approval/p-1/reject", json={"reason": "off-brand"})
        assert resp.status_code == 200
        assert resp.json()["new_status"] == "rejected"

    def test_reject_publish_gate_mismatch_returns_409(self):
        app, _ = _app_posts_approval()
        from services.posts_approval_service import PostGateMismatchError

        with patch(
            "services.posts_approval_service.reject_publish",
            new=AsyncMock(side_effect=PostGateMismatchError("mismatch")),
        ):
            resp = TestClient(app).post("/api/posts-approval/p-1/reject", json={})
        assert resp.status_code == 409

    def test_show_pending_publish_found(self):
        app, _ = _app_posts_approval()
        detail = {"post_id": "p-1", "gate_name": "final_publish_approval", "artifact": {}}
        with patch("services.posts_approval_service.show_pending_publish", new=AsyncMock(return_value=detail)):
            resp = TestClient(app).get("/api/posts-approval/pending/p-1")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Scheduling routes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSchedulingRoutes:
    def test_list_scheduled_returns_result(self):
        app, _ = _app_scheduling()
        result = _schedule_result(rows=[{"post_id": "p-1", "published_at": NOW}], count=1)
        with patch("services.scheduling_service.list_scheduled", new=AsyncMock(return_value=result)):
            resp = TestClient(app).get("/api/scheduling")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["count"] == 1

    def test_show_scheduled_not_found_returns_ok_false(self):
        app, _ = _app_scheduling()
        result = _schedule_result(ok=False, detail="Post p-x not found")
        with patch("services.scheduling_service.show_scheduled", new=AsyncMock(return_value=result)):
            resp = TestClient(app).get("/api/scheduling/p-x")
        assert resp.status_code == 200
        assert resp.json()["ok"] is False

    def test_assign_slot_success(self):
        app, _ = _app_scheduling()
        result = _schedule_result(rows=[{"post_id": "p-1"}], count=1)
        with patch("services.scheduling_service.assign_slot", new=AsyncMock(return_value=result)):
            resp = TestClient(app).post("/api/scheduling/p-1", json={"when": "2026-07-01T12:00:00Z"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_assign_slot_bad_when_returns_422(self):
        app, _ = _app_scheduling()
        with patch("services.scheduling_service.assign_slot", new=AsyncMock(side_effect=ValueError("bad when"))):
            resp = TestClient(app).post("/api/scheduling/p-1", json={"when": "not-a-date"})
        assert resp.status_code == 422

    def test_assign_batch_success(self):
        app, _ = _app_scheduling()
        result = _schedule_result(count=3)
        with patch("services.scheduling_service.assign_batch", new=AsyncMock(return_value=result)):
            resp = TestClient(app).post(
                "/api/scheduling/batch",
                json={"count": 3, "interval": "1d", "start": "2026-07-01T12:00:00Z"},
            )
        assert resp.status_code == 200

    def test_shift_success(self):
        app, _ = _app_scheduling()
        result = _schedule_result(count=2)
        with patch("services.scheduling_service.shift", new=AsyncMock(return_value=result)):
            resp = TestClient(app).patch("/api/scheduling/shift", json={"by_delta": "2h"})
        assert resp.status_code == 200

    def test_clear_success(self):
        app, _ = _app_scheduling()
        result = _schedule_result(count=1)
        with patch("services.scheduling_service.clear", new=AsyncMock(return_value=result)):
            resp = TestClient(app).delete("/api/scheduling")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Topic-batch routes
# ---------------------------------------------------------------------------

BATCH_ID = "11111111-1111-1111-1111-111111111111"


def _make_batch_view():
    from services.topic_batch_service import BatchView, CandidateView

    cand = CandidateView(
        id="cand-001",
        kind="external",
        title="AI in 2026",
        summary="Overview",
        score=0.9,
        decay_factor=1.0,
        effective_score=0.9,
        rank_in_batch=1,
        operator_rank=None,
        operator_edited_topic=None,
        operator_edited_angle=None,
        score_breakdown={},
    )
    return BatchView(
        id=UUID(BATCH_ID),
        niche_id=UUID("22222222-2222-2222-2222-222222222222"),
        status="open",
        picked_candidate_id=None,
        candidates=[cand],
    )


@pytest.mark.unit
class TestTopicBatchRoutes:
    def test_show_batch_returns_view(self):
        app, _ = _app_topic_batch()
        view = _make_batch_view()
        with patch("services.topic_batch_service.TopicBatchService.show_batch", new=AsyncMock(return_value=view)):
            resp = TestClient(app).get(f"/api/topic-batches/{BATCH_ID}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "open"
        assert len(data["candidates"]) == 1

    def test_show_batch_not_found_returns_404(self):
        app, _ = _app_topic_batch()
        with patch(
            "services.topic_batch_service.TopicBatchService.show_batch",
            new=AsyncMock(side_effect=ValueError("unknown batch_id")),
        ):
            resp = TestClient(app).get(f"/api/topic-batches/{BATCH_ID}")
        assert resp.status_code == 404

    def test_show_batch_invalid_uuid_returns_422(self):
        app, _ = _app_topic_batch()
        resp = TestClient(app).get("/api/topic-batches/not-a-uuid")
        assert resp.status_code == 422

    def test_rank_batch_success(self):
        app, _ = _app_topic_batch()
        with patch("services.topic_batch_service.TopicBatchService.rank_batch", new=AsyncMock(return_value=None)):
            resp = TestClient(app).post(
                f"/api/topic-batches/{BATCH_ID}/rank",
                json={"ordered_candidate_ids": ["cand-001"]},
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_edit_winner_no_rank_one_returns_409(self):
        app, _ = _app_topic_batch()
        with patch(
            "services.topic_batch_service.TopicBatchService.edit_winner",
            new=AsyncMock(side_effect=ValueError("no rank-1 candidate")),
        ):
            resp = TestClient(app).post(
                f"/api/topic-batches/{BATCH_ID}/edit-winner",
                json={"topic": "New Topic"},
            )
        assert resp.status_code == 409

    def test_resolve_batch_success(self):
        app, _ = _app_topic_batch()
        with patch("services.topic_batch_service.TopicBatchService.resolve_batch", new=AsyncMock(return_value=None)):
            resp = TestClient(app).post(f"/api/topic-batches/{BATCH_ID}/resolve")
        assert resp.status_code == 200

    def test_reject_batch_success(self):
        app, _ = _app_topic_batch()
        with patch("services.topic_batch_service.TopicBatchService.reject_batch", new=AsyncMock(return_value=None)):
            resp = TestClient(app).post(f"/api/topic-batches/{BATCH_ID}/reject", json={})
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Media-approval routes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMediaApprovalRoutes:
    def test_list_pending_returns_envelope(self):
        app, _ = _app_media_approval()
        rows = [{"post_id": "p-1", "medium": "podcast", "created_at": NOW}]
        with patch("services.media_approval_service.list_pending", new=AsyncMock(return_value=rows)):
            resp = TestClient(app).get("/api/media-approval/pending")
        assert resp.status_code == 200
        data = resp.json()
        # Canonical offset envelope (poindexter#745): items, not the legacy media key.
        assert data["total"] == 1
        assert data["limit"] == 50
        assert data["offset"] == 0
        assert "media" not in data
        assert data["items"][0]["medium"] == "podcast"

    def test_list_pending_empty(self):
        app, _ = _app_media_approval()
        with patch("services.media_approval_service.list_pending", new=AsyncMock(return_value=[])):
            resp = TestClient(app).get("/api/media-approval/pending")
        assert resp.json() == {"items": [], "total": 0, "limit": 50, "offset": 0}

    def test_decide_approve_success(self):
        app, _ = _app_media_approval()
        with patch("services.media_approval_service.decide", new=AsyncMock(return_value=None)):
            with patch("middleware.api_token_auth.get_operator_identity", return_value={"id": "operator:test"}):
                resp = TestClient(app).post(
                    "/api/media-approval/p-1/podcast/decide",
                    json={"approved": True},
                )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        assert resp.json()["approved"] is True

    def test_decide_row_not_found_returns_404(self):
        app, _ = _app_media_approval()
        with patch(
            "services.media_approval_service.decide",
            new=AsyncMock(side_effect=ValueError("No media_approvals row")),
        ):
            with patch("middleware.api_token_auth.get_operator_identity", return_value={"id": "operator:test"}):
                resp = TestClient(app).post(
                    "/api/media-approval/p-1/podcast/decide",
                    json={"approved": False},
                )
        assert resp.status_code == 404
