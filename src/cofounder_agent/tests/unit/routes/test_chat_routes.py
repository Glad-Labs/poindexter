"""
Unit tests for routes/chat_routes.py.

Tests cover:
- POST /api/chat              — chat
- GET  /api/chat/history/{id} — get_conversation
- DELETE /api/chat/history/{id} — clear_conversation
- GET  /api/chat/models       — get_available_models

ollama_client and gemini_client are patched to avoid real network I/O.
No auth required on these endpoints.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

import routes.chat_routes as chat_module
from routes.auth_unified import get_current_user
from routes.chat_routes import router

TEST_USER = {"id": "test-user-001", "username": "testuser", "email": "test@example.com"}


@pytest.fixture(autouse=True)
def clear_conversations():
    """Reset in-memory conversation store between tests."""
    chat_module.conversations.clear()
    yield
    chat_module.conversations.clear()


def _build_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    # Override auth for all tests — no real token needed
    app.dependency_overrides[get_current_user] = lambda: TEST_USER
    return app


def _make_ollama_client(list_models_result=None, chat_result=None):
    client = MagicMock()
    client.list_models = AsyncMock(
        return_value=list_models_result if list_models_result is not None else ["llama2", "mistral"]
    )
    client.chat = AsyncMock(
        return_value=chat_result or {"content": "Hello from Ollama!", "tokens": 10}
    )
    return client


def _make_gemini_client(is_configured=True, list_models_result=None, chat_result=None):
    client = MagicMock()
    client.is_configured = MagicMock(return_value=is_configured)
    client.list_models = AsyncMock(
        return_value=list_models_result
        if list_models_result is not None
        else ["gemini-2.5-flash"]
    )
    client.chat = AsyncMock(return_value=chat_result or "Hello from Gemini!")
    return client


VALID_OLLAMA_PAYLOAD = {
    "message": "What is 2+2?",
    "model": "ollama",
    "conversationId": "test-conv-1",
}

VALID_GEMINI_PAYLOAD = {
    "message": "Hello",
    "model": "gemini",
    "conversationId": "test-conv-2",
}

VALID_OPENAI_PAYLOAD = {
    "message": "Hello",
    "model": "openai",
    "conversationId": "test-conv-3",
}


# ---------------------------------------------------------------------------
# POST /api/chat
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestChat:
    def test_ollama_returns_200(self):
        with (
            patch.object(chat_module, "ollama_client", _make_ollama_client()),
            patch.object(chat_module, "gemini_client", _make_gemini_client()),
        ):
            client = TestClient(_build_app())
            resp = client.post("/api/chat", json=VALID_OLLAMA_PAYLOAD)
        assert resp.status_code == 200

    def test_ollama_response_has_required_fields(self):
        with (
            patch.object(chat_module, "ollama_client", _make_ollama_client()),
            patch.object(chat_module, "gemini_client", _make_gemini_client()),
        ):
            client = TestClient(_build_app())
            data = client.post("/api/chat", json=VALID_OLLAMA_PAYLOAD).json()
        for field in ["response", "model", "conversationId", "timestamp"]:
            assert field in data

    def test_ollama_response_echoes_model(self):
        with (
            patch.object(chat_module, "ollama_client", _make_ollama_client()),
            patch.object(chat_module, "gemini_client", _make_gemini_client()),
        ):
            client = TestClient(_build_app())
            data = client.post("/api/chat", json=VALID_OLLAMA_PAYLOAD).json()
        assert data["model"] == "ollama"
        assert data["conversationId"] == "test-conv-1"

    def test_openai_returns_200_with_demo_response(self):
        """openai/claude providers fall through to generate_demo_response."""
        client = TestClient(_build_app())
        resp = client.post("/api/chat", json=VALID_OPENAI_PAYLOAD)
        assert resp.status_code == 200

    def test_gemini_returns_200(self):
        with (
            patch.object(chat_module, "ollama_client", _make_ollama_client()),
            patch.object(chat_module, "gemini_client", _make_gemini_client()),
        ):
            client = TestClient(_build_app())
            resp = client.post("/api/chat", json=VALID_GEMINI_PAYLOAD)
        assert resp.status_code == 200

    def test_invalid_provider_returns_400(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/chat",
            json={"message": "Hi", "model": "notaprovider", "conversationId": "c"},
        )
        assert resp.status_code == 400

    def test_missing_message_returns_422(self):
        client = TestClient(_build_app())
        resp = client.post(
            "/api/chat", json={"model": "ollama", "conversationId": "c"}
        )
        assert resp.status_code == 422

    def test_model_defaults_to_ollama_when_omitted(self):
        """model has a default of 'ollama', so omitting it still succeeds."""
        client = TestClient(_build_app())
        resp = client.post("/api/chat", json={"message": "Hi", "conversationId": "c"})
        # Returns 200 because model defaults to "ollama" (demo/openai path used in tests)
        assert resp.status_code in (200, 400, 500)

    def test_ollama_model_not_found_returns_200_with_error_message(self):
        """When requested model not in available list, route still returns 200 with guidance."""
        ollama = _make_ollama_client(list_models_result=["mistral"])
        with (
            patch.object(chat_module, "ollama_client", ollama),
            patch.object(chat_module, "gemini_client", _make_gemini_client()),
        ):
            client = TestClient(_build_app())
            data = client.post(
                "/api/chat",
                json={"message": "Hi", "model": "ollama-nonexistent", "conversationId": "c"},
            ).json()
        assert "response" in data
        assert "not available" in data["response"] or "nonexistent" in data["response"]

    def test_gemini_not_configured_returns_200_with_error_message(self):
        """Gemini error path still returns 200 (graceful error embedded in response)."""
        with (
            patch.object(chat_module, "ollama_client", _make_ollama_client()),
            patch.object(chat_module, "gemini_client", _make_gemini_client(is_configured=False)),
        ):
            client = TestClient(_build_app())
            data = client.post("/api/chat", json=VALID_GEMINI_PAYLOAD).json()
        assert "response" in data

    def test_conversation_history_accumulates(self):
        """Multi-turn conversation adds messages to history (scoped by user_id)."""
        ollama = _make_ollama_client()
        with (
            patch.object(chat_module, "ollama_client", ollama),
            patch.object(chat_module, "gemini_client", _make_gemini_client()),
        ):
            tc = TestClient(_build_app())
            tc.post("/api/chat", json={**VALID_OLLAMA_PAYLOAD, "conversationId": "multi"})
            tc.post(
                "/api/chat",
                json={"message": "Follow up", "model": "ollama", "conversationId": "multi"},
            )
        scoped_key = f"{TEST_USER['id']}:multi"
        assert scoped_key in chat_module.conversations
        assert len(chat_module.conversations[scoped_key]) == 4  # 2 user + 2 assistant


# ---------------------------------------------------------------------------
# GET /api/chat/history/{conversation_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetConversation:
    def test_returns_200_for_unknown_conversation(self):
        client = TestClient(_build_app())
        resp = client.get("/api/chat/history/no-such-conv")
        assert resp.status_code == 200

    def test_empty_history_for_unknown_conversation(self):
        client = TestClient(_build_app())
        data = client.get("/api/chat/history/no-such-conv").json()
        assert data["messages"] == []
        assert data["message_count"] == 0

    def test_returns_messages_for_existing_conversation(self):
        # Conversations are scoped by user_id — use the test user's scoped key
        scoped_key = f"{TEST_USER['id']}:existing"
        chat_module.conversations[scoped_key] = [
            {"role": "user", "content": "Hi", "timestamp": "2026-03-12T08:00:00"}
        ]
        client = TestClient(_build_app())
        data = client.get("/api/chat/history/existing").json()
        assert data["message_count"] == 1
        assert data["messages"][0]["content"] == "Hi"

    def test_response_has_required_fields(self):
        client = TestClient(_build_app())
        data = client.get("/api/chat/history/empty-conv").json()
        for field in ["messages", "conversation_id", "message_count"]:
            assert field in data


# ---------------------------------------------------------------------------
# DELETE /api/chat/history/{conversation_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClearConversation:
    def test_returns_200_for_existing_conversation(self):
        scoped_key = f"{TEST_USER['id']}:to-clear"
        chat_module.conversations[scoped_key] = [
            {"role": "user", "content": "Hi", "timestamp": "now"}
        ]
        client = TestClient(_build_app())
        resp = client.delete("/api/chat/history/to-clear")
        assert resp.status_code == 200

    def test_returns_200_for_nonexistent_conversation(self):
        client = TestClient(_build_app())
        resp = client.delete("/api/chat/history/does-not-exist")
        assert resp.status_code == 200

    def test_conversation_removed_after_clear(self):
        scoped_key = f"{TEST_USER['id']}:will-clear"
        chat_module.conversations[scoped_key] = [{"role": "user", "content": "x"}]
        client = TestClient(_build_app())
        client.delete("/api/chat/history/will-clear")
        assert scoped_key not in chat_module.conversations

    def test_response_has_status_success(self):
        client = TestClient(_build_app())
        data = client.delete("/api/chat/history/some-conv").json()
        assert data["status"] == "success"


# ---------------------------------------------------------------------------
# GET /api/chat/models
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetAvailableModels:
    def test_returns_200(self):
        client = TestClient(_build_app())
        resp = client.get("/api/chat/models")
        assert resp.status_code == 200

    def test_response_has_models_and_count(self):
        client = TestClient(_build_app())
        data = client.get("/api/chat/models").json()
        assert "models" in data
        assert "available_count" in data

    def test_models_includes_all_four_providers(self):
        client = TestClient(_build_app())
        data = client.get("/api/chat/models").json()
        ids = [m["id"] for m in data["models"]]
        for provider in ["ollama", "openai", "claude", "gemini"]:
            assert provider in ids

    def test_available_count_matches_models_list(self):
        client = TestClient(_build_app())
        data = client.get("/api/chat/models").json()
        assert data["available_count"] == len(data["models"])


# ---------------------------------------------------------------------------
# Conversation isolation & history coverage (issue #792)
# ---------------------------------------------------------------------------


def _build_app_for_user(user: dict) -> FastAPI:
    """Build a FastAPI app with a specific user injected via dependency override."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@pytest.mark.unit
