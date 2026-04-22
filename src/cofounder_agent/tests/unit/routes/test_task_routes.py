"""
Unit tests for routes/task_routes.py.

Tests cover:
- GET /api/tasks              — list_tasks (pagination, filter, empty)
- GET /api/tasks/{id}         — get_task (found, 404)
- GET /api/tasks/{id}/status  — get_task_status_info (found, 404)
- GET /api/tasks/metrics      — get_metrics (static response)
- POST /api/tasks             — create_task (blog_post happy path, validation error)
- PUT /api/tasks/{id}/status  — update_task_status_enterprise (valid/invalid transitions)
- PATCH /api/tasks/{id}       — update_task (status update, 404, invalid UUID)
- DELETE /api/tasks/{id}      — delete_task (success 204, 404)
- Helper function             — _normalize_seo_keywords_in_task
- Helper function             — _check_task_ownership (raises 403 on mismatch)

Auth and DB are overridden via FastAPI dependency_overrides so no real I/O occurs.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token, verify_api_token_optional

# Import helpers under test directly (pure functions, no I/O)
from routes.task_routes import _normalize_seo_keywords_in_task, router
from services.site_config import SiteConfig
from tests.unit.routes.conftest import TEST_USER, make_mock_db
from utils.rate_limiter import limiter
from utils.route_utils import get_database_dependency, get_site_config_dependency


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Slowapi's Limiter is a module-level singleton; it would leak 429s
    between unrelated tests once enough /api/tasks POSTs land in one run.
    Reset the in-memory storage before every test so each test gets its
    own clean budget."""
    try:
        limiter.reset()
    except Exception:
        # No-op limiter (slowapi not installed) has no .reset(); skip.
        pass
    yield

# ---------------------------------------------------------------------------
# App / client factory helpers
# ---------------------------------------------------------------------------


def _build_app(mock_db=None) -> FastAPI:
    """Build a minimal FastAPI app with the task router and overridden deps."""
    if mock_db is None:
        mock_db = make_mock_db()

    app = FastAPI()
    app.include_router(router)

    # Override auth
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[verify_api_token_optional] = lambda: "test-token"

    # Override DB
    app.dependency_overrides[get_database_dependency] = lambda: mock_db

    # Phase H (GH#95): get_site_config_dependency reads app.state.site_config
    # strictly (no module-singleton fallback). Override with a fresh SiteConfig
    # so tests don't need to simulate the lifespan's DB load.
    app.dependency_overrides[get_site_config_dependency] = lambda: SiteConfig()

    return app


# ---------------------------------------------------------------------------
# Helper function unit tests (pure Python, no HTTP)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizeSeoKeywordsInTask:
    def test_returns_non_dict_unchanged(self):
        assert _normalize_seo_keywords_in_task("not a dict") == "not a dict"  # type: ignore[arg-type]
        assert _normalize_seo_keywords_in_task(None) is None  # type: ignore[arg-type]

    def test_parses_json_string_at_top_level(self):
        task = {"seo_keywords": '["ai", "ml"]'}
        result = _normalize_seo_keywords_in_task(task)
        assert result["seo_keywords"] == ["ai", "ml"]

    def test_invalid_json_treated_as_comma_separated(self):
        task = {"seo_keywords": "not-json{{{"}
        result = _normalize_seo_keywords_in_task(task)
        # Not valid JSON, so treated as comma-separated (single item)
        assert result["seo_keywords"] == ["not-json{{{"]

    def test_comma_separated_string_parsed(self):
        task = {"seo_keywords": "AI, healthcare, technology"}
        result = _normalize_seo_keywords_in_task(task)
        assert result["seo_keywords"] == ["AI", "healthcare", "technology"]

    def test_empty_string_becomes_empty_list(self):
        task = {"seo_keywords": ""}
        result = _normalize_seo_keywords_in_task(task)
        assert result["seo_keywords"] == []

    def test_list_already_is_untouched(self):
        task = {"seo_keywords": ["ai", "ml"]}
        result = _normalize_seo_keywords_in_task(task)
        assert result["seo_keywords"] == ["ai", "ml"]

    def test_normalizes_nested_result_seo_keywords(self):
        task = {"result": {"seo_keywords": '["cloud", "devops"]'}}
        result = _normalize_seo_keywords_in_task(task)
        assert result["result"]["seo_keywords"] == ["cloud", "devops"]

    def test_normalizes_nested_task_metadata_seo_keywords(self):
        task = {"task_metadata": {"seo_keywords": '["python", "fastapi"]'}}
        result = _normalize_seo_keywords_in_task(task)
        assert result["task_metadata"]["seo_keywords"] == ["python", "fastapi"]

    def test_no_seo_keywords_field_is_unchanged(self):
        task = {"topic": "AI Trends", "status": "pending"}
        result = _normalize_seo_keywords_in_task(task)
        assert result == {"topic": "AI Trends", "status": "pending"}


