"""Route tests for /api/chat/* (routes/chat_routes.py, poindexter#947).

Pins the HTTP contract the console's api.js adapter builds against:

  - the console_chat_enabled gate 403s with the remediation command,
  - conversation CRUD statuses (201 / 404 / 409),
  - the turn endpoint streams NDJSON events verbatim from run_turn,
  - conversation reads run the lazy interrupted-turn repair.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import services.chat_agent as chat_agent
import services.chat_conversation_store as store_module
from middleware.api_token_auth import verify_api_token
from routes.chat_routes import router
from utils.route_utils import get_database_dependency, get_site_config_dependency


class FakeSiteConfig:
    def __init__(self, enabled=True, **overrides):
        self.values = {
            "console_chat_enabled": "true" if enabled else "false",
            "console_chat_turn_timeout_s": "120",
        }
        self.values.update({k: str(v) for k, v in overrides.items()})

    def get(self, key, default=None):
        return self.values.get(key, default)


class FakeDb:
    pool = object()


def _build_client(*, enabled=True) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_api_token] = lambda: "test-token"
    app.dependency_overrides[get_database_dependency] = lambda: FakeDb()
    app.dependency_overrides[get_site_config_dependency] = (
        lambda: FakeSiteConfig(enabled=enabled)
    )
    return TestClient(app, raise_server_exceptions=False)


_CONV = {
    "id": "11111111-1111-1111-1111-111111111111",
    "status": "active", "brain": "local", "title": "t",
}


@pytest.fixture
def fake_store(monkeypatch):
    state: dict[str, Any] = {
        "conversation": dict(_CONV), "repaired": [], "archived": [],
    }

    async def create_conversation(pool, *, title="", brain="local",
                                  transport="console", user_id="operator"):
        return {**_CONV, "title": title, "brain": brain}

    async def get_conversation(pool, conversation_id):
        conv = state["conversation"]
        return conv if conversation_id == conv["id"] else None

    async def list_conversations(pool, *, status="active", limit=50):
        return [state["conversation"]]

    async def list_messages(pool, conversation_id, *, limit=200):
        return [{"id": "m1", "role": "user", "parts": []}]

    async def list_task_links(pool, conversation_id):
        return []

    async def archive_conversation(pool, conversation_id):
        state["archived"].append(conversation_id)
        return True

    async def repair_stale_turns(pool, conversation_id, *, stale_after_seconds):
        state["repaired"].append((conversation_id, stale_after_seconds))
        return 0

    for name, fn in [
        ("create_conversation", create_conversation),
        ("get_conversation", get_conversation),
        ("list_conversations", list_conversations),
        ("list_messages", list_messages),
        ("list_task_links", list_task_links),
        ("archive_conversation", archive_conversation),
        ("repair_stale_turns", repair_stale_turns),
    ]:
        monkeypatch.setattr(store_module, name, fn)
    return state


@pytest.mark.unit
class TestEnabledGate:
    def test_disabled_403s_with_remediation(self, fake_store):
        client = _build_client(enabled=False)
        resp = client.get("/api/chat/conversations")
        assert resp.status_code == 403
        assert "console_chat_enabled" in resp.json()["detail"]

    def test_disabled_gates_the_turn_endpoint_too(self, fake_store):
        client = _build_client(enabled=False)
        resp = client.post(
            f"/api/chat/conversations/{_CONV['id']}/messages",
            json={"text": "hi"},
        )
        assert resp.status_code == 403


@pytest.mark.unit
class TestToolsCatalog:
    def test_lists_registry_tools_with_tiers(self, fake_store):
        client = _build_client()
        resp = client.get("/api/chat/tools")
        assert resp.status_code == 200
        body = resp.json()
        assert body["persona"] == "Poindexter"
        names = [t["name"] for t in body["tools"]]
        assert "create_post" in names and "list_tasks" in names
        assert all(t["tier"] in ("read", "write") for t in body["tools"])
        assert all(t["description"] for t in body["tools"])

    def test_gated_by_enabled_flag(self, fake_store):
        client = _build_client(enabled=False)
        assert client.get("/api/chat/tools").status_code == 403


@pytest.mark.unit
class TestApprovalRoutes:
    def _client_with_resolver(self, monkeypatch, resolver):
        import services.chat_approvals as chat_approvals

        monkeypatch.setattr(chat_approvals, "resolve_approval", resolver)
        return _build_client()

    def test_approve_resolves(self, fake_store, monkeypatch):
        seen = {}

        async def resolver(*, pool, db_service, site_config, approval_id,
                           approve, user_id="operator"):
            seen.update({"id": approval_id, "approve": approve})
            return {"id": approval_id, "status": "approved",
                    "executed_ok": True, "result_digest": "done"}

        client = self._client_with_resolver(monkeypatch, resolver)
        resp = client.post("/api/chat/approvals/appr-1/approve")
        assert resp.status_code == 200
        assert resp.json()["status"] == "approved"
        assert seen == {"id": "appr-1", "approve": True}

    def test_deny_resolves(self, fake_store, monkeypatch):
        async def resolver(**kwargs):
            assert kwargs["approve"] is False
            return {"id": kwargs["approval_id"], "status": "denied"}

        client = self._client_with_resolver(monkeypatch, resolver)
        assert client.post("/api/chat/approvals/appr-1/deny").status_code == 200

    def test_unknown_approval_404(self, fake_store, monkeypatch):
        async def resolver(**kwargs):
            raise KeyError(kwargs["approval_id"])

        client = self._client_with_resolver(monkeypatch, resolver)
        assert client.post("/api/chat/approvals/nope/approve").status_code == 404

    def test_gated_by_enabled_flag(self, fake_store):
        client = _build_client(enabled=False)
        assert client.post("/api/chat/approvals/a/approve").status_code == 403


@pytest.mark.unit
class TestPlanRunRoute:
    def test_run_passthrough(self, fake_store, monkeypatch):
        import services.chat_plans as chat_plans

        seen = {}

        async def run_plan(*, pool, db_service, plan_id, topic_override=None,
                           params=None, user_id="operator"):
            seen.update({"id": plan_id, "topic": topic_override,
                         "params": params})
            return {"id": plan_id, "status": "ran", "task_id": "t-1"}

        monkeypatch.setattr(chat_plans, "run_plan", run_plan)
        client = _build_client()
        resp = client.post(
            "/api/chat/plans/p-1/run",
            json={"topic": "Better", "params": {"post_id": "78f8a6cc"}},
        )
        assert resp.status_code == 200
        assert resp.json()["task_id"] == "t-1"
        assert seen == {"id": "p-1", "topic": "Better",
                        "params": {"post_id": "78f8a6cc"}}

    def test_bad_params_422(self, fake_store, monkeypatch):
        import services.chat_plans as chat_plans

        async def run_plan(**kwargs):
            raise ValueError("param key 'task_id' is reserved")

        monkeypatch.setattr(chat_plans, "run_plan", run_plan)
        client = _build_client()
        resp = client.post(
            "/api/chat/plans/p-1/run", json={"params": {"task_id": "x"}},
        )
        assert resp.status_code == 422
        assert "reserved" in resp.json()["detail"]

    def test_unknown_plan_404(self, fake_store, monkeypatch):
        import services.chat_plans as chat_plans

        async def run_plan(**kwargs):
            raise KeyError(kwargs["plan_id"])

        monkeypatch.setattr(chat_plans, "run_plan", run_plan)
        client = _build_client()
        assert client.post("/api/chat/plans/nope/run").status_code == 404

    def test_gated_by_enabled_flag(self, fake_store):
        client = _build_client(enabled=False)
        assert client.post("/api/chat/plans/p/run").status_code == 403


@pytest.mark.unit
class TestWatchRoute:
    def test_snapshot_passthrough(self, fake_store, monkeypatch):
        import services.chat_watch as chat_watch

        async def watch_task(pool, task_id):
            return {"task_id": task_id, "status": "in_progress",
                    "terminal": False, "nodes_done": 3}

        monkeypatch.setattr(chat_watch, "watch_task", watch_task)
        client = _build_client()
        resp = client.get("/api/chat/watch/t-9")
        assert resp.status_code == 200
        assert resp.json()["nodes_done"] == 3

    def test_unknown_task_404(self, fake_store, monkeypatch):
        import services.chat_watch as chat_watch

        async def watch_task(pool, task_id):
            return None

        monkeypatch.setattr(chat_watch, "watch_task", watch_task)
        client = _build_client()
        assert client.get("/api/chat/watch/nope").status_code == 404


@pytest.mark.unit
class TestConversationCrud:
    def test_create_201(self, fake_store):
        client = _build_client()
        resp = client.post("/api/chat/conversations", json={"title": "Hello"})
        assert resp.status_code == 201
        assert resp.json()["title"] == "Hello"

    def test_create_rejects_unknown_brain(self, fake_store):
        client = _build_client()
        resp = client.post("/api/chat/conversations", json={"brain": "hal9000"})
        assert resp.status_code == 422

    def test_list_repairs_stale_turns(self, fake_store):
        client = _build_client()
        resp = client.get("/api/chat/conversations")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1
        assert fake_store["repaired"] == [(None, 240)]

    def test_get_returns_thread_and_repairs(self, fake_store):
        client = _build_client()
        resp = client.get(f"/api/chat/conversations/{_CONV['id']}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["conversation"]["id"] == _CONV["id"]
        assert body["messages"][0]["id"] == "m1"
        assert fake_store["repaired"] == [(_CONV["id"], 240)]

    def test_unknown_conversation_404(self, fake_store):
        client = _build_client()
        resp = client.get(
            "/api/chat/conversations/99999999-9999-9999-9999-999999999999",
        )
        assert resp.status_code == 404

    def test_archive(self, fake_store):
        client = _build_client()
        resp = client.post(f"/api/chat/conversations/{_CONV['id']}/archive")
        assert resp.status_code == 200
        assert fake_store["archived"] == [_CONV["id"]]


@pytest.mark.unit
class TestTurnStream:
    def test_streams_ndjson_events(self, fake_store, monkeypatch):
        async def fake_run_turn(**kwargs):
            assert kwargs["user_text"] == "hi"
            yield {"event": "turn_started", "message_id": "m2"}
            yield {"event": "text", "text": "hello"}
            yield {"event": "done", "turn_status": "complete",
                   "prompt_tokens": 1, "completion_tokens": 2, "cost_usd": 0}

        monkeypatch.setattr(chat_agent, "run_turn", fake_run_turn)
        client = _build_client()
        resp = client.post(
            f"/api/chat/conversations/{_CONV['id']}/messages",
            json={"text": "hi"},
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        import json as _json
        lines = [
            _json.loads(line) for line in resp.text.splitlines() if line.strip()
        ]
        assert [e["event"] for e in lines] == ["turn_started", "text", "done"]

    def test_generator_crash_ends_stream_with_error_event(
        self, fake_store, monkeypatch,
    ):
        async def broken_run_turn(**kwargs):
            yield {"event": "turn_started", "message_id": "m2"}
            raise RuntimeError("boom")

        monkeypatch.setattr(chat_agent, "run_turn", broken_run_turn)
        client = _build_client()
        resp = client.post(
            f"/api/chat/conversations/{_CONV['id']}/messages",
            json={"text": "hi"},
        )
        import json as _json
        lines = [
            _json.loads(line) for line in resp.text.splitlines() if line.strip()
        ]
        assert lines[-1]["event"] == "error"
        assert lines[-1]["reason"] == "stream_crashed"

    def test_archived_conversation_409(self, fake_store):
        fake_store["conversation"]["status"] = "archived"
        client = _build_client()
        resp = client.post(
            f"/api/chat/conversations/{_CONV['id']}/messages",
            json={"text": "hi"},
        )
        assert resp.status_code == 409

    def test_claude_code_brain_409_until_p6(self, fake_store):
        fake_store["conversation"]["brain"] = "claude_code"
        client = _build_client()
        resp = client.post(
            f"/api/chat/conversations/{_CONV['id']}/messages",
            json={"text": "hi"},
        )
        assert resp.status_code == 409
        assert "P6" in resp.json()["detail"]

    def test_empty_text_422(self, fake_store):
        client = _build_client()
        resp = client.post(
            f"/api/chat/conversations/{_CONV['id']}/messages", json={"text": ""},
        )
        assert resp.status_code == 422
