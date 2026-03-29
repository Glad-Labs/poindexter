"""
Integration tests for the complete content pipeline flow.

Tests the end-to-end lifecycle: create task → verify pending → approve → publish
→ verify post created. All DB and external service calls are mocked — no live
server, database, or LLM required.

Covers:
- Flow 1: Full pipeline — create → get (pending) → approve → publish → post created
- Flow 2: Cannot publish a task that is not approved
- Flow 3: Cannot approve a nonexistent task
- Flow 4: Publish creates a post entry via db_service.create_post
- Flow 5: Publish returns published status with post metadata
"""

import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from routes.auth_unified import get_current_user, get_current_user_optional
from utils.route_utils import get_database_dependency

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

TEST_USER = {
    "id": "pipeline-user-uuid-0001",
    "email": "pipeline@example.com",
    "username": "pipeline_user",
    "auth_provider": "github",
    "is_active": True,
}

TASK_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

# Ensure OPERATOR_ID matches test user for ownership checks
os.environ.setdefault("OPERATOR_ID", TEST_USER["id"])

# ---------------------------------------------------------------------------
# Task data factories
# ---------------------------------------------------------------------------


def _make_pending_task():
    """Task freshly created — status=pending."""
    return {
        "id": TASK_ID,
        "task_id": TASK_ID,
        "task_name": "Pipeline Integration Test Post",
        "title": "Pipeline Integration Test Post",
        "topic": "AI Content Pipelines",
        "status": "pending",
        "approval_status": None,
        "task_type": "blog_post",
        "user_id": TEST_USER["id"],
        "category": "technology",
        "target_audience": "developers",
        "primary_keyword": "content pipeline",
        "quality_score": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "task_metadata": {
            "draft_content": "# AI Content Pipelines\n\nThis is a test article about pipelines.",
            "topic": "AI Content Pipelines",
        },
        "result": None,
        "metadata": {},
        "seo_keywords": [],
        "seo_description": "A guide to AI content pipelines",
        "estimated_cost": None,
        "actual_cost": None,
        "publish_mode": "manual",
        "enforce_constraints": False,
        "priority": 0,
        "tags": [],
        "content": "",
    }


def _make_approved_task():
    """Task after approval — status=approved with result content."""
    base = _make_pending_task()
    base["status"] = "approved"
    base["approval_status"] = "approved"
    base["result"] = json.dumps({
        "draft_content": "# AI Content Pipelines\n\nThis is a test article about pipelines.",
        "topic": "AI Content Pipelines",
        "title": "AI Content Pipelines",
        "seo_description": "A guide to AI content pipelines",
        "seo_keywords": ["AI", "content", "pipeline"],
        "metadata": {"approved_at": datetime.now(timezone.utc).isoformat()},
    })
    return base


def _make_published_task():
    """Task after publishing — status=published with post metadata."""
    base = _make_approved_task()
    base["status"] = "published"
    result = json.loads(base["result"])
    result["post_id"] = "post-uuid-001"
    result["post_slug"] = "ai-content-pipelines-a1b2c3d4"
    result["published_url"] = "/posts/ai-content-pipelines-a1b2c3d4"
    result["publish_metadata"] = {
        "published_at": datetime.now(timezone.utc).isoformat(),
        "published_by": "operator",
    }
    base["result"] = json.dumps(result)
    return base


# ---------------------------------------------------------------------------
# Mock DB builder
# ---------------------------------------------------------------------------


def _make_mock_db():
    """Return AsyncMock DatabaseService with defaults for full pipeline."""
    db = AsyncMock()
    db.get_task = AsyncMock(return_value=_make_pending_task())
    db.add_task = AsyncMock(return_value=TASK_ID)
    db.update_task = AsyncMock(return_value=True)
    db.update_task_status = AsyncMock(return_value=True)
    db.delete_task = AsyncMock(return_value=True)
    db.get_tasks_paginated = AsyncMock(return_value=([_make_pending_task()], 1))
    db.log_status_change = AsyncMock(return_value=None)
    # create_post returns a mock with an .id attribute (as the real Post model does)
    mock_post = MagicMock()
    mock_post.id = "post-uuid-001"
    mock_post.get = lambda key, default=None: {"id": "post-uuid-001"}.get(key, default)
    db.create_post = AsyncMock(return_value=mock_post)
    # Pool mock for webhook emission (emit_webhook_event expects db_service.pool)
    db.pool = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# App factory — wires task_routes (which includes publishing_router)
# ---------------------------------------------------------------------------

API_TOKEN = "test-pipeline-token"


