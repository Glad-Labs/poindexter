"""Tests for the media_pipeline + podcast_pipeline graph_def reseed migration.

Pins the fix for the #1876 ``qa.audio`` contract drift that halted the entire
Stage-2 video lane (``dispatch_media_pipeline`` rejected every dispatch because
the stored graph_def stamp d24ed9f4d409 != current 5e1038ae4850). The reseed
must rewrite BOTH graph_defs raw so the boot self-heal re-stamps them.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

_MIGRATION = "20260623_035500_reseed_media_podcast_graph_defs.py"


def _backend_root() -> Path:
    # parents[0]=migrations [1]=services [2]=unit [3]=tests [4]=cofounder_agent
    return Path(__file__).resolve().parents[4]


def _load_migration():
    path = _backend_root() / "services" / "migrations" / _MIGRATION
    spec = importlib.util.spec_from_file_location("reseed_media_podcast_mig", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_specs_contain_qa_audio_node_and_are_raw():
    """Both reseeded specs carry the drifted qa.audio node and are RAW.

    Raw (no ``_contract_fp`` on any node) is the shape the boot self-heal
    accepts — a pre-stamped spec could not un-stick the stale stamp this
    migration exists to clear.
    """
    from services.media_pipeline_spec import MEDIA_PIPELINE_GRAPH_DEF
    from services.podcast_pipeline_spec import PODCAST_PIPELINE_GRAPH_DEF

    for spec in (MEDIA_PIPELINE_GRAPH_DEF, PODCAST_PIPELINE_GRAPH_DEF):
        nodes = spec["nodes"]
        assert any(n.get("atom") == "qa.audio" for n in nodes), (
            "spec must contain the qa.audio node the reseed re-stamps"
        )
        assert all("_contract_fp" not in n for n in nodes), (
            "spec must be raw (unstamped) so the boot self-heal re-stamps it"
        )


async def test_up_rewrites_both_graph_defs_raw():
    """up() issues a raw ``UPDATE`` for media_pipeline AND podcast_pipeline."""
    mod = _load_migration()
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    await mod.up(pool)

    assert conn.execute.await_count == 2
    slugs_written = set()
    for call in conn.execute.await_args_list:
        sql, payload, slug = call.args
        assert "UPDATE pipeline_templates" in sql
        assert "graph_def = $1::jsonb" in sql
        assert "active = true" in sql
        # payload is the raw spec JSON — round-trips and carries no stamp.
        spec = json.loads(payload)
        assert all("_contract_fp" not in n for n in spec["nodes"])
        slugs_written.add(slug)
    assert slugs_written == {"media_pipeline", "podcast_pipeline"}


async def test_down_is_noop():
    """down() must not raise and must not touch the DB (self-heal owns stamping)."""
    mod = _load_migration()
    pool = MagicMock()
    await mod.down(pool)
    pool.acquire.assert_not_called()