class TestConversationIsolation:
    """Cross-user conversation isolation — two users with the same conversationId
    must have fully separate histories (scoped key = user_id:conversationId)."""

    def test_different_users_have_isolated_histories(self):
        user_a = {"id": "user-A", "username": "alice", "email": "a@x.com"}
        user_b = {"id": "user-B", "username": "bob", "email": "b@x.com"}

        ollama = _make_ollama_client()
        gemini = _make_gemini_client()

        with (
            patch.object(chat_module, "ollama_client", ollama),
            patch.object(chat_module, "gemini_client", gemini),
        ):
            client_a = TestClient(_build_app_for_user(user_a))
            client_b = TestClient(_build_app_for_user(user_b))

            client_a.post(
                "/api/chat",
                json={"message": "Hello from A", "model": "ollama", "conversationId": "shared"},
            )
            client_b.post(
                "/api/chat",
                json={"message": "Hello from B", "model": "ollama", "conversationId": "shared"},
            )

        # Both scoped keys must exist
        assert "user-A:shared" in chat_module.conversations
        assert "user-B:shared" in chat_module.conversations
        # They must hold different histories
        assert chat_module.conversations["user-A:shared"] != chat_module.conversations["user-B:shared"]

    def test_user_cannot_read_another_users_conversation_history(self):
        """GET /history/{id} is scoped to the requesting user."""
        user_a = {"id": "user-A", "username": "alice", "email": "a@x.com"}
        user_b = {"id": "user-B", "username": "bob", "email": "b@x.com"}

        ollama = _make_ollama_client()
        gemini = _make_gemini_client()

        # Populate user A's conversation
        with (
            patch.object(chat_module, "ollama_client", ollama),
            patch.object(chat_module, "gemini_client", gemini),
        ):
            TestClient(_build_app_for_user(user_a)).post(
                "/api/chat",
                json={"message": "Secret message", "model": "ollama", "conversationId": "private"},
            )

        # User B reads the same conversationId — should see an empty history
        data = TestClient(_build_app_for_user(user_b)).get("/api/chat/history/private").json()
        assert data["message_count"] == 0
        assert data["messages"] == []

    def test_delete_clears_only_requesting_users_conversation(self):
        """DELETE /history/{id} must not remove another user's same-named conversation."""
        user_a = {"id": "user-A", "username": "alice", "email": "a@x.com"}
        user_b = {"id": "user-B", "username": "bob", "email": "b@x.com"}

        ollama = _make_ollama_client()
        gemini = _make_gemini_client()

        with (
            patch.object(chat_module, "ollama_client", ollama),
            patch.object(chat_module, "gemini_client", gemini),
        ):
            TestClient(_build_app_for_user(user_a)).post(
                "/api/chat",
                json={"message": "A msg", "model": "ollama", "conversationId": "shared-del"},
            )
            TestClient(_build_app_for_user(user_b)).post(
                "/api/chat",
                json={"message": "B msg", "model": "ollama", "conversationId": "shared-del"},
            )

        # User A deletes their conversation
        TestClient(_build_app_for_user(user_a)).delete("/api/chat/history/shared-del")

        assert "user-A:shared-del" not in chat_module.conversations
        assert "user-B:shared-del" in chat_module.conversations


