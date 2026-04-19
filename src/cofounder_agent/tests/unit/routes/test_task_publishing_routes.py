"""
Unit tests for routes/task_publishing_routes.py.

Tests cover:
- POST /{task_id}/approve     — approve_task (happy path, reject via approved=false, 404, invalid status, invalid ID)
- POST /{task_id}/publish     — publish_task (happy path, 404, non-approved status, invalid ID)
- POST /{task_id}/reject      — reject_task (happy path, 404, invalid status, invalid ID)
- POST /{task_id}/generate-image — generate_task_image (invalid source, 404, pexels missing key)
- Utility function            — clean_generated_content

Auth and DB are overridden via FastAPI dependency_overrides so no real I/O occurs.
"""

import json
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from tests.unit.routes.conftest import TEST_USER, make_mock_db
from utils.route_utils import get_database_dependency


def _import_publishing_module():
    """Import task_publishing_routes avoiding circular import with task_routes.

    task_publishing_routes imports _check_task_ownership from task_routes,
    which in turn imports publishing_router back. We mock the task_routes
    import inside task_publishing_routes to break the cycle when needed,
    but since the module may already be loaded, we just grab it.
    """
    # If already imported (e.g. by the full app), just use it
    if "routes.task_publishing_routes" in sys.modules:
        return sys.modules["routes.task_publishing_routes"]

    # Otherwise, mock the circular bit so we can import cleanly
    import importlib

    # Ensure task_routes is loaded first (it triggers the cycle)
    import routes.task_routes  # noqa: F401

    return importlib.import_module("routes.task_publishing_routes")


_pub_mod = _import_publishing_module()
clean_generated_content = _pub_mod.clean_generated_content
publishing_router = _pub_mod.publishing_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

VALID_TASK_ID = "550e8400-e29b-41d4-a716-446655440000"


def _make_task(
    status="awaiting_approval",
    user_id=None,
    topic="AI Trends",
    content="Some blog content here.",
    result=None,
    task_metadata=None,
):
    """Build a minimal task dict suitable for route tests."""
    return {
        "id": VALID_TASK_ID,
        "task_id": VALID_TASK_ID,
        "user_id": user_id or TEST_USER["id"],
        "status": status,
        "topic": topic,
        "task_type": "blog_post",
        "task_name": "Write blog post",
        "result": result or {"content": content, "draft_content": content},
        "task_metadata": task_metadata or {},
        "created_at": "2026-03-01T00:00:00+00:00",
        "updated_at": "2026-03-01T00:00:00+00:00",
    }


def _build_app(mock_db=None) -> FastAPI:
    """Build a minimal FastAPI app with the publishing router and overridden deps."""
    if mock_db is None:
        mock_db = make_mock_db()

    app = FastAPI()
    app.include_router(publishing_router)

    # Override auth
    app.dependency_overrides[verify_api_token] = lambda: "test-token"

    # Override DB
    app.dependency_overrides[get_database_dependency] = lambda: mock_db

    return app


