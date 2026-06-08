"""Contract test for the media_pipeline qa-node re-seed migration (Plan 6).

The re-seed must INSERT…ON CONFLICT (slug) DO UPDATE the ``media_pipeline`` row
in ``pipeline_templates`` so an already-seeded caption-chain row is upgraded in
place to the Plan-6 graph_def (now carrying the media_qa node). It must NOT
delete on down() — the row is intentionally retained (re-seed, not create). The
graph_def source module must stay pure data so migrations-smoke can import it
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
    / "20260608_150000_reseed_media_pipeline_qa_node.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("reseed_media_qa_mig", _MIGRATION)
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
async def test_up_upserts_media_pipeline_with_qa_node():
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.up(pool)

    sql, *params = conn.execute.call_args[0]
    assert "INSERT INTO pipeline_templates" in sql
    assert "'media_pipeline'" in sql
    assert "ON CONFLICT (slug) DO UPDATE" in sql
    # The graph_def is passed as a param (not string-interpolated) and now
    # carries the media_qa node id — the substance of this re-seed.
    graph_blob = " ".join(str(p) for p in params)
    assert "media_qa" in graph_blob
    # The prior chain is still present (re-seed upgrades, doesn't drop nodes).
    assert "transcribe_narration" in graph_blob
    assert "render_long_video" in graph_blob
    assert "render_short_video" in graph_blob
    assert "load_scripts" in graph_blob


@pytest.mark.asyncio
async def test_down_does_not_delete_the_row():
    """down() is a no-op re-seed reversal — the row stays. A DELETE here would
    wrongly drop a template a prior migration created."""
    mod = _load_migration()
    pool, conn = _mock_pool()

    await mod.down(pool)

    for call in conn.execute.call_args_list:
        sql = call[0][0] if call[0] else ""
        assert "DELETE" not in sql.upper()


def test_graph_def_carries_qa_node():
    from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF

    node_ids = {n["id"] for n in MEDIA_PIPELINE_GRAPH_DEF["nodes"]}
    assert "media_qa" in node_ids
    node_atoms = {n["atom"] for n in MEDIA_PIPELINE_GRAPH_DEF["nodes"]}
    assert "media.qa" in node_atoms
    # The media_qa node sits AFTER render_short_video and terminates the graph.
    edges = {(e["from"], e["to"]) for e in MEDIA_PIPELINE_GRAPH_DEF["edges"]}
    assert ("render_short_video", "media_qa") in edges
    assert ("media_qa", "END") in edges
