"""Contract tests for scripts/ops_sessions/_common.py pure helpers."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _ops_dir() -> Path:
    return next(
        p for p in Path(__file__).resolve().parents
        if (p / "scripts" / "ops_sessions").exists()
    ) / "scripts" / "ops_sessions"


sys.path.insert(0, str(_ops_dir()))
import _common  # noqa: E402


def test_parse_ollama_content_extracts_message():
    payload = {"message": {"role": "assistant", "content": "hello"}, "done": True}
    assert _common.parse_ollama_content(payload) == "hello"


def test_parse_ollama_content_missing_raises():
    with pytest.raises(KeyError):
        _common.parse_ollama_content({"done": True})


def test_ollama_unavailable_is_runtimeerror():
    assert issubclass(_common.OllamaUnavailable, RuntimeError)