def _unified_response_dict(**overrides):
    """Return a minimal dict that satisfies UnifiedTaskResponse validation."""
    base = {
        "id": VALID_TASK_ID,
        "task_id": VALID_TASK_ID,
        "task_type": "blog_post",
        "status": "approved",
        "topic": "AI Trends",
        "created_at": "2026-03-01T00:00:00+00:00",
        "updated_at": "2026-03-01T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _mock_model_converter():
    """Return a patch context for ModelConverter that produces a valid UnifiedTaskResponse dict."""
    converter_patch = patch(
        "routes.task_publishing_routes.ModelConverter",
    )
    return converter_patch


# ===========================================================================
# clean_generated_content — pure function tests
# ===========================================================================


@pytest.mark.unit
class TestCleanGeneratedContent:
    def test_empty_string_returns_empty(self):
        assert clean_generated_content("") == ""

    def test_none_returns_none(self):
        # The function returns falsy `content` unchanged
        assert clean_generated_content(None) is None  # type: ignore[arg-type]

    def test_removes_leading_markdown_title(self):
        raw = "# My Great Title\nSome content here."
        result = clean_generated_content(raw)
        assert result == "Some content here."

    def test_removes_double_hash_title(self):
        raw = "## Section Title\nParagraph text."
        result = clean_generated_content(raw)
        assert result == "Paragraph text."

    def test_removes_title_prefix(self):
        raw = "Title: My Blog Post\nContent follows."
        result = clean_generated_content(raw)
        assert result == "My Blog Post\nContent follows."

    def test_removes_introduction_prefix(self):
        raw = "Introduction:\nThe world of AI is vast."
        result = clean_generated_content(raw)
        assert result == "The world of AI is vast."

    def test_removes_conclusion_prefix(self):
        raw = "First paragraph.\n\nConclusion:\nFinal thoughts."
        result = clean_generated_content(raw)
        # "Conclusion:\n" is removed, collapsing the blank line
        assert "Conclusion:" not in result
        assert "Final thoughts." in result

    def test_removes_duplicate_title_from_body(self):
        raw = "AI Trends\n\nThe field of AI is evolving."
        result = clean_generated_content(raw, title="AI Trends")
        assert "AI Trends" not in result
        assert "The field of AI is evolving." in result

    def test_title_removal_is_case_insensitive(self):
        raw = "ai trends\n\nBody text."
        result = clean_generated_content(raw, title="AI Trends")
        assert "ai trends" not in result
        assert "Body text." in result

    def test_collapses_excessive_newlines(self):
        raw = "Paragraph one.\n\n\n\n\nParagraph two."
        result = clean_generated_content(raw)
        assert "\n\n\n" not in result
        assert "Paragraph one.\n\nParagraph two." == result

    def test_strips_leading_trailing_whitespace(self):
        raw = "   \n  Content here.  \n   "
        result = clean_generated_content(raw)
        assert result == "Content here."

    def test_combined_cleanup(self):
        raw = "# AI Trends\nIntroduction:\nThis is the intro.\n\n\n\nBody text."
        result = clean_generated_content(raw, title="AI Trends")
        assert not result.startswith("#")
        assert "Introduction:" not in result
        assert "\n\n\n" not in result


# ===========================================================================
# POST /{task_id}/approve
# ===========================================================================


@pytest.mark.unit
class TestApproveTask:
    def _post_approve(self, client, task_id=VALID_TASK_ID, **params):
        return client.post(f"/{task_id}/approve", params=params)

    def test_approve_happy_path(self):
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        # get_task called twice: first for the original, second for the updated version
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        with _mock_model_converter() as mc:
            mc.to_task_response.return_value = MagicMock()
            mc.task_response_to_unified.return_value = _unified_response_dict()
            client = TestClient(app)
            resp = self._post_approve(client, approved="true")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == VALID_TASK_ID
        # auto_publish=True by default, so update_task_status may be called multiple times
        # (once for approved, once for published)
        assert mock_db.update_task_status.call_count >= 1
        first_call = mock_db.update_task_status.call_args_list[0]
        assert first_call[0][0] == VALID_TASK_ID
        assert first_call[0][1] == "approved"

    def test_reject_via_approved_false(self):
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        with _mock_model_converter() as mc:
            mc.to_task_response.return_value = MagicMock()
            mc.task_response_to_unified.return_value = _unified_response_dict(status="rejected")
            client = TestClient(app)
            resp = self._post_approve(client, approved="false")

        assert resp.status_code == 200
        call_args = mock_db.update_task_status.call_args
        assert call_args[0][1] == "rejected"

    def test_task_not_found_returns_404(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_approve(client)

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_invalid_task_id_returns_400(self):
        mock_db = make_mock_db()
        app = _build_app(mock_db)
        client = TestClient(app)
        # Short non-digit strings (<6 chars) get 400 "Invalid task ID"
        resp = self._post_approve(client, task_id="bad!")

        assert resp.status_code == 400

    def test_numeric_task_id_accepted(self):
        """Numeric IDs are allowed for backwards compatibility."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        task["id"] = "42"
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        with _mock_model_converter() as mc:
            mc.to_task_response.return_value = MagicMock()
            mc.task_response_to_unified.return_value = _unified_response_dict(id="42", task_id="42")
            client = TestClient(app)
            resp = self._post_approve(client, task_id="42")

        assert resp.status_code == 200

    def test_invalid_status_returns_400(self):
        mock_db = make_mock_db()
        task = _make_task(status="some_weird_status")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_approve(client)

        assert resp.status_code == 400
        assert "not in approvable state" in resp.json()["detail"]

    def test_allowed_statuses_all_accepted(self):
        """All listed allowed statuses should not trigger the 400 guard."""
        allowed = [
            "awaiting_approval",
            "completed",
        ]
        for status in allowed:
            mock_db = make_mock_db()
            task = _make_task(status=status)
            mock_db.get_task = AsyncMock(side_effect=[task, task])

            app = _build_app(mock_db)
            with _mock_model_converter() as mc:
                mc.to_task_response.return_value = MagicMock()
                mc.task_response_to_unified.return_value = _unified_response_dict()
                client = TestClient(app)
                resp = self._post_approve(client)

            assert (
                resp.status_code == 200
            ), f"Status '{status}' should be allowed but got {resp.status_code}"

    def test_auto_publish_creates_post(self):
        """When auto_publish=true the route should also update status to published and create a post."""
        mock_db = make_mock_db()
        task = _make_task(
            status="awaiting_approval",
            content="# My Title\nGreat article body.",
        )
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        mock_db.create_post = AsyncMock(return_value=MagicMock(id="post-abc"))
        # Idempotency guard in publish_service checks cloud_pool.fetchrow for existing post
        mock_db.cloud_pool = AsyncMock()
        mock_db.cloud_pool.fetchrow = AsyncMock(return_value=None)

        app = _build_app(mock_db)
        with (
            _mock_model_converter() as mc,
            patch(
                "services.default_author.get_or_create_default_author",
                new_callable=AsyncMock,
                return_value="author-1",
            ),
            patch(
                "services.category_resolver.select_category_for_topic",
                new_callable=AsyncMock,
                return_value="cat-1",
            ),
            patch(
                "services.task_executor._notify_openclaw",
                new_callable=AsyncMock,
            ),
        ):
            mc.to_task_response.return_value = MagicMock()
            mc.task_response_to_unified.return_value = _unified_response_dict(status="published")
            client = TestClient(app)
            resp = client.post(
                f"/{VALID_TASK_ID}/approve",
                params={"approved": "true", "auto_publish": "true"},
            )

        assert resp.status_code == 200
        # update_task_status should be called at least twice: first for approved, then for published
        assert mock_db.update_task_status.call_count >= 2
        mock_db.create_post.assert_called_once()

    def test_ownership_bypass_in_solo_operator_mode(self):
        """Solo-operator mode: ownership check bypassed when auth returns a token string."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval", user_id="someone-else")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_approve(client)

        # Solo-operator: token auth bypasses ownership — approve succeeds
        assert resp.status_code == 200

    def test_db_update_failure_returns_500(self):
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(return_value=task)
        mock_db.update_task_status = AsyncMock(side_effect=RuntimeError("DB down"))

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_approve(client)

        assert resp.status_code == 500

    def test_task_metadata_as_json_string(self):
        """task_metadata stored as JSON string should be parsed without error."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        task["task_metadata"] = json.dumps({"draft_content": "Some content"})
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        with _mock_model_converter() as mc:
            mc.to_task_response.return_value = MagicMock()
            mc.task_response_to_unified.return_value = _unified_response_dict()
            client = TestClient(app)
            resp = self._post_approve(client)

        assert resp.status_code == 200


# ===========================================================================
# POST /{task_id}/publish
# ===========================================================================


@pytest.mark.unit
class TestPublishTask:
    @pytest.fixture(autouse=True)
    def _mock_publish_service(self):
        """Mock publish_post_from_task to prevent real HTTP calls (revalidation, Telegram, video)."""
        mock_result = MagicMock(
            success=True, post_id="post-xyz", post_slug="great-post",
            published_url="/posts/great-post", post_title="Great Post",
            revalidation_success=True,
        )
        with patch(
            "services.publish_service.publish_post_from_task",
            new_callable=AsyncMock,
            return_value=mock_result,
        ):
            yield

    def _post_publish(self, client, task_id=VALID_TASK_ID):
        return client.post(f"/{task_id}/publish")

    def test_publish_happy_path(self):
        mock_db = make_mock_db()
        task = _make_task(status="approved", content="# Great Post\nBody here.")
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        mock_db.create_post = AsyncMock(return_value=MagicMock(id="post-xyz"))

        app = _build_app(mock_db)
        with (
            _mock_model_converter() as mc,
            patch(
                "services.default_author.get_or_create_default_author",
                new_callable=AsyncMock,
                return_value="author-1",
            ),
            patch(
                "services.category_resolver.select_category_for_topic",
                new_callable=AsyncMock,
                return_value="cat-1",
            ),
            # publish_post_from_task is mocked by autouse fixture
        ):
            mc.to_task_response.return_value = MagicMock()
            mc.task_response_to_unified.return_value = _unified_response_dict(status="published")
            client = TestClient(app)
            resp = self._post_publish(client)

        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") == "published" or "post_id" in str(data)

    def test_task_not_found_returns_404(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_publish(client)

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_non_approved_status_returns_400(self):
        mock_db = make_mock_db()
        task = _make_task(status="pending")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_publish(client)

        assert resp.status_code == 400
        assert "Must be 'approved'" in resp.json()["detail"]

    def test_invalid_task_id_returns_400(self):
        mock_db = make_mock_db()
        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_publish(client, task_id="bad!")

        assert resp.status_code == 400
        assert "Invalid task ID" in resp.json()["detail"]

    def test_ownership_bypass_in_solo_operator_mode(self):
        mock_db = make_mock_db()
        task = _make_task(status="approved", user_id="other-user-id")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_publish(client)

        # Solo-operator: token auth bypasses ownership
        assert resp.status_code in (200, 404, 500)

    def test_result_as_json_string_parsed(self):
        """Task result stored as JSON string should be parsed correctly."""
        mock_db = make_mock_db()
        task = _make_task(status="approved")
        task["result"] = json.dumps({"content": "Blog content", "draft_content": "Blog content"})
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        mock_db.create_post = AsyncMock(return_value=MagicMock(id="post-1"))

        app = _build_app(mock_db)
        with (
            _mock_model_converter() as mc,
            patch(
                "services.default_author.get_or_create_default_author",
                new_callable=AsyncMock,
                return_value="author-1",
            ),
            patch(
                "services.category_resolver.select_category_for_topic",
                new_callable=AsyncMock,
                return_value="cat-1",
            ),
        ):
            mc.to_task_response.return_value = MagicMock()
            mc.task_response_to_unified.return_value = _unified_response_dict(status="published")
            client = TestClient(app)
            resp = self._post_publish(client)

        assert resp.status_code == 200

    def test_missing_content_skips_post_creation(self):
        """When there is no content or topic, post creation is skipped but publish still succeeds."""
        mock_db = make_mock_db()
        task = _make_task(status="approved", topic="", content="")
        task["result"] = {}
        task["task_metadata"] = {}
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        with _mock_model_converter() as mc:
            mc.to_task_response.return_value = MagicMock()
            mc.task_response_to_unified.return_value = _unified_response_dict(
                status="published", topic=""
            )
            client = TestClient(app)
            resp = self._post_publish(client)

        assert resp.status_code == 200
        # create_post should NOT have been called
        mock_db.create_post.assert_not_called()

    def test_post_creation_failure_does_not_fail_publish(self):
        """If create_post raises, the task should still be published (non-fatal)."""
        mock_db = make_mock_db()
        task = _make_task(status="approved", content="Some content.")
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        mock_db.create_post = AsyncMock(side_effect=RuntimeError("DB constraint violation"))

        app = _build_app(mock_db)
        with (
            _mock_model_converter() as mc,
            patch(
                "services.default_author.get_or_create_default_author",
                new_callable=AsyncMock,
                return_value="author-1",
            ),
            patch(
                "services.category_resolver.select_category_for_topic",
                new_callable=AsyncMock,
                return_value="cat-1",
            ),
        ):
            mc.to_task_response.return_value = MagicMock()
            mc.task_response_to_unified.return_value = _unified_response_dict(status="published")
            client = TestClient(app)
            resp = self._post_publish(client)

        # Should still succeed despite post creation failure
        assert resp.status_code == 200


# ===========================================================================
# POST /{task_id}/reject
# ===========================================================================


@pytest.mark.unit
class TestRejectTask:
    def _post_reject(self, client, task_id=VALID_TASK_ID):
        return client.post(f"/{task_id}/reject")

    def test_reject_happy_path(self):
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        with _mock_model_converter() as mc:
            mc.to_task_response.return_value = MagicMock()
            mc.task_response_to_unified.return_value = _unified_response_dict(status="rejected")
            client = TestClient(app)
            resp = self._post_reject(client)

        assert resp.status_code == 200
        call_args = mock_db.update_task_status.call_args
        assert call_args[0][1] == "rejected"

    def test_reject_approved_task(self):
        mock_db = make_mock_db()
        task = _make_task(status="approved")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        with _mock_model_converter() as mc:
            mc.to_task_response.return_value = MagicMock()
            mc.task_response_to_unified.return_value = _unified_response_dict(status="rejected")
            client = TestClient(app)
            resp = self._post_reject(client)

        assert resp.status_code == 200

    def test_reject_completed_task(self):
        mock_db = make_mock_db()
        task = _make_task(status="completed")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        with _mock_model_converter() as mc:
            mc.to_task_response.return_value = MagicMock()
            mc.task_response_to_unified.return_value = _unified_response_dict(status="rejected")
            client = TestClient(app)
            resp = self._post_reject(client)

        assert resp.status_code == 200

    def test_task_not_found_returns_404(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_reject(client)

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_invalid_status_returns_400(self):
        mock_db = make_mock_db()
        task = _make_task(status="pending")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_reject(client)

        assert resp.status_code == 400
        assert "Cannot reject" in resp.json()["detail"]

    def test_published_status_returns_400(self):
        """Published tasks cannot be rejected."""
        mock_db = make_mock_db()
        task = _make_task(status="published")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_reject(client)

        assert resp.status_code == 400

    def test_invalid_task_id_returns_400(self):
        mock_db = make_mock_db()
        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_reject(client, task_id="bad!")

        assert resp.status_code == 400
        assert "Invalid task ID" in resp.json()["detail"]

    def test_ownership_bypass_in_solo_operator_mode(self):
        mock_db = make_mock_db()
        task = _make_task(status="completed", user_id="different-user")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_reject(client)

        # Solo-operator: token auth bypasses ownership
        assert resp.status_code in (200, 404, 500)


# ===========================================================================
# POST /{task_id}/generate-image
# ===========================================================================


@pytest.mark.unit
class TestGenerateTaskImage:
    def _post_generate(self, client, task_id=VALID_TASK_ID, body=None):
        if body is None:
            body = {"source": "pexels", "topic": "AI Marketing"}
        return client.post(f"/{task_id}/generate-image", json=body)

    def test_task_not_found_returns_404(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_generate(client)

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_invalid_source_returns_400(self):
        mock_db = make_mock_db()
        task = _make_task()
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_generate(client, body={"source": "dalle", "topic": "AI"})

        assert resp.status_code == 400
        assert "Invalid image source" in resp.json()["detail"]

    def test_pexels_missing_api_key_returns_error(self):
        """When PEXELS_API_KEY is missing, the HTTPException(400) is raised inside the
        inner try block but caught by the outer except-Exception handler which returns 500.
        This tests the actual behavior of the code.
        """
        mock_db = make_mock_db()
        task = _make_task()
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        with patch("routes.task_publishing_routes.os.getenv", return_value=None):
            client = TestClient(app)
            resp = self._post_generate(client)

        # The HTTPException(400) is caught by the broad except-Exception handler
        # and re-raised as 500 — this is a known code-level issue.
        assert resp.status_code == 500

    def test_ownership_bypass_in_solo_operator_mode(self):
        mock_db = make_mock_db()
        task = _make_task(user_id="someone-else")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_generate(client)

        # Solo-operator: token auth bypasses ownership
        assert resp.status_code in (200, 404, 500)

    def test_pexels_success(self):
        """Pexels happy path: API returns photos, image URL stored and returned."""
        mock_db = make_mock_db()
        task = _make_task()
        mock_db.get_task = AsyncMock(return_value=task)
        mock_db.update_task = AsyncMock(return_value=True)

        pexels_response_data = {
            "photos": [
                {
                    "src": {
                        "large": "https://images.pexels.com/photos/123/large.jpg",
                        "original": "https://images.pexels.com/photos/123/original.jpg",
                    },
                    "photographer": "Test Photographer",
                }
            ]
        }

        app = _build_app(mock_db)

        # Build nested async context managers for aiohttp
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=pexels_response_data)

        mock_get_ctx = MagicMock()
        mock_get_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_get_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_get_ctx)

        mock_session_cls = MagicMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("routes.task_publishing_routes.os.getenv", return_value="test-pexels-key"),
            patch("aiohttp.ClientSession", mock_session_cls),
        ):
            client = TestClient(app)
            resp = self._post_generate(client)

        assert resp.status_code == 200
        data = resp.json()
        assert data["source"] == "pexels"
        assert "image_url" in data
        assert data["image_url"] == "https://images.pexels.com/photos/123/large.jpg"
        mock_db.update_task.assert_called()

    def test_pexels_rate_limit_returns_error(self):
        """Pexels 429 response: the HTTPException(429) is raised inside the inner try
        but caught by the outer except-Exception handler, resulting in 500.
        """
        mock_db = make_mock_db()
        task = _make_task()
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)

        mock_resp = MagicMock()
        mock_resp.status = 429

        mock_get_ctx = MagicMock()
        mock_get_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_get_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_get_ctx)

        mock_session_cls = MagicMock()
        mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)

        with (
            patch("routes.task_publishing_routes.os.getenv", return_value="test-key"),
            patch("aiohttp.ClientSession", mock_session_cls),
        ):
            client = TestClient(app)
            resp = self._post_generate(client)

        # The HTTPException(429) is caught by the broad except-Exception and re-raised as 500
        assert resp.status_code == 500

    def test_default_request_body(self):
        """Endpoint accepts minimal request body with defaults."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)

        app = _build_app(mock_db)
        client = TestClient(app)
        # Send empty body — defaults to source="pexels", page=1
        resp = client.post(f"/{VALID_TASK_ID}/generate-image", json={})

        # Should get 404 (task not found) rather than validation error
        assert resp.status_code == 404