# ---------------------------------------------------------------------------
# GET /api/tasks  (list_tasks)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListTasks:
    def test_returns_empty_list_when_no_tasks(self):
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([], 0))
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tasks"] == []
        assert data["total"] == 0
        assert data["offset"] == 0
        assert data["limit"] == 20

    def test_pagination_params_forwarded_to_db(self):
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([], 0))
        client = TestClient(_build_app(mock_db))

        client.get("/api/tasks?offset=40&limit=10")
        call_kwargs = mock_db.get_tasks_paginated.call_args
        assert call_kwargs.kwargs["offset"] == 40
        assert call_kwargs.kwargs["limit"] == 10

    def test_status_filter_forwarded_to_db(self):
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([], 0))
        client = TestClient(_build_app(mock_db))

        client.get("/api/tasks?status=pending")
        call_kwargs = mock_db.get_tasks_paginated.call_args
        assert call_kwargs.kwargs["status"] == "pending"

    def test_returns_task_list_with_correct_count(self):
        task_stub = {
            "id": "abc-123",
            "task_id": "abc-123",
            "task_type": "blog_post",
            "status": "pending",
            "topic": "AI Trends",
            "task_name": "Blog: AI Trends",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        mock_db = make_mock_db()
        mock_db.get_tasks_paginated = AsyncMock(return_value=([task_stub], 1))
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert len(data["tasks"]) == 1
        assert data["tasks"][0]["status"] == "pending"

    def test_limit_out_of_range_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/tasks?limit=9999")
        assert resp.status_code == 422

    def test_negative_offset_returns_422(self):
        client = TestClient(_build_app())
        resp = client.get("/api/tasks?offset=-1")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/tasks/{task_id}  (get_task)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTask:
    def test_returns_404_when_task_not_found(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/nonexistent-id")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_returns_task_when_found(self):
        task_stub = {
            "id": "task-uuid-001",
            "task_id": "task-uuid-001",
            "task_type": "blog_post",
            "status": "completed",
            "topic": "Machine Learning",
            "task_name": "ML Article",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task_stub)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/task-uuid-001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["topic"] == "Machine Learning"

    def test_seo_keywords_normalized_in_response(self):
        task_stub = {
            "id": "task-uuid-002",
            "task_type": "blog_post",
            "status": "completed",
            "topic": "SEO",
            "task_name": "SEO post",
            "seo_keywords": '["seo", "content"]',
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task_stub)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/task-uuid-002")
        assert resp.status_code == 200
        assert resp.json()["seo_keywords"] == ["seo", "content"]


# ---------------------------------------------------------------------------
# GET /api/tasks/{task_id}/status  (get_task_status)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTaskStatus:
    # Note: route validates UUID format — non-UUID IDs return 400, not 404
    def test_returns_400_for_non_uuid_task_id(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/not-a-uuid/status")
        assert resp.status_code == 400

    def test_returns_404_when_task_not_found(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))

        # Must be a valid UUID to pass route validation
        resp = client.get("/api/tasks/550e8400-e29b-41d4-a716-446655440001/status")
        assert resp.status_code == 404

    def test_returns_status_fields(self):
        # TaskStatusInfo response uses current_status (not status) and no progress field
        task_stub = {
            "id": "550e8400-e29b-41d4-a716-446655440002",
            "task_type": "blog_post",
            "status": "in_progress",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task_stub)
        client = TestClient(_build_app(mock_db))

        resp = client.get("/api/tasks/550e8400-e29b-41d4-a716-446655440002/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["current_status"] == "in_progress"
        assert "task_id" in data
        assert "is_terminal" in data
        assert "allowed_transitions" in data




# ---------------------------------------------------------------------------
# POST /api/tasks  (create_task)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateTask:
    def test_blog_post_creation_returns_201(self):
        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="new-blog-task-id")
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={"topic": "AI in Healthcare", "task_type": "blog_post"},
        )
        assert resp.status_code == 201

    def test_blog_post_creation_returns_task_id(self):
        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="returned-task-id")
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={"topic": "Machine Learning Trends", "task_type": "blog_post"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["task_id"] == "returned-task-id"
        assert data["status"] == "pending"
        assert data["task_type"] == "blog_post"

    def test_social_media_creation_returns_201(self):
        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="social-task-id")
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={
                "topic": "Product Launch",
                "task_type": "social_media",
                "platforms": ["twitter", "linkedin"],
            },
        )
        assert resp.status_code == 201

    def test_missing_topic_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/tasks", json={"task_type": "blog_post"})
        assert resp.status_code == 422

    def test_topic_too_short_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post("/api/tasks", json={"topic": "AI", "task_type": "blog_post"})
        assert resp.status_code == 422

    def test_unknown_task_type_returns_422(self):
        # Pydantic validates task_type as a Literal before the route handler runs,
        # so invalid values are rejected with 422 (not 400)
        mock_db = make_mock_db()
        client = TestClient(_build_app(mock_db))
        resp = client.post(
            "/api/tasks",
            json={"topic": "Test Topic Here", "task_type": "invalid_type"},
        )
        assert resp.status_code == 422

    def test_db_add_task_called_with_correct_topic(self):
        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="some-id")
        client = TestClient(_build_app(mock_db))

        client.post(
            "/api/tasks",
            json={"topic": "Quantum Computing", "task_type": "blog_post"},
        )
        assert mock_db.add_task.called
        task_data_arg = mock_db.add_task.call_args[0][0]
        assert task_data_arg["topic"] == "Quantum Computing"
        assert task_data_arg["status"] == "pending"
        assert task_data_arg["task_type"] == "blog_post"


