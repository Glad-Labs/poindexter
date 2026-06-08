"""Contract test for the media_pipeline seed migration (poindexter#689).

The migration must INSERT a media_pipeline row into pipeline_templates with an
ON CONFLICT (slug) upsert (idempotent replay), and down() must remove it. The
graph_def source module must be pure data so migrations-smoke can import it
without a full app boot.
"""
from __future__ import annotations

import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

_MIGRATION = (
    Path(__file__).resolve().parents[4]
    / "services"
    / "migrations"
    / "20260608_120000_seed_media_pipeline_graph_def.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("seed_media_pipeline_mig", _MIGRATION)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _mock_pool():
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


def test_migration_file_exists():
    assert _MIGRATION.exists(), f"migration file missing: {_MIGRATION}"


@pytest.mark.asyncio
async def test_up_upserts_media_pipeline_template():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.up(pool)

    sql, *params = conn.execute.call_args[0]
    assert "INSERT INTO pipeline_templates" in sql
    assert "'media_pipeline'" in sql
    assert "ON CONFLICT (slug) DO UPDATE" in sql
    # graph_def json is passed as a param (not string-interpolated).
    assert any("load_scripts" in str(p) for p in params)


@pytest.mark.asyncio
async def test_down_deletes_media_pipeline_template():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.down(pool)

    sql = conn.execute.call_args[0][0]
    assert "DELETE FROM pipeline_templates" in sql
    assert "media_pipeline" in sql


def test_graph_def_is_pure_data_and_well_formed():
    from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF

    assert MEDIA_PIPELINE_GRAPH_DEF["name"] == "media_pipeline"
    assert MEDIA_PIPELINE_GRAPH_DEF["entry"] == "load_scripts"
    node_atoms = {n["atom"] for n in MEDIA_PIPELINE_GRAPH_DEF["nodes"]}
    assert "media.load_scripts" in node_atoms
