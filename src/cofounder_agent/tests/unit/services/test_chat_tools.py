"""Contract tests for the chat tool registry (services/chat_tools.py).

The registry is the Cofounder agent's capability catalog AND its safety
envelope — these pin the shape (poindexter#947):

  - every tool has a unique name, a valid tier, and a well-formed
    OpenAI-style JSON schema (the ``tools=`` payload),
  - P1 ships exactly one write tool (``create_post``) — adding another
    write tool must fail here until the P3 approval-card gate exists,
  - handlers surface repairable failures as ``ChatToolError`` with an
    LLM-readable message,
  - ``create_post`` delegates to the shared blog-task-creation service and
    reports created tasks through ``ctx.linked_task_ids``.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

import services.blog_task_creation as blog_task_creation
from services.blog_task_creation import BlogTaskCreationError
from services.chat_tools import (
    CHAT_TOOLS,
    ChatToolContext,
    ChatToolError,
    get_tool,
    to_openai_tools,
    tool_names_csv,
)


def _ctx(**overrides) -> ChatToolContext:
    defaults = dict(
        db_service=object(), site_config=object(), pool=object(),
        user_id="operator", conversation_id="c1",
    )
    defaults.update(overrides)
    return ChatToolContext(**defaults)


@pytest.mark.unit
class TestRegistryShape:
    def test_names_unique(self):
        names = [t.name for t in CHAT_TOOLS]
        assert len(names) == len(set(names))

    def test_tiers_valid(self):
        assert all(t.tier in ("read", "write") for t in CHAT_TOOLS)

    def test_write_surface_pinned_with_approval_gates(self):
        """Every write tool must be deliberate: either approval-carded
        (P3 machinery, poindexter#949) or explicitly exempt with a reason.
        A new write tool cannot land without updating this pin."""
        writes = {t.name: t.requires_approval for t in CHAT_TOOLS if t.tier == "write"}
        assert writes == {
            # Exempt: its output already lands in the operator's approval
            # inbox — the human gate is downstream (P1 decision).
            "create_post": False,
            # Approval-carded (executed only via the operator's click):
            "set_setting": True,
            "restart_service": True,
            "cancel_task": True,
        }

    def test_read_tools_never_carry_approval(self):
        assert all(
            not t.requires_approval for t in CHAT_TOOLS if t.tier == "read"
        )

    def test_handlers_are_async(self):
        assert all(inspect.iscoroutinefunction(t.handler) for t in CHAT_TOOLS)

    def test_schemas_well_formed(self):
        for payload in to_openai_tools():
            assert payload["type"] == "function"
            fn = payload["function"]
            assert fn["name"] and fn["description"]
            params = fn["parameters"]
            assert params["type"] == "object"
            assert isinstance(params["properties"], dict)
            assert isinstance(params["required"], list)
            for req in params["required"]:
                assert req in params["properties"]

    def test_lookup_and_csv(self):
        assert get_tool("list_tasks") is not None
        assert get_tool("nope") is None
        assert "create_post" in tool_names_csv()


@pytest.mark.unit
class TestGetSetting:
    def test_missing_or_secret_raises_chat_tool_error(self):
        class Cfg:
            def get(self, key, default=None):
                return None

        with pytest.raises(ChatToolError, match="secret"):
            asyncio.run(
                get_tool("get_setting").handler(_ctx(site_config=Cfg()), key="x")
            )

    def test_present_value_rendered(self):
        class Cfg:
            def get(self, key, default=None):
                return "42"

        out = asyncio.run(
            get_tool("get_setting").handler(_ctx(site_config=Cfg()), key="k")
        )
        assert out == "k = '42'"


@pytest.mark.unit
class TestCreatePost:
    def test_delegates_and_links_task(self, monkeypatch):
        captured = {}

        async def fake_create(request, *, db_service, site_config, user_id):
            captured["topic"] = request.topic
            captured["user_id"] = user_id
            return {"task_id": "t-123", "topic": request.topic,
                    "status": "pending", "message": "queued"}

        monkeypatch.setattr(
            blog_task_creation, "create_blog_post_task", fake_create,
        )
        ctx = _ctx()
        out = asyncio.run(
            get_tool("create_post").handler(ctx, topic="Local-first analytics")
        )
        assert captured == {"topic": "Local-first analytics", "user_id": "operator"}
        assert ctx.linked_task_ids == ["t-123"]
        assert "t-123" in out and "pending" in out

    def test_creation_error_becomes_chat_tool_error(self, monkeypatch):
        async def fake_create(request, **_kw):
            raise BlogTaskCreationError(
                409, "topic collides with 'Existing post' — pass force=true",
            )

        monkeypatch.setattr(
            blog_task_creation, "create_blog_post_task", fake_create,
        )
        ctx = _ctx()
        with pytest.raises(ChatToolError, match="force=true"):
            asyncio.run(get_tool("create_post").handler(ctx, topic="Dup topic"))
        assert ctx.linked_task_ids == []


@pytest.mark.unit
class TestListTasks:
    """The real contract is a ``(rows, total)`` TUPLE — the
    ``PaginatedTasksResult`` name is documented as a lie (#201). The
    original fake here modeled a ``.tasks`` object and hid a bug that
    made the live tool report zero tasks against a full table."""

    def test_renders_rows_total_and_clamps_limit(self):
        class Db:
            async def get_tasks_paginated(self, **kwargs):
                assert kwargs["limit"] == 50  # clamped from 999
                return (
                    [{"task_id": "abcdef123456", "status": "pending",
                      "topic": "T1", "created_at": "2026-07-31T00:00:00"}],
                    1992,
                )

        out = asyncio.run(
            get_tool("list_tasks").handler(_ctx(db_service=Db()), limit=999)
        )
        assert "abcdef12" in out and "[pending]" in out and "T1" in out
        assert "of 1992 total" in out

    def test_empty(self):
        class Db:
            async def get_tasks_paginated(self, **kwargs):
                return ([], 0)

        out = asyncio.run(
            get_tool("list_tasks").handler(_ctx(db_service=Db()), status="failed")
        )
        assert "No tasks found" in out and "failed" in out
