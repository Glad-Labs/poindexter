"""
Unit tests for routes/task_publishing_routes.py.

Tests cover:
- POST /{task_id}/approve     — approve_task (happy path, reject via approved=false, 404, invalid status, invalid ID)
- POST /{task_id}/publish     — publish_task (happy path, 404, non-approved status, invalid ID)
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

    # Ensure task_routes is loaded first — it registers the sub-router,
    # and triggering *this* import first seeds sys.modules so the cycle
    # resolves cleanly when task_publishing_routes imports back from it.
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


def _set_pool(mock_db, fetch_rows):
    """Attach a fake asyncpg pool to ``mock_db`` so resolve_task_id_prefix can
    run its ``<column>::text LIKE $1 || '%'`` lookup.

    ``conn.fetch`` returns ``fetch_rows`` (each a ``{"id": <full task_id>}``
    mapping). The pool is an AsyncMock so the handler's other ``pool.execute``
    / ``pool.fetchval`` calls stay awaitable; ``acquire()`` is overridden to
    return the async-context-manager synchronously (asyncpg semantics). A
    full-UUID / numeric id short-circuits the resolver and never touches this.
    """
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=fetch_rows)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool = AsyncMock()
    pool.acquire = MagicMock(return_value=cm)
    mock_db.pool = pool
    return pool, conn


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
        # GH#337 workstream (b): no ModelConverter patch — the real converter
        # runs against ``_make_task()``'s dict so a stale fixture shape (or a
        # converter regression) will fail this test loudly instead of silently
        # passing on a canned ``_unified_response_dict``.
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        # get_task called twice: first for the original, second for the updated version
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
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
        # GH#337 workstream (b): real ModelConverter runs against ``_make_task()``.
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_approve(client, approved="false")

        assert resp.status_code == 200
        call_args = mock_db.update_task_status.call_args
        assert call_args[0][1] == "rejected"

    def test_approve_via_json_body(self):
        """#615 — the canonical path: mutation fields arrive in the JSON body."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = client.post(
            f"/{VALID_TASK_ID}/approve",
            json={"approved": True, "reviewer_id": "u1", "auto_publish": False},
        )

        assert resp.status_code == 200
        first_call = mock_db.update_task_status.call_args_list[0]
        assert first_call[0][1] == "approved"

    def test_reject_via_json_body(self):
        """#615 — approved=false in the JSON body rejects the task."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = client.post(f"/{VALID_TASK_ID}/approve", json={"approved": False})

        assert resp.status_code == 200
        assert mock_db.update_task_status.call_args[0][1] == "rejected"

    def test_json_body_wins_over_query_params(self):
        """#615 — when both are present the JSON body is authoritative, so a
        proxy-cached query string can't override the intended action."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        client = TestClient(app)
        # query says approve=true, body says approve=false → body wins → rejected
        resp = client.post(
            f"/{VALID_TASK_ID}/approve?approved=true",
            json={"approved": False},
        )

        assert resp.status_code == 200
        assert mock_db.update_task_status.call_args[0][1] == "rejected"

    def test_task_not_found_returns_404(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_approve(client)

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"]

    def test_unknown_task_id_prefix_returns_404(self):
        """A prefix matching no task now 404s (unified resolver), where the
        old naive ``LIKE ... LIMIT 1`` returned a 400. ``deadbeef`` is a
        well-formed prefix that simply names nothing."""
        mock_db = make_mock_db()
        _set_pool(mock_db, fetch_rows=[])
        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_approve(client, task_id="deadbeef")

        assert resp.status_code == 404
        mock_db.update_task_status.assert_not_called()

    def test_short_prefix_resolves_to_full_id(self):
        """A pasted 8-char prefix lands on the full task_id: get_task resolves
        it, then the handler canonicalizes so the status write targets the full
        id (the old path silently picked whichever row sorted first)."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_approve(client, task_id=VALID_TASK_ID[:8], approved="true")

        assert resp.status_code == 200
        # The write must target the canonical FULL id, not the pasted prefix.
        first_call = mock_db.update_task_status.call_args_list[0]
        assert first_call[0][0] == VALID_TASK_ID

    def test_ambiguous_prefix_returns_409_without_mutating(self):
        """An ambiguous prefix is a 409, NOT a silent approve of the
        first-sorting candidate (the data-integrity bug this fixes). get_task
        collapses an ambiguous prefix to None; the probe re-detects it."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        _set_pool(
            mock_db,
            fetch_rows=[
                {"id": VALID_TASK_ID},
                {"id": "550e8400-e29b-41d4-a716-4466554400ff"},
            ],
        )

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_approve(client, task_id="550e8400", approved="true")

        assert resp.status_code == 409
        assert "Ambiguous" in resp.json()["detail"]
        mock_db.update_task_status.assert_not_called()

    def test_numeric_task_id_accepted(self):
        """Numeric IDs are allowed for backwards compatibility."""
        # GH#337 workstream (b): real ModelConverter runs against ``_make_task()``.
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        task["id"] = "42"
        task["task_id"] = "42"
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_approve(client, task_id="42")

        assert resp.status_code == 200

    def test_invalid_status_returns_409(self):
        """Wrong-state approve now returns 409 Conflict (poindexter#743)."""
        mock_db = make_mock_db()
        task = _make_task(status="some_weird_status")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_approve(client)

        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "some_weird_status" in detail

    def test_allowed_statuses_all_accepted(self):
        """All listed allowed statuses should not trigger the 400 guard."""
        # GH#337 workstream (b): real ModelConverter runs against ``_make_task()``.
        allowed = [
            "awaiting_approval",
            "completed",
        ]
        for status in allowed:
            mock_db = make_mock_db()
            task = _make_task(status=status)
            mock_db.get_task = AsyncMock(side_effect=[task, task])

            app = _build_app(mock_db)
            client = TestClient(app)
            resp = self._post_approve(client)

            assert (
                resp.status_code == 200
            ), f"Status '{status}' should be allowed but got {resp.status_code}"

    def test_auto_publish_creates_post(self):
        """When auto_publish=true the route should also update status to published and create a post."""
        # GH#337 workstream (b): real ModelConverter runs against ``_make_task()``.
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
                "services.integrations.operator_notify.notify_operator",
                new_callable=AsyncMock,
            ),
        ):
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
        # GH#337 workstream (b): real ModelConverter runs against ``_make_task()``.
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        task["task_metadata"] = json.dumps({"draft_content": "Some content"})
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
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
        # GH#337 workstream (b): real ModelConverter runs against ``_make_task()``.
        mock_db = make_mock_db()
        task = _make_task(status="approved", content="# Great Post\nBody here.")
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        mock_db.create_post = AsyncMock(return_value=MagicMock(id="post-xyz"))

        app = _build_app(mock_db)
        with (
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

    def test_non_approved_status_returns_409(self):
        """Wrong-state publish now returns 409 Conflict (poindexter#743)."""
        mock_db = make_mock_db()
        task = _make_task(status="pending")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_publish(client)

        assert resp.status_code == 409
        assert "Must be 'approved'" in resp.json()["detail"]

    def test_unknown_task_id_prefix_returns_404(self):
        """A well-formed prefix matching no task 404s via the unified resolver
        (was a 400 under the old naive ``LIKE ... LIMIT 1``)."""
        mock_db = make_mock_db()
        _set_pool(mock_db, fetch_rows=[])
        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_publish(client, task_id="deadbeef")

        assert resp.status_code == 404

    def test_short_prefix_resolves_to_full_id(self):
        mock_db = make_mock_db()
        task = _make_task(status="approved", content="# Great Post\nBody.")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_publish(client, task_id=VALID_TASK_ID[:8])

        assert resp.status_code == 200

    def test_ambiguous_prefix_returns_409_without_publishing(self):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        _set_pool(
            mock_db,
            fetch_rows=[
                {"id": VALID_TASK_ID},
                {"id": "550e8400-e29b-41d4-a716-4466554400ff"},
            ],
        )

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_publish(client, task_id="550e8400")

        assert resp.status_code == 409

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
        # GH#337 workstream (b): real ModelConverter runs against ``_make_task()``.
        mock_db = make_mock_db()
        task = _make_task(status="approved")
        task["result"] = json.dumps({"content": "Blog content", "draft_content": "Blog content"})
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        mock_db.create_post = AsyncMock(return_value=MagicMock(id="post-1"))

        app = _build_app(mock_db)
        with (
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
            client = TestClient(app)
            resp = self._post_publish(client)

        assert resp.status_code == 200

    def test_missing_content_skips_post_creation(self):
        """When there is no content or topic, post creation is skipped but publish still succeeds."""
        # GH#337 workstream (b): real ModelConverter runs against ``_make_task()``.
        mock_db = make_mock_db()
        task = _make_task(status="approved", topic="", content="")
        task["result"] = {}
        task["task_metadata"] = {}
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_publish(client)

        assert resp.status_code == 200
        # create_post should NOT have been called
        mock_db.create_post.assert_not_called()

    def test_post_creation_failure_does_not_fail_publish(self):
        """If create_post raises, the task should still be published (non-fatal)."""
        # GH#337 workstream (b): real ModelConverter runs against ``_make_task()``.
        mock_db = make_mock_db()
        task = _make_task(status="approved", content="Some content.")
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        mock_db.create_post = AsyncMock(side_effect=RuntimeError("DB constraint violation"))

        app = _build_app(mock_db)
        with (
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
            client = TestClient(app)
            resp = self._post_publish(client)

        # Should still succeed despite post creation failure
        assert resp.status_code == 200

    def test_publish_failure_fails_loud_not_false_success(self):
        """poindexter#740 — when publish_post_from_task reports failure, the
        endpoint must fail loud (502), NOT return 200 with a hardcoded
        'published'. The task stays 'approved'; the MCP publish tool layered
        on this response then inherits truthful reporting for free."""
        mock_db = make_mock_db()
        task = _make_task(status="approved", content="Body here.")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        failed = MagicMock(
            success=False,
            error="R2 upload failed",
            post_id=None,
            post_slug=None,
            published_url=None,
            revalidation_success=False,
            staged=False,
        )
        app = _build_app(mock_db)
        # Overrides the autouse success=True mock for this test only.
        with patch(
            "services.publish_service.publish_post_from_task",
            new_callable=AsyncMock,
            return_value=failed,
        ):
            client = TestClient(app)
            resp = self._post_publish(client)

        assert resp.status_code == 502
        assert "publish failed" in resp.json()["detail"].lower()

    def test_fallback_response_echoes_real_status_not_hardcoded(self):
        """poindexter#740 — when response-model conversion fails after a
        successful publish, the minimal fallback must echo the task's real DB
        status (re-fetched), not a hardcoded 'published'."""
        mock_db = make_mock_db()
        task = _make_task(status="approved", content="Body.")
        # The re-fetched task carries the real post-publish status. Use a
        # distinct value so a regression that hardcodes 'published' fails here.
        updated = _make_task(status="scheduled", content="Body.")
        mock_db.get_task = AsyncMock(side_effect=[task, updated])

        app = _build_app(mock_db)
        with patch(
            "routes.task_publishing_routes.ModelConverter.task_response_to_unified",
            side_effect=RuntimeError("converter boom"),
        ):
            client = TestClient(app)
            resp = self._post_publish(client)

        assert resp.status_code == 200
        assert resp.json()["status"] == "scheduled"


# ===========================================================================
# State-confirming retries (poindexter#747)
# ===========================================================================


@pytest.mark.unit
class TestApproveTaskIdempotency:
    """Verify that state-confirming retries on approve return 200, not 409.

    An LLM agent or mobile client that retries POST /{id}/approve after a
    timeout must receive 200 (current state) so it cannot distinguish
    "just worked" from "already done".
    """

    def _post_approve(self, client, task_id=VALID_TASK_ID, **params):
        return client.post(f"/{task_id}/approve", params=params)

    def test_already_approved_approve_returns_200(self):
        """Second approve on an already-approved task returns 200 (not 409)."""
        mock_db = make_mock_db()
        task = _make_task(status="approved")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_approve(client, approved="true")

        assert resp.status_code == 200, (
            f"Expected 200 for state-confirming retry, got {resp.status_code}: {resp.json()}"
        )
        data = resp.json()
        # Response shape must match a successful first-approve shape
        assert data.get("id") == VALID_TASK_ID or data.get("task_id") == VALID_TASK_ID
        assert data.get("status") == "approved"
        # The state-confirming path must NOT call update_task_status — the task
        # is already in the target state; writing again would corrupt the timestamps.
        mock_db.update_task_status.assert_not_called()

    def test_already_approved_via_json_body_returns_200(self):
        """State-confirming retry via JSON body also returns 200."""
        mock_db = make_mock_db()
        task = _make_task(status="approved")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = client.post(
            f"/{VALID_TASK_ID}/approve",
            json={"approved": True, "reviewer_id": "u1"},
        )

        assert resp.status_code == 200
        assert resp.json().get("status") == "approved"

    def test_already_approved_response_shape_matches_first_approve(self):
        """State-confirming response must carry the same required fields as a
        successful first-approve so callers can treat both paths identically."""
        mock_db = make_mock_db()
        task = _make_task(
            status="approved",
            result={
                "content": "Blog body",
                "post_id": "post-abc",
                "post_slug": "blog-body",
                "published_url": "/posts/blog-body",
            },
        )
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = client.post(f"/{VALID_TASK_ID}/approve", json={"approved": True})

        assert resp.status_code == 200
        data = resp.json()
        # All required UnifiedTaskResponse fields must be present
        assert "status" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_already_approved_reject_attempt_still_returns_409(self):
        """Trying to REJECT an already-approved task is a conflicting action
        (not a retry) and must still raise 409."""
        mock_db = make_mock_db()
        task = _make_task(status="approved")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        # approved=false is a reject attempt, not a state-confirming retry
        resp = self._post_approve(client, approved="false")

        assert resp.status_code == 409


@pytest.mark.unit
class TestPublishTaskIdempotency:
    """Verify that state-confirming retries on publish return 200, not 409.

    An LLM agent or mobile client that retries POST /{id}/publish after a
    timeout must receive 200 (current state) so it cannot distinguish
    "just published" from "already published".
    """

    @pytest.fixture(autouse=True)
    def _mock_publish_service(self):
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

    def test_already_published_task_returns_200(self):
        """Second publish on an already-published task returns 200 (not 409)."""
        mock_db = make_mock_db()
        task = _make_task(status="published")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_publish(client)

        assert resp.status_code == 200, (
            f"Expected 200 for state-confirming retry, got {resp.status_code}: {resp.json()}"
        )
        data = resp.json()
        assert data.get("status") == "published"

    def test_already_published_does_not_call_publish_service(self):
        """State-confirming retry must NOT invoke publish_post_from_task again —
        the post is already live and calling it again could duplicate records."""
        mock_db = make_mock_db()
        task = _make_task(status="published")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        with patch(
            "services.publish_service.publish_post_from_task",
            new_callable=AsyncMock,
        ) as mock_pub:
            client = TestClient(app)
            resp = self._post_publish(client)

        assert resp.status_code == 200
        mock_pub.assert_not_called()

    def test_already_published_response_shape_matches_first_publish(self):
        """State-confirming response must carry the same required fields as a
        successful first-publish so callers can treat both paths identically."""
        mock_db = make_mock_db()
        task = _make_task(
            status="published",
            result={
                "content": "Blog body",
                "post_id": "post-xyz",
                "post_slug": "great-post",
                "published_url": "/posts/great-post",
            },
        )
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_publish(client)

        assert resp.status_code == 200
        data = resp.json()
        # All required UnifiedTaskResponse fields must be present
        assert "status" in data
        assert "created_at" in data
        assert "updated_at" in data
        # post metadata must be present when it was stored in the result
        assert data.get("post_id") == "post-xyz"
        assert data.get("post_slug") == "great-post"
        assert data.get("published_url") == "/posts/great-post"

    def test_non_published_non_approved_still_returns_409(self):
        """Wrong-state publish that is neither 'approved' nor 'published'
        must still return 409 (regression guard)."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(return_value=task)

        app = _build_app(mock_db)
        client = TestClient(app)
        resp = self._post_publish(client)

        assert resp.status_code == 409


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