def _build_pipeline_app(mock_db=None) -> FastAPI:
    """Build minimal FastAPI app with task + publishing routes and mocked deps."""
    from routes.task_routes import router

    app = FastAPI()
    app.include_router(router)

    db = mock_db or _make_mock_db()

    # Override auth: return test user for get_current_user, token string for verify_api_token
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    app.dependency_overrides[get_current_user_optional] = lambda: TEST_USER
    app.dependency_overrides[verify_api_token] = lambda: API_TOKEN
    app.dependency_overrides[get_database_dependency] = lambda: db

    return app


# ---------------------------------------------------------------------------
# Flow 1: Full pipeline — create → get (pending) → approve → publish
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFullPipelineFlow:
    """End-to-end: create task, verify pending, approve, publish, verify post."""

    def test_create_task_returns_success_with_id(self):
        """Step 1: POST /api/tasks creates a new task and returns its ID."""
        db = _make_mock_db()
        client = TestClient(_build_pipeline_app(db), raise_server_exceptions=False)

        resp = client.post(
            "/api/tasks/",
            json={
                "task_name": "Pipeline Integration Test Post",
                "topic": "AI Content Pipelines",
                "task_type": "blog_post",
                "category": "technology",
                "target_audience": "developers",
                "primary_keyword": "content pipeline",
            },
        )
        assert resp.status_code in (200, 201), f"Create failed: {resp.status_code} {resp.text}"
        body = resp.json()
        assert "id" in body or "task_id" in body
        db.add_task.assert_awaited_once()

    def test_get_task_returns_pending_status(self):
        """Step 2: GET /api/tasks/{id} returns the task with status=pending."""
        db = _make_mock_db()
        db.get_task = AsyncMock(return_value=_make_pending_task())
        client = TestClient(_build_pipeline_app(db), raise_server_exceptions=False)

        resp = client.get(f"/api/tasks/{TASK_ID}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "pending"
        assert body["topic"] == "AI Content Pipelines"

    @patch("routes.task_publishing_routes.trigger_nextjs_revalidation", new_callable=AsyncMock, return_value=True)
    @patch(
        "services.content_router_service._select_category_for_topic",
        new_callable=AsyncMock,
        return_value="cat-uuid-001",
    )
    @patch(
        "services.content_router_service._get_or_create_default_author",
        new_callable=AsyncMock,
        return_value="author-uuid-001",
    )
    @patch("routes.task_publishing_routes.emit_webhook_event", new_callable=AsyncMock)
    def test_approve_task_returns_approved_status(
        self, mock_webhook, mock_author, mock_category, mock_revalidation
    ):
        """Step 3: POST /api/tasks/{id}/approve transitions to approved."""
        db = _make_mock_db()
        pending = _make_pending_task()
        # After approve, get_task should return approved version
        approved = _make_approved_task()
        db.get_task = AsyncMock(side_effect=[pending, approved])

        client = TestClient(_build_pipeline_app(db), raise_server_exceptions=False)

        resp = client.post(
            f"/api/tasks/{TASK_ID}/approve",
            params={"approved": True, "human_feedback": "Looks great"},
        )
        assert resp.status_code == 200, f"Approve failed: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["status"] == "approved"

    @patch("routes.task_publishing_routes.trigger_nextjs_revalidation", new_callable=AsyncMock, return_value=True)
    @patch(
        "services.content_router_service._select_category_for_topic",
        new_callable=AsyncMock,
        return_value="cat-uuid-001",
    )
    @patch(
        "services.content_router_service._get_or_create_default_author",
        new_callable=AsyncMock,
        return_value="author-uuid-001",
    )
    @patch("routes.task_publishing_routes.emit_webhook_event", new_callable=AsyncMock)
    def test_publish_task_returns_published_status(
        self, mock_webhook, mock_author, mock_category, mock_revalidation
    ):
        """Step 4: POST /api/tasks/{id}/publish transitions to published."""
        db = _make_mock_db()
        approved = _make_approved_task()
        published = _make_published_task()
        # First call: fetch for status check; second call: fetch after publish
        db.get_task = AsyncMock(side_effect=[approved, published])

        client = TestClient(_build_pipeline_app(db), raise_server_exceptions=False)

        resp = client.post(f"/api/tasks/{TASK_ID}/publish")
        assert resp.status_code == 200, f"Publish failed: {resp.status_code} {resp.text}"
        body = resp.json()
        assert body["status"] == "published"

    @patch("routes.task_publishing_routes.trigger_nextjs_revalidation", new_callable=AsyncMock, return_value=True)
    @patch(
        "services.content_router_service._select_category_for_topic",
        new_callable=AsyncMock,
        return_value="cat-uuid-001",
    )
    @patch(
        "services.content_router_service._get_or_create_default_author",
        new_callable=AsyncMock,
        return_value="author-uuid-001",
    )
    @patch("routes.task_publishing_routes.emit_webhook_event", new_callable=AsyncMock)
    def test_publish_creates_post_in_database(
        self, mock_webhook, mock_author, mock_category, mock_revalidation
    ):
        """Step 5: Publishing calls db_service.create_post to persist the post."""
        db = _make_mock_db()
        approved = _make_approved_task()
        published = _make_published_task()
        db.get_task = AsyncMock(side_effect=[approved, published])

        client = TestClient(_build_pipeline_app(db), raise_server_exceptions=False)

        resp = client.post(f"/api/tasks/{TASK_ID}/publish")
        assert resp.status_code == 200, f"Publish failed: {resp.status_code} {resp.text}"

        # Verify create_post was called with correct data
        db.create_post.assert_awaited_once()
        post_data = db.create_post.call_args[0][0]
        assert post_data["status"] == "published"
        assert post_data["title"]  # Title should be populated
        assert post_data["content"]  # Content should be populated
        assert post_data["slug"]  # Slug should be generated
        assert post_data["author_id"] == "author-uuid-001"
        assert post_data["category_id"] == "cat-uuid-001"

    @patch("routes.task_publishing_routes.trigger_nextjs_revalidation", new_callable=AsyncMock, return_value=True)
    @patch(
        "services.content_router_service._select_category_for_topic",
        new_callable=AsyncMock,
        return_value="cat-uuid-001",
    )
    @patch(
        "services.content_router_service._get_or_create_default_author",
        new_callable=AsyncMock,
        return_value="author-uuid-001",
    )
    @patch("routes.task_publishing_routes.emit_webhook_event", new_callable=AsyncMock)
    def test_full_pipeline_chained(
        self, mock_webhook, mock_author, mock_category, mock_revalidation
    ):
        """Full chained flow: create → get → approve → publish → verify post."""
        db = _make_mock_db()

        pending = _make_pending_task()
        approved = _make_approved_task()
        published = _make_published_task()

        # Sequence of get_task calls across the pipeline:
        # 1. GET /tasks/{id} — returns pending
        # 2. POST /approve — fetches task (pending), then fetches updated (approved)
        # 3. POST /publish — fetches task (approved), then fetches updated (published)
        db.get_task = AsyncMock(
            side_effect=[pending, pending, approved, approved, published]
        )

        client = TestClient(_build_pipeline_app(db), raise_server_exceptions=False)

        # Step 1: Create task
        create_resp = client.post(
            "/api/tasks/",
            json={
                "task_name": "Pipeline Integration Test Post",
                "topic": "AI Content Pipelines",
                "task_type": "blog_post",
                "category": "technology",
                "target_audience": "developers",
                "primary_keyword": "content pipeline",
            },
        )
        assert create_resp.status_code in (200, 201), (
            f"Create failed: {create_resp.status_code} {create_resp.text}"
        )
        db.add_task.assert_awaited_once()

        # Step 2: Verify pending status
        get_resp = client.get(f"/api/tasks/{TASK_ID}")
        assert get_resp.status_code == 200
        assert get_resp.json()["status"] == "pending"

        # Step 3: Approve
        approve_resp = client.post(
            f"/api/tasks/{TASK_ID}/approve",
            params={"approved": True},
        )
        assert approve_resp.status_code == 200, (
            f"Approve failed: {approve_resp.status_code} {approve_resp.text}"
        )
        assert approve_resp.json()["status"] == "approved"

        # Step 4: Publish
        publish_resp = client.post(f"/api/tasks/{TASK_ID}/publish")
        assert publish_resp.status_code == 200, (
            f"Publish failed: {publish_resp.status_code} {publish_resp.text}"
        )
        assert publish_resp.json()["status"] == "published"

        # Step 5: Verify post was created in DB
        db.create_post.assert_awaited_once()
        post_data = db.create_post.call_args[0][0]
        assert post_data["status"] == "published"
        assert "pipeline" in post_data["slug"].lower() or "ai" in post_data["slug"].lower()


# ---------------------------------------------------------------------------
# Flow 2: Cannot publish a non-approved task
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPublishRequiresApproval:
    """Publishing a task that is not approved must fail with 400."""

    def test_publish_pending_task_returns_400(self):
        db = _make_mock_db()
        db.get_task = AsyncMock(return_value=_make_pending_task())
        client = TestClient(_build_pipeline_app(db), raise_server_exceptions=False)

        resp = client.post(f"/api/tasks/{TASK_ID}/publish")
        assert resp.status_code == 400
        assert "approved" in resp.json()["detail"].lower()

    def test_publish_rejected_task_returns_400(self):
        rejected = _make_pending_task()
        rejected["status"] = "rejected"
        db = _make_mock_db()
        db.get_task = AsyncMock(return_value=rejected)
        client = TestClient(_build_pipeline_app(db), raise_server_exceptions=False)

        resp = client.post(f"/api/tasks/{TASK_ID}/publish")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Flow 3: Approve / publish nonexistent task
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestNonexistentTask:
    """Operations on a nonexistent task return 404."""

    def test_approve_nonexistent_returns_404(self):
        db = _make_mock_db()
        db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_pipeline_app(db), raise_server_exceptions=False)

        resp = client.post(
            f"/api/tasks/{TASK_ID}/approve",
            params={"approved": True},
        )
        assert resp.status_code == 404

    def test_publish_nonexistent_returns_404(self):
        db = _make_mock_db()
        db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_pipeline_app(db), raise_server_exceptions=False)

        resp = client.post(f"/api/tasks/{TASK_ID}/publish")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Flow 4: Publish populates post metadata in response
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPublishResponseMetadata:
    """Publish response includes post_id, post_slug, and published_url."""

    @patch("routes.task_publishing_routes.trigger_nextjs_revalidation", new_callable=AsyncMock, return_value=True)
    @patch(
        "services.content_router_service._select_category_for_topic",
        new_callable=AsyncMock,
        return_value="cat-uuid-001",
    )
    @patch(
        "services.content_router_service._get_or_create_default_author",
        new_callable=AsyncMock,
        return_value="author-uuid-001",
    )
    @patch("routes.task_publishing_routes.emit_webhook_event", new_callable=AsyncMock)
    def test_publish_response_has_published_url(
        self, mock_webhook, mock_author, mock_category, mock_revalidation
    ):
        db = _make_mock_db()
        approved = _make_approved_task()
        published = _make_published_task()
        db.get_task = AsyncMock(side_effect=[approved, published])

        client = TestClient(_build_pipeline_app(db), raise_server_exceptions=False)

        resp = client.post(f"/api/tasks/{TASK_ID}/publish")
        assert resp.status_code == 200
        body = resp.json()
        # The published task result should contain post info
        # Check either top-level or nested in result
        has_post_info = (
            body.get("published_url")
            or body.get("post_id")
            or body.get("post_slug")
            or (isinstance(body.get("result"), dict) and body["result"].get("published_url"))
        )
        assert has_post_info, f"Expected post metadata in response: {body}"

    @patch("routes.task_publishing_routes.trigger_nextjs_revalidation", new_callable=AsyncMock, return_value=True)
    @patch(
        "services.content_router_service._select_category_for_topic",
        new_callable=AsyncMock,
        return_value="cat-uuid-001",
    )
    @patch(
        "services.content_router_service._get_or_create_default_author",
        new_callable=AsyncMock,
        return_value="author-uuid-001",
    )
    @patch("routes.task_publishing_routes.emit_webhook_event", new_callable=AsyncMock)
    def test_publish_updates_task_status_in_db(
        self, mock_webhook, mock_author, mock_category, mock_revalidation
    ):
        """update_task_status is called with 'published'."""
        db = _make_mock_db()
        approved = _make_approved_task()
        published = _make_published_task()
        db.get_task = AsyncMock(side_effect=[approved, published])

        client = TestClient(_build_pipeline_app(db), raise_server_exceptions=False)

        resp = client.post(f"/api/tasks/{TASK_ID}/publish")
        assert resp.status_code == 200

        # Verify update_task_status was called with "published"
        db.update_task_status.assert_awaited()
        # At least one call should have "published" as the status arg
        published_calls = [
            call for call in db.update_task_status.call_args_list
            if call[0][1] == "published"
        ]
        assert len(published_calls) >= 1, (
            f"Expected update_task_status('published') call. Calls: {db.update_task_status.call_args_list}"
        )