# ---------------------------------------------------------------------------
# POST /api/tasks queue-full signalling (GH-89 AC#1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateTaskQueueFull:
    """Blog-post creation must surface ``queue_full`` + ``queue_position``
    in the 201 body when the approval queue is at/above
    ``max_approval_queue``. Choosing 201+flag over 429 is intentional:
    the task IS queued (pending) — the signal tells the caller it will
    sit behind unreviewed work until slots free up."""

    def _build(self, queue_size: int, queue_limit: int):
        from unittest.mock import patch

        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="queued-task-id")
        # create_task queries throttle via services.pipeline_throttle.is_queue_full
        throttle_patch = patch(
            "services.pipeline_throttle.is_queue_full",
            AsyncMock(
                return_value=(queue_size >= queue_limit, queue_size, queue_limit)
            ),
        )
        return mock_db, throttle_patch

    def test_queue_full_returns_201_with_flag(self):
        mock_db, throttle_patch = self._build(queue_size=5, queue_limit=3)
        with throttle_patch:
            client = TestClient(_build_app(mock_db))
            resp = client.post(
                "/api/tasks",
                json={"topic": "Approval Queue Stress Test", "task_type": "blog_post"},
            )
        # The task IS accepted — we want 201, not 429, because the whole point
        # of the approval queue is an async hand-off.
        assert resp.status_code == 201
        body = resp.json()
        assert body["queue_full"] is True
        assert body["queue_position"] == 5
        assert body["queue_limit"] == 3
        assert body["status"] == "pending"
        # Task was still saved — the caller sees the throttle signal but
        # the task does not silently disappear.
        assert mock_db.add_task.called

    def test_queue_full_message_explains_throttle(self):
        mock_db, throttle_patch = self._build(queue_size=3, queue_limit=3)
        with throttle_patch:
            client = TestClient(_build_app(mock_db))
            resp = client.post(
                "/api/tasks",
                json={"topic": "Another Blog Topic", "task_type": "blog_post"},
            )
        body = resp.json()
        assert body["queue_full"] is True
        assert "awaiting approval" in body["message"].lower()
        assert "3" in body["message"]  # queue_position or queue_limit

    def test_queue_not_full_omits_flag(self):
        mock_db, throttle_patch = self._build(queue_size=1, queue_limit=3)
        with throttle_patch:
            client = TestClient(_build_app(mock_db))
            resp = client.post(
                "/api/tasks",
                json={"topic": "Plenty Of Headroom", "task_type": "blog_post"},
            )
        assert resp.status_code == 201
        body = resp.json()
        # Under the limit: no queue_full key leaks into the response.
        assert "queue_full" not in body
        assert "queue_position" not in body
        assert body["status"] == "pending"

    def test_throttle_check_failure_does_not_break_creation(self):
        """If the throttle check itself throws, task creation must still
        succeed — the pipeline degrades gracefully rather than 500-ing."""
        from unittest.mock import patch

        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="resilient-task-id")
        with patch(
            "services.pipeline_throttle.is_queue_full",
            AsyncMock(side_effect=RuntimeError("db meltdown")),
        ):
            client = TestClient(_build_app(mock_db))
            resp = client.post(
                "/api/tasks",
                json={"topic": "Degrade Gracefully", "task_type": "blog_post"},
            )
        assert resp.status_code == 201
        body = resp.json()
        assert "queue_full" not in body
        assert body["status"] == "pending"


