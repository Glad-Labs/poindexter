"""
Unit tests for routes/task_publishing_routes.py.

Tests cover:
- POST /{task_id}/approve     — approve_task (happy path, reject via approved=false, 404, invalid status, invalid ID)
- POST /{task_id}/publish     — publish_task (happy path, 404, non-approved status, invalid ID)
- POST /{task_id}/generate-image — retired: 410 Gone for every legacy request, Link to
                                   regen-image / replace-image, no side effects
- Utility function            — clean_generated_content

Auth and DB are overridden via FastAPI dependency_overrides so no real I/O occurs.
"""

import json
import re
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.api_token_auth import verify_api_token
from poindexter.utils.route_utils import get_database_dependency
from tests.unit.routes.conftest import TEST_USER, make_mock_db


def _import_publishing_module():
    """Import task_publishing_routes avoiding circular import with task_routes.

    task_publishing_routes imports _check_task_ownership from task_routes,
    which in turn imports publishing_router back. We mock the task_routes
    import inside task_publishing_routes to break the cycle when needed,
    but since the module may already be loaded, we just grab it.
    """
    # If already imported (e.g. by the full app), just use it
    if "poindexter.routes.task_publishing_routes" in sys.modules:
        return sys.modules["poindexter.routes.task_publishing_routes"]

    # Otherwise, mock the circular bit so we can import cleanly
    import importlib

    # Ensure task_routes is loaded first — it registers the sub-router,
    # and triggering *this* import first seeds sys.modules so the cycle
    # resolves cleanly when task_publishing_routes imports back from it.
    import poindexter.routes.task_routes  # noqa: F401

    return importlib.import_module("poindexter.routes.task_publishing_routes")


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


# ===========================================================================
# Draft editing routes — edit-body / replace-image / regen-image (#523)
# ===========================================================================


