"""
Unit tests for chat_schemas.py

Tests field validation and model behaviour for chat schemas.
"""

import pytest
from pydantic import ValidationError

from schemas.chat_schemas import (
    ChatMessage,
    ChatRequest,
    ChatResponse,
)


@pytest.mark.unit
class TestChatMessage:
    def test_valid_user_message(self):
        msg = ChatMessage(content="Hello, how are you?")
        assert msg.role == "user"
        assert msg.timestamp is None

    def test_assistant_role(self):
        msg = ChatMessage(content="I am doing well.", role="assistant")
        assert msg.role == "assistant"

    def test_with_timestamp(self):
        msg = ChatMessage(
            content="Hi",
            role="user",
            timestamp="2026-01-01T10:00:00Z",
        )
        assert msg.timestamp == "2026-01-01T10:00:00Z"

    def test_missing_content_raises(self):
        with pytest.raises(ValidationError):
            ChatMessage()  # type: ignore[call-arg]


@pytest.mark.unit
class TestChatRequest:
    def test_valid_minimal(self):
        req = ChatRequest(message="Tell me about AI")
        assert req.model == "ollama"
        assert req.conversationId == "default"
        assert req.temperature == 0.7
        assert req.max_tokens == 500

    def test_custom_model(self):
        req = ChatRequest(message="Hello", model="claude")
        assert req.model == "claude"

    def test_custom_conversation_id(self):
        req = ChatRequest(message="Hello", conversationId="conv-123")
        assert req.conversationId == "conv-123"

    def test_temperature_bounds(self):
        # Valid bounds
        req = ChatRequest(message="Hello", temperature=0.0)
        assert req.temperature == 0.0
        req = ChatRequest(message="Hello", temperature=2.0)
        assert req.temperature == 2.0

    def test_temperature_too_high_raises(self):
        with pytest.raises(ValidationError):
            ChatRequest(message="Hello", temperature=2.1)

    def test_temperature_negative_raises(self):
        with pytest.raises(ValidationError):
            ChatRequest(message="Hello", temperature=-0.1)

    def test_max_tokens_minimum(self):
        req = ChatRequest(message="Hello", max_tokens=1)
        assert req.max_tokens == 1

    def test_max_tokens_maximum(self):
        req = ChatRequest(message="Hello", max_tokens=4000)
        assert req.max_tokens == 4000

    def test_max_tokens_too_high_raises(self):
        with pytest.raises(ValidationError):
            ChatRequest(message="Hello", max_tokens=4001)

    def test_max_tokens_zero_raises(self):
        with pytest.raises(ValidationError):
            ChatRequest(message="Hello", max_tokens=0)

    def test_missing_message_raises(self):
        with pytest.raises(ValidationError):
            ChatRequest()  # type: ignore[call-arg]


@pytest.mark.unit
class TestChatResponse:
    def test_valid(self):
        resp = ChatResponse(
            response="Here is your answer.",
            model="ollama",
            conversationId="conv-123",
            timestamp="2026-01-01T10:00:00Z",
        )
        assert resp.tokens_used is None
        assert resp.cached is False

    def test_with_optional_fields(self):
        resp = ChatResponse(
            response="Here is your answer.",
            model="claude",
            conversationId="default",
            timestamp="2026-01-01T10:00:00Z",
            tokens_used=150,
            cached=True,
        )
        assert resp.tokens_used == 150
        assert resp.cached is True

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            ChatResponse(  # type: ignore[call-arg]
                response="Answer",
                model="ollama",
                # missing conversationId
                timestamp="2026-01-01T10:00:00Z",
            )