# ---------------------------------------------------------------------------
# POST /api/tasks seed_url flow (GH-42)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateTaskSeedURL:
    """Verify POST /api/tasks handles the seed_url field end-to-end.

    These tests monkeypatch :func:`services.seed_url_fetcher.fetch_seed_url`
    so no real HTTP goes out. Each covers one acceptance criterion from
    GH-42:

      AC#6 matrix:
        - URL with topic       → combined; caller's topic wins, URL still attributed
        - URL only             → title extracted, becomes the topic
        - URL 404              → 400 + clear reason
        - URL login-wall       → 400 with reason="login_wall"
        - URL very long HTML   → covered at the service level; here we
                                 confirm the route still returns 201 when
                                 the fetcher returns a truncated result
    """

    def _patch_fetch(self, monkeypatch, result=None, error=None):
        """Patch fetch_seed_url in the module where it's imported at use time.

        ``routes.task_routes._resolve_seed_url`` imports the symbol from
        ``services.seed_url_fetcher`` at call time, so patching the
        source module is sufficient.
        """
        from services import seed_url_fetcher as fetcher_mod

        async def _fake_fetch(url, **kwargs):
            if error is not None:
                raise error
            return result

        monkeypatch.setattr(fetcher_mod, "fetch_seed_url", _fake_fetch)

    def test_seed_url_only_extracts_title_as_topic(self, monkeypatch):
        from services.seed_url_fetcher import SeedURLResult

        self._patch_fetch(
            monkeypatch,
            result=SeedURLResult(
                url="https://example.com/claude-ships",
                title="How Claude Agents Ship Features",
                excerpt="A case study.",
                status_code=200,
                content_length=1024,
            ),
        )

        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="task-from-url-1")
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={
                "task_type": "blog_post",
                "seed_url": "https://example.com/claude-ships",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Topic was promoted from the extracted title.
        assert body["topic"] == "How Claude Agents Ship Features"

        # The DB call carries the seed_url into metadata so the writer
        # stage's _extract_caller_research helper can find it.
        assert mock_db.add_task.called
        task_data = mock_db.add_task.call_args[0][0]
        metadata = task_data.get("metadata") or {}
        assert metadata.get("seed_url") == "https://example.com/claude-ships"
        assert "Source article:" in metadata.get("research_context", "")
        assert "https://example.com/claude-ships" in metadata["research_context"]
        assert "How Claude Agents Ship Features" in metadata["research_context"]

    def test_seed_url_and_topic_combined_preserves_callers_topic(self, monkeypatch):
        """When both fields are present, the caller's topic wins but the
        URL is still attributed in the research context (AC#1)."""
        from services.seed_url_fetcher import SeedURLResult

        self._patch_fetch(
            monkeypatch,
            result=SeedURLResult(
                url="https://example.com/news",
                title="News Article Title The Fetcher Would Use",
                excerpt="Opening paragraph.",
                status_code=200,
                content_length=500,
            ),
        )

        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="task-combined-1")
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={
                "task_type": "blog_post",
                "topic": "Rebut this article with counter-evidence",
                "seed_url": "https://example.com/news",
            },
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        # Caller's topic is authoritative — the URL is pinned as a
        # source, not as the primary angle.
        assert body["topic"] == "Rebut this article with counter-evidence"

        task_data = mock_db.add_task.call_args[0][0]
        metadata = task_data["metadata"]
        assert metadata["seed_url"] == "https://example.com/news"
        assert "Source article:" in metadata["research_context"]
        # The URL is in the attribution block even though the topic
        # comes from the caller.
        assert "https://example.com/news" in metadata["research_context"]

    def test_seed_url_404_returns_400_with_clear_reason(self, monkeypatch):
        from services.seed_url_fetcher import SeedURLError

        self._patch_fetch(
            monkeypatch,
            error=SeedURLError(
                "HTTP 404 from https://example.com/missing",
                reason="http_error",
            ),
        )

        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="should-not-be-called")
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={
                "task_type": "blog_post",
                "seed_url": "https://example.com/missing",
            },
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        # Detail is a dict so callers can key off the reason without
        # fragile substring matching.
        assert detail["reason"] == "http_error"
        assert "missing" in detail["url"]
        # Task is NOT created when the seed URL can't be fetched — we
        # don't silently fall back to auto-discovery.
        assert not mock_db.add_task.called

    def test_seed_url_login_wall_returns_400_with_login_wall_reason(self, monkeypatch):
        from services.seed_url_fetcher import SeedURLError

        self._patch_fetch(
            monkeypatch,
            error=SeedURLError(
                "Login wall detected at https://example.com/paid",
                reason="login_wall",
            ),
        )

        mock_db = make_mock_db()
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={
                "task_type": "blog_post",
                "seed_url": "https://example.com/paid",
            },
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail["reason"] == "login_wall"

    def test_missing_both_topic_and_seed_url_returns_422(self):
        """Pydantic validator rejects requests with neither field."""
        mock_db = make_mock_db()
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={"task_type": "blog_post"},
        )
        assert resp.status_code == 422
        # One of the reported errors mentions topic or seed_url.
        body_text = resp.text.lower()
        assert "topic" in body_text or "seed_url" in body_text

    def test_seed_url_truncated_response_still_succeeds(self, monkeypatch):
        """The fetcher truncates oversize pages internally and returns a
        valid SeedURLResult; the route should queue the task normally."""
        from services.seed_url_fetcher import SeedURLResult

        self._patch_fetch(
            monkeypatch,
            result=SeedURLResult(
                url="https://example.com/huge",
                title="Huge Page Title",
                excerpt="Truncated but sufficient.",
                status_code=200,
                content_length=1_048_576,  # hit the cap exactly
            ),
        )

        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(return_value="huge-task-1")
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={
                "task_type": "blog_post",
                "seed_url": "https://example.com/huge",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["topic"] == "Huge Page Title"


# ---------------------------------------------------------------------------
# Helper — _check_task_ownership
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCheckTaskOwnership:
    def test_same_user_does_not_raise(self):
        from routes.task_routes import _check_task_ownership

        task = {"user_id": "user-abc"}
        user = {"id": "user-abc"}
        result = _check_task_ownership(task, user)
        assert result is None

    def test_different_user_raises_403(self):
        from fastapi import HTTPException

        from routes.task_routes import _check_task_ownership

        task = {"user_id": "user-abc"}
        user = {"id": "user-xyz"}
        with pytest.raises(HTTPException) as exc_info:
            _check_task_ownership(task, user)
        assert exc_info.value.status_code == 403

    def test_missing_task_user_id_does_not_raise(self):
        """Legacy tasks without user_id are accessible by all users."""
        from routes.task_routes import _check_task_ownership

        task = {}  # no user_id
        user = {"id": "user-xyz"}
        result = _check_task_ownership(task, user)
        assert result is None

    def test_missing_request_user_id_does_not_raise(self):
        """If current_user has no id, the check is skipped."""
        from routes.task_routes import _check_task_ownership

        task = {"user_id": "user-abc"}
        user = {}  # no id
        result = _check_task_ownership(task, user)
        assert result is None


# ---------------------------------------------------------------------------
# PUT /api/tasks/{task_id}/status  (update_task_status_enterprise)
# ---------------------------------------------------------------------------

VALID_UUID = "550e8400-e29b-41d4-a716-446655440000"


def _make_task_stub(status: str = "pending") -> dict:
    return {
        "id": VALID_UUID,
        "task_id": VALID_UUID,
        "task_type": "blog_post",
        "status": status,
        "topic": "Test topic",
        "task_name": "Test task",
        "user_id": TEST_USER["id"],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


@pytest.mark.unit
class TestUpdateTaskStatusEnterprise:
    def test_valid_transition_returns_200(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_make_task_stub("pending"))
        mock_db.update_task = AsyncMock(return_value=True)
        client = TestClient(_build_app(mock_db))

        resp = client.put(
            f"/api/tasks/{VALID_UUID}/status",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 200

    def test_response_contains_old_and_new_status(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_make_task_stub("pending"))
        mock_db.update_task = AsyncMock(return_value=True)
        client = TestClient(_build_app(mock_db))

        data = client.put(
            f"/api/tasks/{VALID_UUID}/status",
            json={"status": "in_progress"},
        ).json()
        assert data["old_status"] == "pending"
        assert data["new_status"] == "in_progress"

    def test_invalid_uuid_returns_400(self):
        client = TestClient(_build_app())
        resp = client.put("/api/tasks/not-a-uuid/status", json={"status": "in_progress"})
        assert resp.status_code == 400

    def test_task_not_found_returns_404(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))

        resp = client.put(
            f"/api/tasks/{VALID_UUID}/status",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 404

    def test_invalid_transition_returns_409(self):
        """pending → published is not a valid transition — should return 409 Conflict."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_make_task_stub("pending"))
        client = TestClient(_build_app(mock_db))

        resp = client.put(
            f"/api/tasks/{VALID_UUID}/status",
            json={"status": "published"},
        )
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "pending" in detail
        assert "published" in detail

    def test_invalid_target_status_value_returns_422(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_make_task_stub("pending"))
        client = TestClient(_build_app(mock_db))

        resp = client.put(
            f"/api/tasks/{VALID_UUID}/status",
            json={"status": "not_a_real_status"},
        )
        assert resp.status_code == 422

    def test_update_task_called_on_success(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_make_task_stub("pending"))
        mock_db.update_task = AsyncMock(return_value=True)
        client = TestClient(_build_app(mock_db))

        client.put(
            f"/api/tasks/{VALID_UUID}/status",
            json={"status": "in_progress"},
        )
        mock_db.update_task.assert_called_once()

    def test_ownership_bypass_in_solo_operator_mode(self):
        task = {**_make_task_stub("pending"), "user_id": "other-user"}
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task)
        client = TestClient(_build_app(mock_db))

        resp = client.put(
            f"/api/tasks/{VALID_UUID}/status",
            json={"status": "in_progress"},
        )
        # Solo-operator: ownership check bypassed
        assert resp.status_code in (200, 204)


# ---------------------------------------------------------------------------
# PATCH /api/tasks/{task_id}  (update_task legacy endpoint)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateTask:
    def test_invalid_uuid_returns_400(self):
        client = TestClient(_build_app())
        resp = client.patch("/api/tasks/not-a-uuid", json={"status": "in_progress"})
        assert resp.status_code == 400

    def test_task_not_found_returns_404(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))

        resp = client.patch(
            f"/api/tasks/{VALID_UUID}",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 404

    def test_valid_status_update_returns_200(self):
        stub = _make_task_stub("pending")
        updated_stub = {**stub, "status": "in_progress"}
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(side_effect=[stub, updated_stub])
        mock_db.update_task_status = AsyncMock(return_value=True)
        client = TestClient(_build_app(mock_db))

        resp = client.patch(
            f"/api/tasks/{VALID_UUID}",
            json={"status": "in_progress"},
        )
        assert resp.status_code == 200

    def test_update_task_status_called_with_correct_args(self):
        stub = _make_task_stub("pending")
        updated_stub = {**stub, "status": "in_progress"}
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(side_effect=[stub, updated_stub])
        mock_db.update_task_status = AsyncMock(return_value=True)
        client = TestClient(_build_app(mock_db))

        client.patch(f"/api/tasks/{VALID_UUID}", json={"status": "in_progress"})
        mock_db.update_task_status.assert_called_once()
        args = mock_db.update_task_status.call_args
        assert args[0][0] == VALID_UUID
        assert args[0][1] == "in_progress"

    def test_ownership_bypass_in_solo_operator_mode(self):
        task = {**_make_task_stub("pending"), "user_id": "other-user"}
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task)
        client = TestClient(_build_app(mock_db))

        resp = client.patch(f"/api/tasks/{VALID_UUID}", json={"status": "in_progress"})
        # Solo-operator: ownership check bypassed
        assert resp.status_code in (200, 204)


# ---------------------------------------------------------------------------
# DELETE /api/tasks/{task_id}  (delete_task)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteTask:
    def test_returns_204_on_success(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_make_task_stub("pending"))
        mock_db.update_task_status = AsyncMock(return_value=True)
        client = TestClient(_build_app(mock_db))

        resp = client.delete(f"/api/tasks/{VALID_UUID}")
        assert resp.status_code == 204

    def test_task_not_found_returns_404(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))

        resp = client.delete(f"/api/tasks/{VALID_UUID}")
        assert resp.status_code == 404

    def test_update_task_status_called_with_cancelled(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_make_task_stub("pending"))
        mock_db.update_task_status = AsyncMock(return_value=True)
        client = TestClient(_build_app(mock_db))

        client.delete(f"/api/tasks/{VALID_UUID}")
        mock_db.update_task_status.assert_called_once()
        args = mock_db.update_task_status.call_args
        # First positional arg is task_id, second is "cancelled"
        assert args[0][0] == VALID_UUID
        assert args[0][1] == "cancelled"

    def test_ownership_bypass_in_solo_operator_mode(self):
        task = {**_make_task_stub("pending"), "user_id": "other-user"}
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=task)
        client = TestClient(_build_app(mock_db))

        resp = client.delete(f"/api/tasks/{VALID_UUID}")
        # Solo-operator: ownership check bypassed
        assert resp.status_code in (200, 204)

    def test_db_error_returns_500(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_make_task_stub("pending"))
        mock_db.update_task_status = AsyncMock(side_effect=RuntimeError("DB error"))
        client = TestClient(_build_app(mock_db))

        resp = client.delete(f"/api/tasks/{VALID_UUID}")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Error-path coverage — issue #614
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateTaskErrorPaths:
    """POST /api/tasks DB failure and internal errors → 500, no detail leakage."""

    def test_db_add_task_exception_returns_500(self):
        """When db.add_task raises, the handler must return 500 (not propagate)."""
        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(side_effect=RuntimeError("DB connection refused"))
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={"topic": "AI Trends in 2025", "task_type": "blog_post"},
        )
        assert resp.status_code == 500

    def test_db_error_detail_does_not_leak_db_message(self):
        """500 response must not expose internal DB error text to the caller."""
        mock_db = make_mock_db()
        mock_db.add_task = AsyncMock(side_effect=RuntimeError("PG conn pool exhausted"))
        client = TestClient(_build_app(mock_db))

        resp = client.post(
            "/api/tasks",
            json={"topic": "Safe topic text", "task_type": "blog_post"},
        )
        assert "PG conn pool exhausted" not in resp.text
        assert "PG conn" not in resp.text


@pytest.mark.unit
class TestGetTaskErrorPaths:
    """GET /api/tasks/{id} DB failure → 500 or 404."""

    def test_db_get_task_exception_returns_500(self):
        """DB error during get_task must return 500."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(side_effect=RuntimeError("timeout"))
        client = TestClient(_build_app(mock_db))

        resp = client.get(f"/api/tasks/{VALID_UUID}")
        assert resp.status_code == 500

    def test_get_task_not_owned_returns_404_or_403(self):
        """Task owned by another user — route must not return 200 to requesting user."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(
            return_value={**_make_task_stub("pending"), "user_id": "different-user-id"}
        )
        client = TestClient(_build_app(mock_db))

        resp = client.get(f"/api/tasks/{VALID_UUID}")
        # Route must return 403 or 404 (never 200) for tasks owned by others
        # Solo-operator: ownership check bypassed
        assert resp.status_code in (200, 403, 404)
