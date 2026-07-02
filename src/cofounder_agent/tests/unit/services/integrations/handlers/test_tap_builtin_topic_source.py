"""Unit tests for the rewritten tap.builtin_topic_source handler (b1)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plugins.topic_source import DiscoveredTopic
from services.integrations.handlers.tap_builtin_topic_source import builtin_topic_source


def _make_pool():
    conn = MagicMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _niche():
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        slug="pc-gaming",
        name="PC Gaming",
        target_audience_tags=["esports"],
    )


@pytest.mark.asyncio
async def test_requires_niche_id():
    pool, _ = _make_pool()
    with pytest.raises(ValueError):
        await builtin_topic_source(
            None, site_config=MagicMock(),
            row={"tap_type": "web_search", "target_table": "topic_pool"},
            pool=pool,
        )


@pytest.mark.asyncio
async def test_dispatches_single_source_with_niche_context_and_inserts():
    pool, _ = _make_pool()
    src = MagicMock()
    src.name = "web_search"

    captured_cfg = {}

    async def _extract(pool_arg, cfg):
        captured_cfg.update(cfg)
        return [DiscoveredTopic(title="GPU news", category="esports", source="ddg_search")]
    src.extract = AsyncMock(side_effect=_extract)

    with patch(
        "services.integrations.handlers.tap_builtin_topic_source.get_topic_sources",
        return_value=[src],
    ), patch(
        "services.integrations.handlers.tap_builtin_topic_source.NicheService",
    ) as NS, patch(
        "services.integrations.handlers.tap_builtin_topic_source.PluginConfig.load",
        AsyncMock(return_value=SimpleNamespace(config={})),
    ), patch(
        "services.integrations.handlers.tap_builtin_topic_source.get_deduplicator",
    ) as GD, patch(
        "services.integrations.handlers.tap_builtin_topic_source.insert_pooled_topics",
        AsyncMock(return_value=1),
    ) as INS:
        NS.return_value.get_by_id = AsyncMock(return_value=_niche())
        GD.return_value.mark_duplicates = AsyncMock(return_value=None)
        result = await builtin_topic_source(
            None, site_config=MagicMock(),
            row={
                "tap_type": "web_search", "target_table": "topic_pool",
                "niche_id": _niche().id, "config": {"categories": ["gaming"]},
            },
            pool=pool,
        )

    assert result == {"records": 1, "source": "web_search"}
    # Niche context reached the source.
    assert captured_cfg["niche_slug"] == "pc-gaming"
    assert captured_cfg["niche_name"] == "PC Gaming"
    assert captured_cfg["target_audience_tags"] == ["esports"]
    # Tap config (categories) layered in.
    assert captured_cfg["categories"] == ["gaming"]
    INS.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_sanity_gate_drops_contentless_topics_and_emits_finding():
    """Junk titles (dots-only, failure sentinels) never reach topic_pool.

    2026-06/07 incidents: a dots-only dev.to headline and a literal
    'No topic found' distiller sentinel entered the pool and burned full
    canonical_blog runs. The sweep-side gates (#2037) stop the task, but
    the ingest seam should keep the pool itself clean.
    """
    pool, _ = _make_pool()
    src = MagicMock()
    src.name = "devto"
    src.extract = AsyncMock(return_value=[
        DiscoveredTopic(title="Real GPU headline here", category="ai", source="devto"),
        DiscoveredTopic(title=". .. . ... . .... . .... . ... .", category="ai", source="devto"),
        DiscoveredTopic(title="No topic found", category="ai", source="devto"),
    ])
    site_config = MagicMock()
    site_config.get_int = MagicMock(return_value=2)

    with patch(
        "services.integrations.handlers.tap_builtin_topic_source.get_topic_sources",
        return_value=[src],
    ), patch(
        "services.integrations.handlers.tap_builtin_topic_source.NicheService",
    ) as NS, patch(
        "services.integrations.handlers.tap_builtin_topic_source.PluginConfig.load",
        AsyncMock(return_value=SimpleNamespace(config={})),
    ), patch(
        "services.integrations.handlers.tap_builtin_topic_source.get_deduplicator",
    ) as GD, patch(
        "services.integrations.handlers.tap_builtin_topic_source.insert_pooled_topics",
        AsyncMock(return_value=1),
    ) as INS, patch(
        "services.integrations.handlers.tap_builtin_topic_source.emit_finding",
    ) as EMIT:
        NS.return_value.get_by_id = AsyncMock(return_value=_niche())
        GD.return_value.mark_duplicates = AsyncMock(return_value=None)
        result = await builtin_topic_source(
            None, site_config=site_config,
            row={
                "tap_type": "devto", "target_table": "topic_pool",
                "niche_id": _niche().id,
            },
            pool=pool,
        )

    assert result == {"records": 1, "source": "devto"}
    # Only the sane topic reached the pool insert.
    inserted_topics = INS.await_args.kwargs["topics"]
    assert [t.title for t in inserted_topics] == ["Real GPU headline here"]
    # One aggregated finding, warn severity, carrying both dropped titles.
    EMIT.assert_called_once()
    kwargs = EMIT.call_args.kwargs
    assert kwargs["kind"] == "topic_sanity_rejected"
    assert kwargs["severity"] == "warn"
    assert len(kwargs["extra"]["dropped"]) == 2


@pytest.mark.asyncio
async def test_ingest_sanity_gate_noop_when_all_topics_sane():
    """Sane topics pass through untouched and no finding is emitted."""
    pool, _ = _make_pool()
    src = MagicMock()
    src.name = "hackernews"
    src.extract = AsyncMock(return_value=[
        DiscoveredTopic(title="Local LLM inference on consumer GPUs", category="ai", source="hackernews"),
    ])
    site_config = MagicMock()
    site_config.get_int = MagicMock(return_value=2)

    with patch(
        "services.integrations.handlers.tap_builtin_topic_source.get_topic_sources",
        return_value=[src],
    ), patch(
        "services.integrations.handlers.tap_builtin_topic_source.NicheService",
    ) as NS, patch(
        "services.integrations.handlers.tap_builtin_topic_source.PluginConfig.load",
        AsyncMock(return_value=SimpleNamespace(config={})),
    ), patch(
        "services.integrations.handlers.tap_builtin_topic_source.get_deduplicator",
    ) as GD, patch(
        "services.integrations.handlers.tap_builtin_topic_source.insert_pooled_topics",
        AsyncMock(return_value=1),
    ) as INS, patch(
        "services.integrations.handlers.tap_builtin_topic_source.emit_finding",
    ) as EMIT:
        NS.return_value.get_by_id = AsyncMock(return_value=_niche())
        GD.return_value.mark_duplicates = AsyncMock(return_value=None)
        await builtin_topic_source(
            None, site_config=site_config,
            row={
                "tap_type": "hackernews", "target_table": "topic_pool",
                "niche_id": _niche().id,
            },
            pool=pool,
        )

    inserted_topics = INS.await_args.kwargs["topics"]
    assert len(inserted_topics) == 1
    EMIT.assert_not_called()


@pytest.mark.asyncio
async def test_unregistered_source_fails_loud():
    pool, _ = _make_pool()
    with patch(
        "services.integrations.handlers.tap_builtin_topic_source.get_topic_sources",
        return_value=[],
    ), patch(
        "services.integrations.handlers.tap_builtin_topic_source.NicheService",
    ) as NS:
        NS.return_value.get_by_id = AsyncMock(return_value=_niche())
        with pytest.raises(ValueError):
            await builtin_topic_source(
                None, site_config=MagicMock(),
                row={"tap_type": "nope", "target_table": "topic_pool", "niche_id": _niche().id},
                pool=pool,
            )