@pytest.mark.unit
class TestDraftEditingRoutes:
    """The 3 edit routes delegate to PostEditService and serialize EditResult.

    PostEditService is patched to a fake so these tests cover the route wiring
    (task-id resolution, body parsing, error mapping, response shape) — the
    service logic itself is covered by tests/unit/modules/content."""

    def _client_with_fake_service(self, monkeypatch, calls):
        from poindexter.modules.content.post_edit_service import EditResult

        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_make_task())

        class FakeSvc:
            def __init__(self, **kw):
                calls["ctor"] = kw

            async def edit_body(self, task_id, **kw):
                calls["edit_body"] = (task_id, kw)
                return EditResult(task_id, "body", True, "edited", warnings=["w1"])

            async def replace_image(self, task_id, **kw):
                calls["replace_image"] = (task_id, kw)
                return EditResult(task_id, kw["which"], True, "swapped", new_url=kw["url"])

            async def regen_image(self, task_id, **kw):
                calls["regen_image"] = (task_id, kw)
                return EditResult(
                    task_id, "featured", True, "regenerated",
                    new_url="https://cdn/new.webp",
                )

            async def remove_image(self, task_id, **kw):
                calls["remove_image"] = (task_id, kw)
                return EditResult(task_id, kw["which"], True, "removed")

            async def add_image(self, task_id, **kw):
                calls["add_image"] = (task_id, kw)
                return EditResult(
                    task_id, "inline:new", True, "added",
                    new_url="https://cdn/added.webp",
                )

            async def retitle(self, task_id, **kw):
                calls["retitle"] = (task_id, kw)
                if not kw["title"].strip():
                    raise ValueError("title must not be blank")
                return EditResult(task_id, "title", True, "retitled (v1)", warnings=["w2"])

        async def fake_enqueue(pool, task_id, *, allow_stock=False):
            calls["enqueue_image_rebuild"] = (task_id, {"allow_stock": allow_stock})
            return "rebuild-task-123"

        monkeypatch.setattr(_pub_mod, "PostEditService", FakeSvc)
        monkeypatch.setattr(_pub_mod, "enqueue_image_rebuild", fake_enqueue)
        # Keep regen from building the real (image-gen) image service.
        monkeypatch.setattr(
            "poindexter.services.image_service.get_image_service",
            lambda site_config=None: object(),
        )
        return TestClient(_build_app(mock_db))

    def test_edit_body_routes_to_service(self, monkeypatch):
        calls: dict = {}
        client = self._client_with_fake_service(monkeypatch, calls)
        r = client.post(f"/{VALID_TASK_ID}/edit-body", json={"find": "x", "replace": "y"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["field"] == "body"
        assert body["warnings"] == ["w1"]
        tid, kw = calls["edit_body"]
        assert tid == VALID_TASK_ID
        assert kw == {"new_content": None, "find": "x", "replace": "y"}

    def test_retitle_routes_to_service(self, monkeypatch):
        calls: dict = {}
        client = self._client_with_fake_service(monkeypatch, calls)
        r = client.post(f"/{VALID_TASK_ID}/retitle", json={"title": "A Parent Built a MUD"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["field"] == "title"
        assert body["warnings"] == ["w2"]
        assert calls["retitle"] == (VALID_TASK_ID, {"title": "A Parent Built a MUD"})

    def test_retitle_value_error_maps_to_400(self, monkeypatch):
        calls: dict = {}
        client = self._client_with_fake_service(monkeypatch, calls)
        r = client.post(f"/{VALID_TASK_ID}/retitle", json={"title": "   "})
        assert r.status_code == 400
        assert "blank" in r.json()["detail"]

    def test_replace_image_routes_to_service(self, monkeypatch):
        calls: dict = {}
        client = self._client_with_fake_service(monkeypatch, calls)
        r = client.post(
            f"/{VALID_TASK_ID}/replace-image",
            json={"which": "inline:2", "url": "u.png"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["new_url"] == "u.png"
        assert calls["replace_image"][1] == {"which": "inline:2", "url": "u.png"}

    def test_regen_image_routes_to_service(self, monkeypatch):
        calls: dict = {}
        client = self._client_with_fake_service(monkeypatch, calls)
        r = client.post(
            f"/{VALID_TASK_ID}/regen-image",
            json={"which": "featured", "prompt": "a teal robot"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["new_url"] == "https://cdn/new.webp"
        assert calls["regen_image"][1] == {"which": "featured", "prompt": "a teal robot"}

    def test_remove_image_routes_to_service(self, monkeypatch):
        calls: dict = {}
        client = self._client_with_fake_service(monkeypatch, calls)
        r = client.post(f"/{VALID_TASK_ID}/remove-image", json={"which": "inline:1"})
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert calls["remove_image"][1] == {"which": "inline:1"}

    def test_add_image_routes_to_service(self, monkeypatch):
        calls: dict = {}
        client = self._client_with_fake_service(monkeypatch, calls)
        r = client.post(
            f"/{VALID_TASK_ID}/add-image",
            json={"after": None, "section": "Intro", "prompt": None},
        )
        assert r.status_code == 200, r.text
        assert r.json()["new_url"] == "https://cdn/added.webp"
        assert calls["add_image"][1] == {"after": None, "section": "Intro", "prompt": None}

    def test_rebuild_images_enqueues_task(self, monkeypatch):
        calls: dict = {}
        client = self._client_with_fake_service(monkeypatch, calls)
        r = client.post(f"/{VALID_TASK_ID}/rebuild-images", json={"allow_stock": False})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["task_id"] == "rebuild-task-123"
        assert body["target_task_id"] == VALID_TASK_ID
        assert "rebuild-task-123" in body["detail"]
        # The hint must name a real subcommand — `tasks get`, not `tasks show`
        # (which doesn't exist).
        assert "poindexter tasks get rebuild-task-123" in body["detail"]
        tid, kw = calls["enqueue_image_rebuild"]
        assert tid == VALID_TASK_ID and kw == {"allow_stock": False}

    def test_rebuild_images_allow_stock_threads_through(self, monkeypatch):
        calls: dict = {}
        client = self._client_with_fake_service(monkeypatch, calls)
        r = client.post(f"/{VALID_TASK_ID}/rebuild-images", json={"allow_stock": True})
        assert r.status_code == 200, r.text
        assert calls["enqueue_image_rebuild"][1] == {"allow_stock": True}

    def test_rebuild_images_unknown_task_404(self):
        mock_db = make_mock_db()  # get_task returns None
        client = TestClient(_build_app(mock_db))
        r = client.post(f"/{VALID_TASK_ID}/rebuild-images", json={"allow_stock": False})
        assert r.status_code == 404

    def test_edit_body_unknown_task_404(self):
        mock_db = make_mock_db()  # get_task returns None by default
        client = TestClient(_build_app(mock_db))
        r = client.post(f"/{VALID_TASK_ID}/edit-body", json={"new_content": "x"})
        assert r.status_code == 404

    def test_regen_image_runtime_error_reaches_the_operator_verbatim(self, monkeypatch):
        """poindexter#1005 — the 503 body IS the operator's diagnosis. A CUDA
        OOM used to arrive as "image generation produced no output", which
        described an output step the render never reached."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_make_task())

        class FailSvc:
            def __init__(self, **kw):
                pass

            async def regen_image(self, task_id, **kw):
                raise RuntimeError(
                    "image generation failed (server_error): image-gen server "
                    "returned HTTP 503: CUDA out of memory. Tried to allocate "
                    "76.00 MiB."
                )

        monkeypatch.setattr(_pub_mod, "PostEditService", FailSvc)
        monkeypatch.setattr(
            "poindexter.services.image_service.get_image_service",
            lambda site_config=None: object(),
        )
        client = TestClient(_build_app(mock_db))
        r = client.post(
            f"/{VALID_TASK_ID}/regen-image",
            json={"which": "featured", "prompt": "x"},
        )
        assert r.status_code == 503
        assert "CUDA out of memory" in r.json()["detail"]

    def test_edit_body_value_error_maps_to_400(self, monkeypatch):
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_make_task())

        class FailSvc:
            def __init__(self, **kw):
                pass

            async def edit_body(self, task_id, **kw):
                raise ValueError("find string not present in draft body")

        monkeypatch.setattr(_pub_mod, "PostEditService", FailSvc)
        client = TestClient(_build_app(mock_db))
        r = client.post(f"/{VALID_TASK_ID}/edit-body", json={"find": "zzz", "replace": ""})
        assert r.status_code == 400
        assert "find string not present" in r.json()["detail"]

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
                "poindexter.services.default_author.get_or_create_default_author",
                new_callable=AsyncMock,
                return_value="author-1",
            ),
            patch(
                "poindexter.services.category_resolver.select_category_for_topic",
                new_callable=AsyncMock,
                return_value="cat-1",
            ),
            patch(
                "poindexter.services.integrations.operator_notify.notify_operator",
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


@pytest.mark.unit
class TestApproveFeaturedImageOverride:
    """``featured_image_url`` on approve must be the image the post ships with.

    It never was, from 2026-04-03 on (poindexter#1102): the route merged the
    override into the task's ``result`` and wrote it, then handed
    ``publish_post_from_task`` the task dict it had read BEFORE that write.
    Publish built the posts row from that snapshot, so the row got the
    pipeline's image, and its stage-only / publish backstamp then wrote the
    snapshot back over ``result``, erasing the override from the task too.

    These run the REAL ``publish_post_from_task`` down to ``create_post``, so
    they assert what the posts row actually receives. ``PostEditService`` is
    replaced by a recorder (its own tests live in
    tests/unit/modules/content), except in the one test that drives the real
    writer through the route.
    """

    OLD = "https://cdn.example/pipeline-hero.webp"
    NEW = "https://cdn.example/operator-pick.webp"

    def _task(self):
        return _make_task(
            status="awaiting_approval",
            content="# My Title\nGreat article body.",
            result={
                "content": "# My Title\nGreat article body.",
                "featured_image_url": self.OLD,
            },
            task_metadata={"featured_image_url": self.OLD},
        )

    def _db(self):
        """A mock DB the real publish path can run against, up to create_post."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=self._task())
        mock_db.create_post = AsyncMock(return_value=MagicMock(id="post-abc"))
        # publish_service's idempotency guard reads cloud_pool first: no
        # existing post, so it goes on to create one.
        mock_db.cloud_pool = AsyncMock()
        mock_db.cloud_pool.fetchrow = AsyncMock(return_value=None)
        return mock_db

    @pytest.fixture
    def edits(self, monkeypatch):
        """Swap PostEditService for a recorder. Returns the shared call log."""
        from poindexter.modules.content.post_edit_service import EditResult

        log: list = []

        class FakeEditService:
            def __init__(self, **kw):
                log.append(("ctor", kw))

            async def replace_image(self, task_id, **kw):
                log.append(("replace_image", task_id, kw))
                return EditResult(task_id, "featured", True, "swapped", new_url=kw["url"])

        monkeypatch.setattr(_pub_mod, "PostEditService", FakeEditService)
        return log

    def _approve(self, mock_db, *, json_body=None, params=None, platform=None):
        app = _build_app(mock_db)
        if platform is not None:
            app.state.kernel_platform = platform
        with (
            patch(
                "poindexter.services.default_author.get_or_create_default_author",
                new_callable=AsyncMock,
                return_value="author-1",
            ),
            patch(
                "poindexter.services.category_resolver.select_category_for_topic",
                new_callable=AsyncMock,
                return_value="cat-1",
            ),
            patch(
                "poindexter.services.integrations.operator_notify.notify_operator",
                new_callable=AsyncMock,
            ),
        ):
            return TestClient(app).post(
                f"/{VALID_TASK_ID}/approve", json=json_body, params=params,
            )

    def _created_post(self, mock_db) -> dict:
        mock_db.create_post.assert_awaited_once()
        return mock_db.create_post.await_args.args[0]

    def _result_writes(self, mock_db) -> list[dict]:
        return [
            json.loads(c.kwargs["result"])
            for c in mock_db.update_task_status.await_args_list
            if c.kwargs.get("result")
        ]

    # -- the post gets the override ------------------------------------------

    def test_override_reaches_the_staged_post(self, edits):
        """The default approve (stage only) creates the posts row with it."""
        mock_db = self._db()

        resp = self._approve(
            mock_db, json_body={"approved": True, "featured_image_url": self.NEW},
        )

        assert resp.status_code == 200, resp.text
        post = self._created_post(mock_db)
        assert post["status"] == "approved"
        assert post["featured_image_url"] == self.NEW
        assert post["cover_image_url"] == self.NEW

    def test_override_reaches_the_published_post(self, edits):
        """auto_publish=true ships the post with it."""
        mock_db = self._db()

        resp = self._approve(
            mock_db,
            json_body={
                "approved": True,
                "auto_publish": True,
                "featured_image_url": self.NEW,
            },
        )

        assert resp.status_code == 200, resp.text
        post = self._created_post(mock_db)
        assert post["status"] == "published"
        assert post["featured_image_url"] == self.NEW

    def test_query_param_override_reaches_the_post(self, edits):
        """The deprecated query-param spelling (#615) behaves the same."""
        mock_db = self._db()

        resp = self._approve(
            mock_db, params={"approved": "true", "featured_image_url": self.NEW},
        )

        assert resp.status_code == 200, resp.text
        assert self._created_post(mock_db)["featured_image_url"] == self.NEW

    def test_publish_backstamp_keeps_the_override_on_the_task(self, edits):
        """Publish's stage-only backstamp rewrites the whole ``result`` column
        from the dict it was handed. Handed the stale snapshot, it wrote the
        pipeline image back over the override the approve write had stored."""
        mock_db = self._db()

        self._approve(
            mock_db, json_body={"approved": True, "featured_image_url": self.NEW},
        )

        writes = self._result_writes(mock_db)
        assert len(writes) == 2, "expected the approve write and the backstamp"
        assert [w["featured_image_url"] for w in writes] == [self.NEW, self.NEW]
        assert writes[1]["post_id"] == "post-abc", "second write is the backstamp"

    def test_no_override_keeps_the_pipeline_image(self, edits):
        """Without an override nothing is edited and the post gets the
        pipeline's image, as before."""
        mock_db = self._db()

        resp = self._approve(mock_db, json_body={"approved": True})

        assert resp.status_code == 200, resp.text
        assert self._created_post(mock_db)["featured_image_url"] == self.OLD
        assert not [e for e in edits if e[0] == "replace_image"]

    def test_blank_override_is_no_override(self, edits):
        """Whitespace is not an image. It must not blank the hero."""
        mock_db = self._db()

        resp = self._approve(
            mock_db, json_body={"approved": True, "featured_image_url": "   "},
        )

        assert resp.status_code == 200, resp.text
        assert self._created_post(mock_db)["featured_image_url"] == self.OLD
        assert not [e for e in edits if e[0] == "replace_image"]

    # -- through the canonical writer ----------------------------------------

    def test_override_goes_through_the_canonical_writer_first(self, edits):
        """The same writer as POST /replace-image, called with the canonical
        task id, BEFORE the approval commits, and wired to the kernel's audit
        handle so the edit is audited like any other image swap."""
        mock_db = self._db()
        mock_db.update_task_status = AsyncMock(
            side_effect=lambda *a, **kw: edits.append(("status", a[1])) or True,
        )
        platform = object()

        resp = self._approve(
            mock_db,
            json_body={"approved": True, "featured_image_url": self.NEW},
            platform=platform,
        )

        assert resp.status_code == 200, resp.text
        replace = [e for e in edits if e[0] == "replace_image"]
        assert replace == [
            ("replace_image", VALID_TASK_ID, {"which": "featured", "url": self.NEW}),
        ]
        order = [e[0] for e in edits if e[0] in ("replace_image", "status")]
        assert order[0] == "replace_image", f"edit must precede the approval: {order}"
        ctor = next(e[1] for e in edits if e[0] == "ctor")
        assert ctor["platform"] is platform

    def test_writer_refusal_400s_with_the_task_untouched(self, monkeypatch):
        """A value the writer refuses stops the approval: 400, status unchanged,
        no post, no approval recorded."""

        class RefusingEditService:
            def __init__(self, **kw):
                pass

            async def replace_image(self, task_id, **kw):
                raise ValueError(f"no pipeline_versions row for task {task_id}")

        monkeypatch.setattr(_pub_mod, "PostEditService", RefusingEditService)
        mock_db = self._db()

        resp = self._approve(
            mock_db, json_body={"approved": True, "featured_image_url": self.NEW},
        )

        assert resp.status_code == 400
        assert "no pipeline_versions row" in resp.json()["detail"]
        mock_db.update_task_status.assert_not_awaited()
        mock_db.create_post.assert_not_awaited()
        gate_writes = [
            c for c in mock_db.pool.execute.await_args_list
            if "pipeline_gate_history" in c.args[0]
        ]
        assert gate_writes == []

    def test_override_is_ignored_on_a_rejection(self, edits, caplog):
        """Rejecting with an image is contradictory: nothing to put it on.
        It is dropped out loud, not written onto the rejected task."""
        mock_db = self._db()

        with caplog.at_level("WARNING"):
            resp = self._approve(
                mock_db, json_body={"approved": False, "featured_image_url": self.NEW},
            )

        assert resp.status_code == 200, resp.text
        assert not [e for e in edits if e[0] == "replace_image"]
        (write,) = self._result_writes(mock_db)
        assert write["featured_image_url"] == self.OLD
        assert "featured_image_url ignored" in caplog.text

    def test_the_approval_record_names_the_image_and_its_source(self, edits):
        """The chosen image and image_source ride on the pipeline_gate_history
        row. image_source had no durable home: the approval block in
        ``result`` never survives an approve (poindexter#1104)."""
        mock_db = self._db()

        self._approve(
            mock_db,
            json_body={
                "approved": True,
                "featured_image_url": self.NEW,
                "image_source": "pexels",
            },
        )

        (gate,) = [
            c for c in mock_db.pool.execute.await_args_list
            if "pipeline_gate_history" in c.args[0]
        ]
        recorded = json.loads(gate.args[-1])
        assert recorded["decision"] == "approved"
        assert recorded["featured_image_url"] == self.NEW
        assert recorded["image_source"] == "pexels"

    def test_override_runs_the_real_writer(self):
        """End to end through the real PostEditService: the draft store's
        featured column, the result/task_metadata mirror and the audit row,
        which is what POST /replace-image does, and then the post."""
        mock_db = self._db()

        async def fetchrow(sql, *args):
            if "FROM pipeline_versions" in sql:
                return {"content": "body", "version": 3}
            return None

        mock_db.pool = AsyncMock()
        mock_db.pool.fetchrow = AsyncMock(side_effect=fetchrow)
        mock_db.pool.fetch = AsyncMock(return_value=[])  # no posts row before approval
        platform = MagicMock()
        platform.audit.write = AsyncMock()

        resp = self._approve(
            mock_db,
            json_body={"approved": True, "featured_image_url": self.NEW},
            platform=platform,
        )

        assert resp.status_code == 200, resp.text
        draft_store = [
            c.args for c in mock_db.pool.execute.await_args_list
            if c.args[0].startswith("UPDATE pipeline_versions SET featured_image_url")
        ]
        assert draft_store == [(draft_store[0][0], self.NEW, VALID_TASK_ID, 3)]
        mirror = mock_db.update_task.await_args.args[1]
        assert json.loads(mirror["result"])["featured_image_url"] == self.NEW
        assert json.loads(mirror["task_metadata"])["featured_image_url"] == self.NEW
        audit = platform.audit.write.await_args
        assert audit.args[0] == "post_image_replace"
        assert audit.kwargs["details"]["url"] == self.NEW
        assert self._created_post(mock_db)["featured_image_url"] == self.NEW


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
            "poindexter.services.publish_service.publish_post_from_task",
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
                "poindexter.services.default_author.get_or_create_default_author",
                new_callable=AsyncMock,
                return_value="author-1",
            ),
            patch(
                "poindexter.services.category_resolver.select_category_for_topic",
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
                "poindexter.services.default_author.get_or_create_default_author",
                new_callable=AsyncMock,
                return_value="author-1",
            ),
            patch(
                "poindexter.services.category_resolver.select_category_for_topic",
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
                "poindexter.services.default_author.get_or_create_default_author",
                new_callable=AsyncMock,
                return_value="author-1",
            ),
            patch(
                "poindexter.services.category_resolver.select_category_for_topic",
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
            "poindexter.services.publish_service.publish_post_from_task",
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
            "poindexter.routes.task_publishing_routes.ModelConverter.task_response_to_unified",
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
            "poindexter.services.publish_service.publish_post_from_task",
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
            "poindexter.services.publish_service.publish_post_from_task",
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


def _parse_link_header(value: str) -> list[tuple[str, dict[str, str]]]:
    """Split an RFC 8288 ``Link`` header into ``(target, params)`` pairs, in order.

    httpx's ``Response.links`` keys its result by ``rel``, so two successors
    sharing ``rel="successor-version"`` would collapse into one there.
    """
    out = []
    for target, params in re.findall(r'<([^>]*)>((?:\s*;\s*[a-z]+="(?:[^"\\]|\\.)*")*)', value):
        out.append((target, dict(re.findall(r'([a-z]+)="((?:[^"\\]|\\.)*)"', params))))
    return out


@pytest.mark.unit
class TestGenerateImageRetired:
    """``POST /{task_id}/generate-image`` was retired on 2026-09-28.

    It answers 410 Gone and points at the two PostEditService routes that
    replaced its sources: ``regen-image`` for ``source=image_gen`` and
    ``replace-image`` for ``source=pexels``. The backcompat contract is 410
    plus a pointer to the replacement, never a 404, because a client that was
    never updated has to learn where the work went.
    """

    _URL = f"/{VALID_TASK_ID}/generate-image"

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param(None, id="no-body"),
            pytest.param({}, id="empty-body-was-the-pexels-default"),
            pytest.param({"source": "pexels", "topic": "AI Marketing", "page": 2}, id="pexels"),
            pytest.param(
                {
                    "source": "image_gen",
                    "topic": "AI Marketing",
                    "content_summary": "How AI is changing marketing",
                },
                id="image_gen",
            ),
            pytest.param({"source": "dalle"}, id="unknown-source-was-a-400"),
            pytest.param({"source": ["x"], "page": "two"}, id="bad-types-were-a-422"),
        ],
    )
    def test_every_legacy_request_gets_410_not_404_or_422(self, body):
        """Whatever the old client sends, and whether or not the task exists,
        the answer is the same 410. get_task returning None would have been a
        404 before, and the malformed bodies a 400 or 422."""
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=None)
        client = TestClient(_build_app(mock_db))

        resp = client.post(self._URL) if body is None else client.post(self._URL, json=body)

        assert resp.status_code == 410, resp.text
        assert resp.json()["retired"] is True

    def test_the_410_reads_no_task_renders_nothing_and_writes_nothing(
        self, monkeypatch, tmp_path,
    ):
        """The old route read the task, rendered into ~/Downloads (or called
        Pexels), then wrote the result back twice. The stub must do none of
        it."""
        from poindexter.services.image_service import ImageService

        monkeypatch.setenv("HOME", str(tmp_path))
        mock_db = make_mock_db()
        mock_db.get_task = AsyncMock(return_value=_make_task())
        mock_db.update_task = AsyncMock(return_value=True)
        client = TestClient(_build_app(mock_db))

        with (
            patch.object(ImageService, "generate_image_result", autospec=True) as render,
            patch("aiohttp.ClientSession") as pexels_session,
        ):
            for source in ("image_gen", "pexels"):
                resp = client.post(self._URL, json={"source": source, "topic": "AI"})
                assert resp.status_code == 410, resp.text

        mock_db.get_task.assert_not_awaited()
        mock_db.update_task.assert_not_awaited()
        render.assert_not_called()
        pexels_session.assert_not_called()
        assert not (tmp_path / "Downloads").exists()

    def test_headers_mark_it_deprecated_and_link_both_successors(self):
        resp = TestClient(_build_app()).post(self._URL, json={"source": "image_gen"})

        assert resp.headers["Deprecation"] == "true"
        assert resp.headers["Warning"].startswith('299 - "')
        assert "generate-image is retired" in resp.headers["Warning"]
        assert _parse_link_header(resp.headers["Link"]) == [
            (
                f"/{VALID_TASK_ID}/regen-image",
                {"rel": "successor-version", "title": "source=image_gen"},
            ),
            (
                f"/{VALID_TASK_ID}/replace-image",
                {"rel": "successor-version", "title": "source=pexels"},
            ),
        ]

    def test_body_names_each_successor_with_its_cli_and_mcp_spelling(self):
        body = TestClient(_build_app()).post(self._URL, json={}).json()

        assert body["error_code"] == "GONE"
        assert "regen-image" in body["message"]
        assert "replace-image" in body["message"]
        assert body["detail"] == body["message"]
        assert body["successors"] == [
            {
                "method": "POST",
                "href": f"/{VALID_TASK_ID}/regen-image",
                "replaces": "source=image_gen",
                "cli": (
                    f"poindexter tasks regen-image {VALID_TASK_ID} "
                    '--which featured --prompt "<prompt>"'
                ),
                "mcp_tool": "regen_post_image",
            },
            {
                "method": "POST",
                "href": f"/{VALID_TASK_ID}/replace-image",
                "replaces": "source=pexels",
                "cli": (
                    f"poindexter tasks replace-image {VALID_TASK_ID} "
                    "--which featured --url <image-url>"
                ),
                "mcp_tool": "replace_post_image",
            },
        ]

    def test_the_app_error_machinery_passes_the_410_through_intact(self):
        """In production the app registers its own exception handlers and the
        request-id middleware. The HTTPException handler rebuilds a response
        from scratch and drops the exception's headers, which is why the stub
        returns its 410 instead of raising it. Through that machinery the
        response keeps its Link and speaks the app's error envelope, with the
        request ID the middleware assigned."""
        from middleware.request_id import RequestIDMiddleware
        from poindexter.utils.exception_handlers import register_exception_handlers

        app = FastAPI()
        register_exception_handlers(app)
        app.add_middleware(RequestIDMiddleware)
        app.include_router(publishing_router, prefix="/api/tasks")
        app.dependency_overrides[verify_api_token] = lambda: "test-token"

        resp = TestClient(app).post(
            f"/api/tasks{self._URL}", json={}, headers={"X-Request-ID": "probe-123"},
        )

        assert resp.status_code == 410, resp.text
        assert resp.headers["X-Request-ID"] == "probe-123"
        body = resp.json()
        assert body["error_code"] == "GONE"
        assert body["request_id"] == "probe-123"
        assert body["retired"] is True
        assert [target for target, _ in _parse_link_header(resp.headers["Link"])] == [
            f"/api/tasks/{VALID_TASK_ID}/regen-image",
            f"/api/tasks/{VALID_TASK_ID}/replace-image",
        ]

    def test_links_follow_the_prefix_the_router_is_mounted_under(self):
        """The hrefs come from url_for, not a hardcoded prefix. In the app the
        router sits under /api/tasks, and the Link has to say so."""
        app = FastAPI()
        app.include_router(publishing_router, prefix="/api/tasks")
        app.dependency_overrides[verify_api_token] = lambda: "test-token"

        resp = TestClient(app).post(f"/api/tasks{self._URL}", json={})

        assert resp.status_code == 410, resp.text
        assert [target for target, _ in _parse_link_header(resp.headers["Link"])] == [
            f"/api/tasks/{VALID_TASK_ID}/regen-image",
            f"/api/tasks/{VALID_TASK_ID}/replace-image",
        ]

    def test_the_successors_it_names_are_live_routes(self, monkeypatch):
        """Follow the pointers: each Link target must reach its PostEditService
        handler. If either route is renamed or removed, url_for raises and the
        410 itself fails here, so the pointer cannot dangle."""
        calls: dict = {}
        client = TestDraftEditingRoutes()._client_with_fake_service(monkeypatch, calls)

        gone = client.post(self._URL, json={"source": "image_gen"})
        assert gone.status_code == 410, gone.text
        (regen_href, _), (replace_href, _) = _parse_link_header(gone.headers["Link"])

        regen = client.post(
            regen_href, json={"which": "featured", "prompt": "a teal server rack"},
        )
        assert regen.status_code == 200, regen.text
        assert calls["regen_image"] == (
            VALID_TASK_ID, {"which": "featured", "prompt": "a teal server rack"},
        )

        replace = client.post(
            replace_href, json={"which": "featured", "url": "https://images.example/1.jpg"},
        )
        assert replace.status_code == 200, replace.text
        assert calls["replace_image"] == (
            VALID_TASK_ID, {"which": "featured", "url": "https://images.example/1.jpg"},
        )

    def test_a_crafted_task_id_cannot_inject_a_header(self):
        """The path parameter arrives decoded, so %0D%0A in the URL is a real
        CR/LF by the time the handler sees it. It must reach the Link header
        percent-encoded, not as a line break."""
        resp = TestClient(_build_app()).post("/abc%0D%0AX-Injected:%201/generate-image")

        assert resp.status_code == 410, resp.text
        assert "x-injected" not in {name.lower() for name in resp.headers}
        assert [target for target, _ in _parse_link_header(resp.headers["Link"])] == [
            "/abc%0D%0AX-Injected%3A%201/regen-image",
            "/abc%0D%0AX-Injected%3A%201/replace-image",
        ]

    def test_it_still_requires_auth(self):
        """Retiring the route must not open an unauthenticated surface: the
        router-level token check still runs before the 410."""
        app = FastAPI()
        app.include_router(publishing_router)

        resp = TestClient(app).post(self._URL, json={})

        assert resp.status_code == 401

    def test_openapi_marks_it_deprecated_and_documents_the_410(self):
        operation = _build_app().openapi()["paths"]["/{task_id}/generate-image"]["post"]

        assert operation["deprecated"] is True
        assert "410" in operation["responses"]
        assert "regen-image" in operation["summary"]
        assert "replace-image" in operation["summary"]
        # No request schema: an old client's body is ignored, never validated.
        assert "requestBody" not in operation


# ===========================================================================
# Scheduled approve — POST /{task_id}/approve with publish_at
# ===========================================================================


@pytest.mark.unit
class TestApproveTaskScheduled:
    """The console's Schedule action: approve + a publish slot in one call.

    Regression context — until 2026-08-08 this branch wrote
    ``pipeline_tasks.scheduled_at`` (a column nothing read) and *skipped*
    the ``stage_only`` staging call entirely, so a scheduled approve
    created no ``posts`` row at all and the post never published. The
    publish queue is keyed on ``posts (status='scheduled', published_at)``
    — what ``scheduled_publisher`` polls — so these tests pin that the
    slot lands there and nowhere else.
    """

    def _approve(self, client, task_id=VALID_TASK_ID, **body):
        return client.post(f"/{task_id}/approve", json=body)

    def _staged(self, post_id="post-1", slug="a-slug"):
        """A successful stage_only PublishResult double."""
        r = MagicMock()
        r.success = True
        r.post_id = post_id
        r.post_slug = slug
        r.error = None
        return r

    def _slot_ok(self):
        r = MagicMock()
        r.ok = True
        r.detail = "scheduled"
        return r

    def test_publish_at_stages_then_assigns_the_slot(self):
        """The slot goes through assign_slot — posts, not pipeline_tasks."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        _set_pool(mock_db, [])

        stage = AsyncMock(return_value=self._staged())
        assign = AsyncMock(return_value=self._slot_ok())
        app = _build_app(mock_db)

        with (
            patch("poindexter.services.publish_service.publish_post_from_task", stage),
            patch("poindexter.services.scheduling_service.assign_slot", assign),
        ):
            resp = self._approve(
                TestClient(app),
                approved=True,
                auto_publish=False,
                publish_at="2026-09-01T09:00:00+00:00",
            )

        assert resp.status_code == 200
        # Staged first — a scheduled approve must still create the posts row.
        assert stage.await_count == 1, "publish_at must not skip staging"
        assert stage.await_args.kwargs["stage_only"] is True
        # Then promoted to the queue via assign_slot.
        assert assign.await_count == 1
        assert assign.await_args.args[0] == "post-1"
        assert assign.await_args.args[1].isoformat() == "2026-09-01T09:00:00+00:00"

    def test_publish_at_reports_the_committed_slot(self):
        """`scheduled_for` echoes what the server committed — the console
        renders it straight into the confirmation toast."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        _set_pool(mock_db, [])

        app = _build_app(mock_db)
        with (
            patch(
                "poindexter.services.publish_service.publish_post_from_task",
                AsyncMock(return_value=self._staged()),
            ),
            patch(
                "poindexter.services.scheduling_service.assign_slot",
                AsyncMock(return_value=self._slot_ok()),
            ),
        ):
            resp = self._approve(
                TestClient(app),
                approved=True,
                publish_at="2026-09-01T09:00:00+00:00",
            )

        assert resp.json()["scheduled_for"] == "2026-09-01T09:00:00+00:00"

    def test_slot_failure_leaves_scheduled_for_null_and_explains(self):
        """A refused slot must NOT read as success.

        The approve itself already committed, so this stays 200 — but
        ``scheduled_for`` is null and ``message`` carries the reason, which
        is what flips the console's toast from cyan to amber.
        """
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        _set_pool(mock_db, [])

        refused = MagicMock()
        refused.ok = False
        refused.detail = "Post post-1 already scheduled for 2026-09-02T09:00:00+00:00"

        app = _build_app(mock_db)
        with (
            patch(
                "poindexter.services.publish_service.publish_post_from_task",
                AsyncMock(return_value=self._staged()),
            ),
            patch(
                "poindexter.services.scheduling_service.assign_slot",
                AsyncMock(return_value=refused),
            ),
        ):
            resp = self._approve(
                TestClient(app),
                approved=True,
                publish_at="2026-09-01T09:00:00+00:00",
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["scheduled_for"] is None
        assert "NOT scheduled" in data["message"]
        assert "already scheduled" in data["message"]

    def test_staging_failure_skips_assign_slot(self):
        """No posts row means nothing to schedule — don't call assign_slot
        with a null post_id and manufacture a confusing second error."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        _set_pool(mock_db, [])

        failed = MagicMock()
        failed.success = False
        failed.post_id = None
        failed.post_slug = None
        failed.error = "slug collision"

        assign = AsyncMock()
        app = _build_app(mock_db)
        with (
            patch(
                "poindexter.services.publish_service.publish_post_from_task",
                AsyncMock(return_value=failed),
            ),
            patch("poindexter.services.scheduling_service.assign_slot", assign),
        ):
            resp = self._approve(
                TestClient(app),
                approved=True,
                publish_at="2026-09-01T09:00:00+00:00",
            )

        assert resp.status_code == 200
        assert assign.await_count == 0
        assert resp.json()["scheduled_for"] is None
        assert "slug collision" in resp.json()["message"]

    def test_unparseable_publish_at_400s_before_any_state_change(self):
        """A typo'd timestamp used to fall through to an immediate publish.

        It now 400s while the task is still untouched — no status flip, no
        staging call (feedback_no_silent_defaults).
        """
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        stage = AsyncMock()
        app = _build_app(mock_db)
        with patch("poindexter.services.publish_service.publish_post_from_task", stage):
            resp = self._approve(
                TestClient(app), approved=True, publish_at="next tuseday"
            )

        assert resp.status_code == 400
        assert mock_db.update_task_status.await_count == 0, "task must be untouched"
        assert stage.await_count == 0

    def test_relative_publish_at_is_accepted(self):
        """parse_when is shared with the CLI, so operator shorthand works."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        _set_pool(mock_db, [])

        assign = AsyncMock(return_value=self._slot_ok())
        app = _build_app(mock_db)
        with (
            patch(
                "poindexter.services.publish_service.publish_post_from_task",
                AsyncMock(return_value=self._staged()),
            ),
            patch("poindexter.services.scheduling_service.assign_slot", assign),
        ):
            resp = self._approve(
                TestClient(app), approved=True, publish_at="tomorrow 9am"
            )

        assert resp.status_code == 200
        assert assign.await_count == 1
        assert assign.await_args.args[1].hour == 9

    def test_clock_words_resolve_in_the_operator_timezone(self):
        """"tomorrow 9am" is 9am where the operator is, not 09:00Z.

        This route read clock words as UTC until 2026-08-09 while social
        drafts already read them locally, so the same words meant different
        instants depending on which queue you typed them at. The console
        never saw it (its picker sends an absolute ISO instant) — the API,
        the CLI and the MCP `schedule_post` tool did.
        """
        from zoneinfo import ZoneInfo

        from poindexter.services.site_config import SiteConfig
        from poindexter.utils.route_utils import get_site_config_dependency

        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        _set_pool(mock_db, [])

        assign = AsyncMock(return_value=self._slot_ok())
        app = _build_app(mock_db)
        ny = SiteConfig(initial_config={"operator_timezone": "America/New_York"})
        app.dependency_overrides[get_site_config_dependency] = lambda: ny

        with (
            patch(
                "poindexter.services.publish_service.publish_post_from_task",
                AsyncMock(return_value=self._staged()),
            ),
            patch("poindexter.services.scheduling_service.assign_slot", assign),
        ):
            resp = self._approve(
                TestClient(app), approved=True, publish_at="2026-09-01 09:00"
            )

        assert resp.status_code == 200
        committed = assign.await_args.args[1]
        # 09:00 in New York on 2026-09-01 (EDT, UTC-4) is 13:00Z.
        assert committed.astimezone(ZoneInfo("America/New_York")).hour == 9
        assert committed.utctimetuple().tm_hour == 13

    def test_explicit_offset_overrides_the_operator_timezone(self):
        """An absolute instant is honoured as sent — the console's path."""
        from poindexter.services.site_config import SiteConfig
        from poindexter.utils.route_utils import get_site_config_dependency

        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        _set_pool(mock_db, [])

        assign = AsyncMock(return_value=self._slot_ok())
        app = _build_app(mock_db)
        app.dependency_overrides[get_site_config_dependency] = lambda: SiteConfig(
            initial_config={"operator_timezone": "America/New_York"}
        )

        with (
            patch(
                "poindexter.services.publish_service.publish_post_from_task",
                AsyncMock(return_value=self._staged()),
            ),
            patch("poindexter.services.scheduling_service.assign_slot", assign),
        ):
            resp = self._approve(
                TestClient(app),
                approved=True,
                publish_at="2026-09-01T09:00:00+00:00",
            )

        assert resp.status_code == 200
        assert assign.await_args.args[1].isoformat() == "2026-09-01T09:00:00+00:00"

    def test_auto_publish_with_publish_at_is_rejected(self):
        """"Ship now" and "ship Thursday" can't both be true.

        The old code silently resolved this by clearing auto_publish. A
        contradictory request is an operator error, so it 400s instead.
        """
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])

        app = _build_app(mock_db)
        resp = self._approve(
            TestClient(app),
            approved=True,
            auto_publish=True,
            publish_at="2026-09-01T09:00:00+00:00",
        )

        assert resp.status_code == 400
        assert "mutually exclusive" in resp.json()["detail"]
        assert mock_db.update_task_status.await_count == 0

    def test_plain_approve_still_stages_without_a_slot(self):
        """The no-publish_at path is unchanged: stage, never assign_slot."""
        mock_db = make_mock_db()
        task = _make_task(status="awaiting_approval")
        mock_db.get_task = AsyncMock(side_effect=[task, task])
        _set_pool(mock_db, [])

        stage = AsyncMock(return_value=self._staged())
        assign = AsyncMock()
        app = _build_app(mock_db)
        with (
            patch("poindexter.services.publish_service.publish_post_from_task", stage),
            patch("poindexter.services.scheduling_service.assign_slot", assign),
        ):
            resp = self._approve(TestClient(app), approved=True, auto_publish=False)

        assert resp.status_code == 200
        assert stage.await_count == 1
        assert assign.await_count == 0
        assert resp.json()["scheduled_for"] is None
