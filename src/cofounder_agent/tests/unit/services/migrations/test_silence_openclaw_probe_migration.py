"""Tests for migration 20260527_024058 — silence openclaw probe.

Pins the contract for the 2026-05-27 fix that appended
``openclaw_gateway_url`` to ``operator_url_probe_skip_keys``. The
migration is logic-light but ships an idempotency contract worth
guarding: re-running on a converged DB must not duplicate the key,
and a missing source row must not crash the runner.
"""

from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest


_MIGRATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "services"
    / "migrations"
    / "20260527_024058_silence_openclaw_probe_pending_upstream_fix.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "m_20260527_024058", _MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_pool(current_value: str | None):
    """asyncpg pool stub that tracks fetchval + execute calls."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=current_value)
    conn.execute = AsyncMock(return_value="UPDATE 1")

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


@pytest.mark.asyncio
async def test_appends_openclaw_when_missing() -> None:
    mod = _load_migration()
    pool, conn = _make_pool(
        "alpha_url,bravo_url,charlie_url",
    )

    await mod.up(pool)

    # Should have called execute once with the new value, alphabetised.
    assert conn.execute.await_count == 1
    args, _kwargs = conn.execute.call_args
    new_value = args[1]
    assert "openclaw_gateway_url" in new_value.split(",")
    # Alphabetised sort is the convention of the existing seed row.
    assert new_value == ",".join(sorted(new_value.split(",")))


@pytest.mark.asyncio
async def test_no_op_when_already_present() -> None:
    mod = _load_migration()
    pool, conn = _make_pool(
        "alpha_url,openclaw_gateway_url,zulu_url",
    )

    await mod.up(pool)

    # No execute call when the key is already present — idempotent.
    assert conn.execute.await_count == 0


@pytest.mark.asyncio
async def test_no_op_when_row_missing() -> None:
    """If ``operator_url_probe_skip_keys`` was never seeded, the
    migration must no-op gracefully — not crash the runner."""
    mod = _load_migration()
    pool, conn = _make_pool(None)

    await mod.up(pool)

    assert conn.execute.await_count == 0


@pytest.mark.asyncio
async def test_down_removes_openclaw() -> None:
    mod = _load_migration()
    pool, conn = _make_pool(
        "alpha_url,openclaw_gateway_url,zulu_url",
    )

    await mod.down(pool)

    assert conn.execute.await_count == 1
    args, _kwargs = conn.execute.call_args
    new_value = args[1]
    assert "openclaw_gateway_url" not in new_value.split(",")
    assert "alpha_url" in new_value.split(",")
    assert "zulu_url" in new_value.split(",")


@pytest.mark.asyncio
async def test_down_no_op_when_absent() -> None:
    mod = _load_migration()
    pool, conn = _make_pool(
        "alpha_url,zulu_url",
    )

    await mod.down(pool)

    assert conn.execute.await_count == 0
