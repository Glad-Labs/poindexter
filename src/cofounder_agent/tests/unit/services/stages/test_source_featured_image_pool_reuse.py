"""Contract test for the 2026-05-27 pool-exhaustion fix.

Pins the architecture-audit finding:
``_load_recent_published_styles`` previously opened a raw
``asyncpg.connect`` on every image-stage run (one per published post).
Under burst load the unbounded connections starved the Postgres
connection budget (default 100). The fix routes through the
lifespan pool when available via ``site_config._pool``.

A regression that re-introduces the raw connect would surface as
"connection slots taken" Postgres errors under content-burst
load — visible in Loki and on the Postgres metrics dashboard but
the kind of slow-burn issue that fails at the worst moment.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_pool_with_rows(rows: list[dict[str, Any]]) -> Any:
    """asyncpg pool stub that records fetch calls + returns canned rows."""
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=rows)

    pool = MagicMock()

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool, conn


@pytest.mark.asyncio
async def test_uses_lifespan_pool_when_available() -> None:
    """``site_config._pool`` is the lifespan-bound asyncpg pool. When
    set, the helper MUST acquire from it instead of opening a fresh
    connection. Pre-fix this opened a raw ``asyncpg.connect`` on every
    run — pool exhaustion at modest concurrency."""
    from modules.content.stages.source_featured_image import (
        _load_recent_published_styles,
    )

    pool, conn = _make_pool_with_rows([
        {"style": "photorealistic"},
        {"style": "isometric"},
    ])

    site_config = MagicMock()
    site_config._pool = pool
    site_config.get = MagicMock(side_effect=lambda k, d="": d)

    # Patch asyncpg.connect to detect any fallback to the raw path.
    with patch("asyncpg.connect", new=AsyncMock()) as raw_connect:
        styles = await _load_recent_published_styles(site_config)

    assert styles == ["photorealistic", "isometric"]
    assert conn.fetch.await_count == 1
    assert raw_connect.await_count == 0, (
        "_load_recent_published_styles fell back to raw asyncpg.connect "
        "despite the lifespan pool being available. Pre-fix behaviour "
        "regressed — pool exhaustion under load."
    )


@pytest.mark.asyncio
async def test_falls_back_to_raw_connect_when_no_pool() -> None:
    """Tests / early-boot CLI may not have a pool wired. The helper
    must still fetch via the legacy raw connect path so single-shot
    callers don't regress."""
    from modules.content.stages.source_featured_image import (
        _load_recent_published_styles,
    )

    site_config = MagicMock()
    site_config._pool = None
    site_config.get = MagicMock(
        side_effect=lambda key, default="": (
            "postgresql://test" if key == "database_url" else default
        ),
    )

    fake_conn = MagicMock()
    fake_conn.fetch = AsyncMock(return_value=[{"style": "moody"}])
    fake_conn.close = AsyncMock(return_value=None)

    with patch("asyncpg.connect", new=AsyncMock(return_value=fake_conn)) as raw_connect:
        styles = await _load_recent_published_styles(site_config)

    assert styles == ["moody"]
    assert raw_connect.await_count == 1


@pytest.mark.asyncio
async def test_pool_error_falls_back_gracefully() -> None:
    """If the pool ``acquire()`` raises, the helper falls back to the
    raw connect path rather than failing the stage. Defensive — pool
    hiccup shouldn't take down image generation."""
    from modules.content.stages.source_featured_image import (
        _load_recent_published_styles,
    )

    broken_pool = MagicMock()

    @asynccontextmanager
    async def _broken_acquire():
        raise RuntimeError("pool exhausted")
        yield  # unreachable but required by generator contract

    broken_pool.acquire = _broken_acquire

    site_config = MagicMock()
    site_config._pool = broken_pool
    site_config.get = MagicMock(
        side_effect=lambda key, default="": (
            "postgresql://test" if key == "database_url" else default
        ),
    )

    fake_conn = MagicMock()
    fake_conn.fetch = AsyncMock(return_value=[{"style": "fallback-style"}])
    fake_conn.close = AsyncMock(return_value=None)

    with patch("asyncpg.connect", new=AsyncMock(return_value=fake_conn)):
        styles = await _load_recent_published_styles(site_config)

    assert styles == ["fallback-style"]


@pytest.mark.asyncio
async def test_no_site_config_returns_empty() -> None:
    """Stages running outside DI return empty — no surprise database
    activity from the inline-style picker."""
    from modules.content.stages.source_featured_image import (
        _load_recent_published_styles,
    )

    styles = await _load_recent_published_styles(None)
    assert styles == []
