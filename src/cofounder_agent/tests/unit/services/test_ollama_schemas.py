"""
Unit tests for ollama_schemas.py

Tests field validation and model behaviour for Ollama integration schemas.
"""

import pytest
from pydantic import ValidationError

from schemas.ollama_schemas import OllamaHealthResponse, OllamaModelSelection, OllamaWarmupResponse


@pytest.mark.unit
class TestOllamaHealthResponse:
    def test_valid_connected(self):
        resp = OllamaHealthResponse(
            connected=True,
            status="online",
            message="Ollama is running",
            timestamp="2026-01-01T10:00:00Z",
        )
        assert resp.connected is True
        assert resp.models is None

    def test_valid_with_models(self):
        resp = OllamaHealthResponse(
            connected=True,
            status="online",
            models=["llama2", "mistral"],
            message="2 models available",
            timestamp="2026-01-01T10:00:00Z",
        )
        assert resp.models == ["llama2", "mistral"]

    def test_not_connected(self):
        resp = OllamaHealthResponse(
            connected=False,
            status="offline",
            message="Cannot connect to Ollama",
            timestamp="2026-01-01T10:00:00Z",
        )
        assert resp.connected is False

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            OllamaHealthResponse(  # type: ignore[call-arg]
                connected=True,
                status="online",
                # missing message
                timestamp="2026-01-01T10:00:00Z",
            )


@pytest.mark.unit
class TestOllamaWarmupResponse:
    def test_valid(self):
        resp = OllamaWarmupResponse(
            status="ready",
            model="llama2",
            message="Model warmed up",
            timestamp="2026-01-01T10:00:00Z",
        )
        assert resp.generation_time is None

    def test_with_generation_time(self):
        resp = OllamaWarmupResponse(
            status="ready",
            model="mistral",
            message="Model ready",
            generation_time=1.23,
            timestamp="2026-01-01T10:00:00Z",
        )
        assert resp.generation_time == 1.23

    def test_missing_model_raises(self):
        with pytest.raises(ValidationError):
            OllamaWarmupResponse(  # type: ignore[call-arg]
                status="ready",
                message="Ready",
                timestamp="2026-01-01T10:00:00Z",
            )


@pytest.mark.unit
class TestOllamaModelSelection:
    def test_valid(self):
        sel = OllamaModelSelection(model="llama2")
        assert sel.model == "llama2"

    def test_missing_model_raises(self):
        with pytest.raises(ValidationError):
            OllamaModelSelection()  # type: ignore[call-arg]
