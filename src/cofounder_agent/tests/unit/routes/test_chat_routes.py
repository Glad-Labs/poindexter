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
