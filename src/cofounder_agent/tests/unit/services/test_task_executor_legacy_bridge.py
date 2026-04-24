"""Tests for the legacy notification bridge (Phase 1d).

Confirms ``_try_outbound_deliver`` routes through the integrations
framework when a matching enabled row exists, and cleanly falls back
when the framework is unavailable or the row is disabled.
"""

from __future__ import annotations

from typing import Any

import pytest

from services.integrations import registry as registry_module
from services.integrations import shared_context
from services.task_executor import _try_outbound_deliver


class _FakePool:
    def __init__(self):
        self._row: dict[str, Any] | None = None
        self.executes: list[tuple[str, tuple]] = []

    def set_row(self, row):
        self._row = row

    async def fetchrow(self, query, *args):
        return self._row

    async def execute(self, query, *args):
        self.executes.append((query, args))


class _FakeDBService:
    def __init__(self, pool):
        self.pool = pool


def _row(enabled=True, handler_name="discord_post", direction="outbound"):
    return {
        "id": "00000000-0000-0000-0000-000000000010",
        "name": "discord_ops",
        "direction": direction,
        "handler_name": handler_name,
        "path": None,
        "url": "https://discord.com/api/webhooks/x/y",
        "signing_algorithm": "none",
        "secret_key_ref": None,
        "event_filter": {},
        "enabled": enabled,
        "config": {},
        "metadata": {},
    }


@pytest.fixture(autouse=True)
def _isolation():
    saved_reg = dict(registry_module._REGISTRY)
    registry_module._REGISTRY.clear()
    shared_context.clear_database_service()
    yield
    registry_module._REGISTRY.clear()
    registry_module._REGISTRY.update(saved_reg)
    shared_context.clear_database_service()


@pytest.mark.asyncio
async def test_framework_not_registered_returns_false():
    """If nobody called set_database_service, bridge returns False
    so the caller's legacy path runs."""
    result = await _try_outbound_deliver("discord_ops", "hi", site_config=object())
    assert result is False


@pytest.mark.asyncio
async def test_pool_unset_returns_false():
    """DB service registered but pool is None (pre-initialize)."""
    class _NoPool:
        pool = None
    shared_context.set_database_service(_NoPool())
    result = await _try_outbound_deliver("discord_ops", "hi", site_config=object())
    assert result is False


@pytest.mark.asyncio
async def test_row_disabled_returns_false_without_raising():
    pool = _FakePool()
    pool.set_row(_row(enabled=False))
    shared_context.set_database_service(_FakeDBService(pool))

    @registry_module.register_handler("outbound", "discord_post")
    async def _handler(payload, *, site_config, row, pool):
        raise AssertionError("should not be invoked")

    result = await _try_outbound_deliver("discord_ops", "hi", site_config=object())
    assert result is False


@pytest.mark.asyncio
async def test_row_missing_returns_false():
    pool = _FakePool()
    pool.set_row(None)
    shared_context.set_database_service(_FakeDBService(pool))
    result = await _try_outbound_deliver("discord_ops", "hi", site_config=object())
    assert result is False


@pytest.mark.asyncio
async def test_happy_path_delivers_and_returns_true():
    calls: list[Any] = []

    @registry_module.register_handler("outbound", "discord_post")
    async def discord_post(payload, *, site_config, row, pool):
        calls.append(payload)
        return {"status_code": 204}

    pool = _FakePool()
    pool.set_row(_row())
    shared_context.set_database_service(_FakeDBService(pool))
    result = await _try_outbound_deliver("discord_ops", "hello", site_config=object())
    assert result is True
    assert calls == ["hello"]
    # counter update recorded
    assert any("total_success" in q for q, _ in pool.executes)


@pytest.mark.asyncio
async def test_handler_exception_returns_false_so_fallback_runs():
    @registry_module.register_handler("outbound", "discord_post")
    async def boom(payload, *, site_config, row, pool):
        raise RuntimeError("network gone")

    pool = _FakePool()
    pool.set_row(_row())
    shared_context.set_database_service(_FakeDBService(pool))
    result = await _try_outbound_deliver("discord_ops", "hi", site_config=object())
    # Handler raised -> bridge returns False so the caller falls back
    assert result is False
    # Failure recorded on the row
    assert any("last_error" in q for q, _ in pool.executes)
