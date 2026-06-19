"""Contract test for the re-stamp migration (poindexter#755).

up() must recompute each active graph_def's per-node contract fingerprints from
the live registry and UPDATE the row in place. It must be smoke-safe: if the
atom registry can't be discovered (migrations-smoke has no full app boot), it
skips without error. down() is a no-op (stamps are additive node keys).
"""
from __future__ import annotations

import importlib.util
import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.atom import AtomMeta

_MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "services"
    / "migrations"
    / "20260619_041634_restamp_active_graph_defs_with_atom_contract_fingerprints.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("restamp_mig", _MIGRATION)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mock_pool(rows):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


def test_migration_file_exists():
    assert _MIGRATION.exists(), f"migration file missing: {_MIGRATION}"


@pytest.mark.asyncio
async def test_up_stamps_active_rows(monkeypatch):
    mod = _load_migration()
    import services.atom_registry as reg
    import services.pipeline_architect as pa

    monkeypatch.setattr(reg, "discover", lambda: None)
    monkeypatch.setattr(
        pa,
        "get_atom_meta",
        lambda n: AtomMeta(
            name=n, type="atom", version="1.0.0", description="d", produces=("draft",)
        ),
    )

    rows = [
        {
            "slug": "canonical_blog",
            "graph_def": json.dumps(
                {
                    "name": "canonical_blog",
                    "nodes": [{"id": "a", "atom": "atoms.draft", "config": {}}],
                    "edges": [{"from": "a", "to": "END"}],
                }
            ),
        }
    ]
    pool, conn = _mock_pool(rows)
    await mod.up(pool)

    assert conn.execute.await_count == 1
    sql, *params = conn.execute.call_args[0]
    assert "UPDATE pipeline_templates" in sql
    assert any("_contract_fp" in str(p) for p in params)


@pytest.mark.asyncio
async def test_up_is_noop_when_registry_unavailable(monkeypatch):
    mod = _load_migration()
    import services.atom_registry as reg

    def _boom():
        raise ImportError("no atoms in smoke env")

    monkeypatch.setattr(reg, "discover", _boom)
    pool, conn = _mock_pool([])
    # Must not raise — smoke-safe.
    await mod.up(pool)
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_down_is_noop():
    mod = _load_migration()
    pool, conn = _mock_pool([])
    await mod.down(pool)
    conn.execute.assert_not_awaited()
