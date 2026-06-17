"""Unit tests for ``services.declarative_config_service`` (#1522).

The generic declarative-config service is the single owner of CRUD over the
5 data-plane tables (external_taps / retention_policies / webhook_endpoints /
publishing_adapters / qa_gates). These tests pin the registry invariants and
the CRUD behavior (read path, upsert whitelisting + jsonb handling +
injection-safety, delete) against a fake asyncpg pool — no live DB.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.declarative_config_service import (
    _SURFACES,
    SurfaceSpec,
    SurfaceValidationError,
    UnknownSurfaceError,
    delete_row,
    get_row,
    list_rows,
    resolve_surface,
    upsert_row,
)


def _make_pool(*, fetch=None, fetchrow=None, execute=None):
    """Fake asyncpg pool: ``async with pool.acquire() as conn`` yielding a
    conn whose ``fetch`` / ``fetchrow`` / ``execute`` are AsyncMocks. Mirrors
    the ``_make_mock_pool`` idiom in ``test_topic_batch_service.py``."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[] if fetch is None else fetch)
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.execute = AsyncMock(return_value=execute)

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool = MagicMock()
    pool.acquire = _acquire
    return pool, conn


def test_all_five_surfaces_registered():
    assert set(_SURFACES) == {"taps", "retention", "webhooks", "publishers", "qa-gates"}


def test_resolve_surface_returns_spec():
    spec = resolve_surface("taps")
    assert isinstance(spec, SurfaceSpec)
    assert spec.table == "external_taps"
    assert spec.key_column == "name"


def test_resolve_unknown_surface_raises():
    with pytest.raises(UnknownSurfaceError):
        resolve_surface("nope")


def test_key_column_is_always_in_mutable_columns():
    for spec in _SURFACES.values():
        assert spec.key_column in spec.mutable_columns


def test_json_columns_subset_of_mutable():
    for spec in _SURFACES.values():
        assert spec.json_columns <= set(spec.mutable_columns)


# --- read path: list_rows / get_row -------------------------------------


async def test_list_rows_selects_from_registry_table():
    pool, conn = _make_pool(fetch=[{"name": "rss", "enabled": True}])
    rows = await list_rows(pool, "taps")
    assert rows == [{"name": "rss", "enabled": True}]
    sql = conn.fetch.await_args.args[0]
    assert "external_taps" in sql


async def test_list_rows_unknown_surface_raises():
    pool, _ = _make_pool()
    with pytest.raises(UnknownSurfaceError):
        await list_rows(pool, "nope")


async def test_get_row_returns_none_when_missing():
    pool, _ = _make_pool(fetchrow=None)
    assert await get_row(pool, "taps", "missing") is None


async def test_get_row_deserializes_jsonb_string():
    # asyncpg can hand back jsonb as a text string; the service must
    # deserialize json_columns to a dict (mirrors qa_gates_db tolerance).
    pool, _ = _make_pool(fetchrow={"name": "rss", "config": '{"url": "x"}'})
    row = await get_row(pool, "taps", "rss")
    assert row is not None
    assert row["config"] == {"url": "x"}


async def test_list_rows_applies_equality_filter_on_known_columns_only():
    # A known column becomes a bound WHERE clause; an unknown key is dropped
    # (never interpolated) — the registry is the only source of identifiers.
    pool, conn = _make_pool(fetch=[])
    await list_rows(pool, "taps", filters={"enabled": True, "bogus": "x"})
    sql = conn.fetch.await_args.args[0]
    bound = conn.fetch.await_args.args[1:]
    assert "enabled =" in sql
    assert "bogus" not in sql
    assert True in bound


# --- write path: upsert_row ---------------------------------------------


async def test_upsert_whitelists_columns():
    # Telemetry + unknown columns must never reach the SQL.
    pool, conn = _make_pool(fetchrow={"name": "rss", "enabled": True})
    await upsert_row(
        pool, "taps",
        {"name": "rss", "enabled": True, "total_runs": 999, "bogus": "x"},
    )
    sql = conn.fetchrow.await_args.args[0]
    assert "total_runs" not in sql
    assert "bogus" not in sql
    assert "name" in sql and "enabled" in sql


async def test_upsert_requires_key_column():
    pool, _ = _make_pool()
    with pytest.raises(SurfaceValidationError):
        await upsert_row(pool, "taps", {"enabled": True})  # no name


async def test_upsert_serializes_json_columns_with_cast():
    pool, conn = _make_pool(fetchrow={"name": "rss"})
    await upsert_row(pool, "taps", {"name": "rss", "config": {"url": "x"}})
    sql = conn.fetchrow.await_args.args[0]
    bound = conn.fetchrow.await_args.args[1:]
    assert "::jsonb" in sql
    assert '{"url": "x"}' in bound  # json.dumps'd, not the raw dict


async def test_upsert_injection_attempt_is_inert():
    pool, conn = _make_pool(fetchrow={"name": "rss"})
    await upsert_row(
        pool, "taps",
        {"name": "rss", "enabled; DROP TABLE external_taps; --": True},
    )
    sql = conn.fetchrow.await_args.args[0]
    assert "DROP TABLE" not in sql


async def test_upsert_sets_updated_at():
    pool, conn = _make_pool(fetchrow={"name": "rss"})
    await upsert_row(pool, "taps", {"name": "rss", "enabled": True})
    sql = conn.fetchrow.await_args.args[0]
    assert "updated_at = now()" in sql


async def test_upsert_returns_deserialized_row():
    pool, _ = _make_pool(fetchrow={"name": "rss", "config": '{"a": 1}'})
    row = await upsert_row(pool, "taps", {"name": "rss"})
    assert row["config"] == {"a": 1}


# --- delete path: delete_row --------------------------------------------


async def test_delete_returns_true_on_hit():
    pool, _ = _make_pool(execute="DELETE 1")
    assert await delete_row(pool, "taps", "rss") is True


async def test_delete_returns_false_on_miss():
    pool, _ = _make_pool(execute="DELETE 0")
    assert await delete_row(pool, "taps", "missing") is False
