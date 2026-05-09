"""Unit tests for ``services.llm_providers.dispatcher.resolve_tier_model``.

Pin the no-silent-defaults contract: an unknown tier or a missing
``app_settings.cost_tier.<tier>.model`` row must raise loudly. Lane B
sweep agents lean on this to know they can replace literal model names
with ``await resolve_tier_model(pool, tier)`` without smuggling in a
fallback path.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.llm_providers.dispatcher import resolve_tier_model


class _FakeConn:
    def __init__(self, value: str | None):
        self._value = value

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass

    async def fetchval(self, query: str, *args: Any) -> str | None:
        return self._value


class _FakePool:
    def __init__(self, value: str | None):
        self._value = value

    def acquire(self):
        return _FakeConn(self._value)


@pytest.mark.asyncio
async def test_returns_configured_model():
    pool = _FakePool("ollama/gemma3:27b")
    assert await resolve_tier_model(pool, "standard") == "ollama/gemma3:27b"


@pytest.mark.asyncio
async def test_strips_whitespace():
    pool = _FakePool("  ollama/gemma3:27b  ")
    assert await resolve_tier_model(pool, "standard") == "ollama/gemma3:27b"


@pytest.mark.asyncio
async def test_raises_on_unknown_tier():
    pool = _FakePool("ollama/gemma3:27b")
    with pytest.raises(ValueError, match="unknown tier"):
        await resolve_tier_model(pool, "platinum")


@pytest.mark.asyncio
async def test_raises_on_missing_row():
    pool = _FakePool(None)
    with pytest.raises(RuntimeError, match="no model configured"):
        await resolve_tier_model(pool, "standard")


@pytest.mark.asyncio
async def test_raises_on_empty_row():
    pool = _FakePool("")
    with pytest.raises(RuntimeError, match="no model configured"):
        await resolve_tier_model(pool, "standard")


@pytest.mark.asyncio
async def test_all_four_tiers_accepted():
    pool = _FakePool("ollama/test:1")
    for tier in ("free", "budget", "standard", "premium"):
        assert await resolve_tier_model(pool, tier) == "ollama/test:1"