# ---------------------------------------------------------------------------
# Flow 5: Auth enforcement on pipeline endpoints
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPipelineAuthEnforcement:
    """Without auth overrides, pipeline endpoints reject unauthenticated requests."""

    def test_create_without_auth_rejected(self):
        from routes.task_routes import router

        app = FastAPI()
        app.include_router(router)
        db = _make_mock_db()
        app.dependency_overrides[get_database_dependency] = lambda: db

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/tasks/",
            json={
                "task_name": "Unauthed Post",
                "topic": "Test",
            },
        )
        assert resp.status_code in (401, 403, 422)

    def test_approve_without_auth_rejected(self):
        from routes.task_routes import router

        app = FastAPI()
        app.include_router(router)
        db = _make_mock_db()
        app.dependency_overrides[get_database_dependency] = lambda: db

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            f"/api/tasks/{TASK_ID}/approve",
            params={"approved": True},
        )
        assert resp.status_code in (401, 403, 422)

    def test_publish_without_auth_rejected(self):
        from routes.task_routes import router

        app = FastAPI()
        app.include_router(router)
        db = _make_mock_db()
        app.dependency_overrides[get_database_dependency] = lambda: db

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(f"/api/tasks/{TASK_ID}/publish")
        assert resp.status_code in (401, 403, 422)