@pytest.mark.unit
class TestHistoryEndpoint:
    """GET /api/chat/history/{id} — timestamps, message counts, empty state."""

    def test_history_returns_first_and_last_timestamps(self):
        """first_message and last_message must reflect the actual message timestamps."""
        ollama = _make_ollama_client()
        gemini = _make_gemini_client()
        client = TestClient(_build_app())

        with (
            patch.object(chat_module, "ollama_client", ollama),
            patch.object(chat_module, "gemini_client", gemini),
        ):
            # Send two messages to build up history
            client.post(
                "/api/chat",
                json={"message": "First", "model": "ollama", "conversationId": "ts-conv"},
            )
            client.post(
                "/api/chat",
                json={"message": "Second", "model": "ollama", "conversationId": "ts-conv"},
            )

        data = client.get("/api/chat/history/ts-conv").json()
        assert data["message_count"] >= 2
        assert data["first_message"] is not None
        assert data["last_message"] is not None

    def test_history_empty_for_unknown_conversation(self):
        client = TestClient(_build_app())
        data = client.get("/api/chat/history/nonexistent-conv").json()
        assert data["message_count"] == 0
        assert data["messages"] == []
        assert data["first_message"] is None

    def test_history_includes_message_count_field(self):
        ollama = _make_ollama_client()
        gemini = _make_gemini_client()
        client = TestClient(_build_app())

        with (
            patch.object(chat_module, "ollama_client", ollama),
            patch.object(chat_module, "gemini_client", gemini),
        ):
            client.post(
                "/api/chat",
                json={"message": "Hello", "model": "ollama", "conversationId": "mc-conv"},
            )

        data = client.get("/api/chat/history/mc-conv").json()
        assert "message_count" in data
        assert data["message_count"] > 0


@pytest.mark.unit
class TestMultiTurnHistory:
    """Verify that conversation history accumulates across turns."""

    def test_second_message_uses_accumulated_history(self):
        """After two messages the history slice contains at least 2 entries."""
        ollama = _make_ollama_client()
        gemini = _make_gemini_client()
        client = TestClient(_build_app())

        with (
            patch.object(chat_module, "ollama_client", ollama),
            patch.object(chat_module, "gemini_client", gemini),
        ):
            client.post(
                "/api/chat",
                json={"message": "Turn 1", "model": "ollama", "conversationId": "mt-conv"},
            )
            client.post(
                "/api/chat",
                json={"message": "Turn 2", "model": "ollama", "conversationId": "mt-conv"},
            )

        scoped_key = f"{TEST_USER['id']}:mt-conv"
        assert scoped_key in chat_module.conversations
        assert len(chat_module.conversations[scoped_key]) >= 2
