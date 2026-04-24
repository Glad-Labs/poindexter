"""Unit tests for the outbound webhook dispatcher (Phase 1b)."""

from __future__ import annotations

from typing import Any

import pytest

from services.integrations import outbound_dispatcher
from services.integrations import registry as registry_module


class _FakePool:
    def __init__(self):
        self.executes: list[tuple[str, tuple]] = []
        self._row: dict[str, Any] | None = None

    def set_row(self, row):
        self._row = row

    async def fetchrow(self, query, *args):
        return self._row

    async def execute(self, query, *args):
        self.executes.append((query, args))


class _FakeDBService:
    def __init__(self, pool):
        self.pool = pool


class _FakeSiteConfig:
    async def get_secret(self, key, default=""):
        return None


def _row(**overrides):
    base = {
        "id": "00000000-0000-0000-0000-000000000099",
        "name": "test_out",
        "direction": "outbound",
        "handler_name": "echo_out",
        "path": None,
        "url": "https://example.com/hook",
        "signing_algorithm": "none",
        "secret_key_ref": None,
        "event_filter": {},
        "enabled": True,
        "config": {},
        "metadata": {},
    }
    base.update(overrides)
    return base


@pytest.fixture(autouse=True)
def _clear_registry():
    saved = dict(registry_module._REGISTRY)
    registry_module._REGISTRY.clear()
    yield
    registry_module._REGISTRY.clear()
    registry_module._REGISTRY.update(saved)


@pytest.mark.asyncio
async def test_unknown_name_raises():
    pool = _FakePool()
    pool.set_row(None)
    db = _FakeDBService(pool)
    with pytest.raises(outbound_dispatcher.OutboundWebhookError):
        await outbound_dispatcher.deliver(
            "missing", {"x": 1}, db_service=db, site_config=_FakeSiteConfig()
        )


@pytest.mark.asyncio
async def test_disabled_raises():
    pool = _FakePool()
    pool.set_row(_row(enabled=False))
    db = _FakeDBService(pool)
    with pytest.raises(outbound_dispatcher.OutboundWebhookError):
        await outbound_dispatcher.deliver(
            "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
        )


@pytest.mark.asyncio
async def test_inbound_row_rejected():
    pool = _FakePool()
    pool.set_row(_row(direction="inbound", url=None))
    db = _FakeDBService(pool)
    with pytest.raises(outbound_dispatcher.OutboundWebhookError):
        await outbound_dispatcher.deliver(
            "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
        )


@pytest.mark.asyncio
async def test_happy_path_dispatches_and_records_success():
    calls: list[dict[str, Any]] = []

    @registry_module.register_handler("outbound", "echo_out")
    async def echo_out(payload, *, site_config, row, pool):
        calls.append(
            {"payload": payload, "row_name": row["name"], "pool": pool}
        )
        return {"delivered": True}

    pool = _FakePool()
    pool.set_row(_row())
    db = _FakeDBService(pool)
    result = await outbound_dispatcher.deliver(
        "test_out", {"content": "hi"}, db_service=db, site_config=_FakeSiteConfig()
    )
    assert result == {"ok": True, "name": "test_out", "delivered": True}
    assert calls[0]["row_name"] == "test_out"
    # One UPDATE for success
    assert len(pool.executes) == 1
    assert "total_success" in pool.executes[0][0]


@pytest.mark.asyncio
async def test_handler_exception_records_failure_and_reraises():
    @registry_module.register_handler("outbound", "boom")
    async def boom(payload, *, site_config, row, pool):
        raise RuntimeError("delivery failed")

    pool = _FakePool()
    pool.set_row(_row(handler_name="boom"))
    db = _FakeDBService(pool)
    with pytest.raises(RuntimeError, match="delivery failed"):
        await outbound_dispatcher.deliver(
            "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
        )
    assert any("last_error" in q for q, _ in pool.executes)


@pytest.mark.asyncio
async def test_unknown_handler_records_failure():
    pool = _FakePool()
    pool.set_row(_row(handler_name="does_not_exist"))
    db = _FakeDBService(pool)
    with pytest.raises(registry_module.HandlerRegistrationError):
        await outbound_dispatcher.deliver(
            "test_out", {}, db_service=db, site_config=_FakeSiteConfig()
        )
    # failure recorded on the row
    assert any("last_error" in q for q, _ in pool.executes)


@pytest.mark.asyncio
async def test_string_payload_accepted_by_contract_passthrough():
    """The dispatcher itself is payload-shape-agnostic; handlers validate."""
    @registry_module.register_handler("outbound", "passthru")
    async def passthru(payload, *, site_config, row, pool):
        return {"received_type": type(payload).__name__}

    pool = _FakePool()
    pool.set_row(_row(handler_name="passthru"))
    db = _FakeDBService(pool)
    result = await outbound_dispatcher.deliver(
        "test_out", "plain string", db_service=db, site_config=_FakeSiteConfig()
    )
    assert result["received_type"] == "str"
