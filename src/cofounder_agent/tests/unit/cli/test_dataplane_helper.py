"""Tests for the shared data-plane CLI helper ``poindexter/cli/_dataplane.py``
(#1522). The helper owns pool lifecycle so the 5 data-plane CLI modules stop
hand-rolling ``asyncpg.connect`` + raw SQL — they delegate to
``declarative_config_service`` through it.
"""

from __future__ import annotations

import pytest

from poindexter.cli import _dataplane


def test_run_service_opens_runs_and_closes_pool(monkeypatch):
    events: list[str] = []

    class _Pool:
        async def close(self):
            events.append("closed")

    async def _create_pool(*_a, **_k):
        events.append("created")
        return _Pool()

    monkeypatch.setattr("asyncpg.create_pool", _create_pool)
    monkeypatch.setattr(_dataplane, "_dsn", lambda: "postgresql://x")

    async def _factory(pool):
        events.append("ran")
        return "result"

    out = _dataplane.run_service(_factory)
    assert out == "result"
    assert events == ["created", "ran", "closed"]


def test_run_service_closes_pool_even_on_error(monkeypatch):
    events: list[str] = []

    class _Pool:
        async def close(self):
            events.append("closed")

    async def _create_pool(*_a, **_k):
        return _Pool()

    monkeypatch.setattr("asyncpg.create_pool", _create_pool)
    monkeypatch.setattr(_dataplane, "_dsn", lambda: "postgresql://x")

    async def _boom(pool):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        _dataplane.run_service(_boom)
    assert events == ["closed"]  # pool still closed despite the error
