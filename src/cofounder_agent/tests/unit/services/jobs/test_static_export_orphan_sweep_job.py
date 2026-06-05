"""Unit tests for ``services/jobs/static_export_orphan_sweep.py``.

The janitor retires per-post JSONs whose slug is no longer published.
Outcomes worth pinning:

1. **Orphans present** — exported slugs not in the published set are each
   retired (R2 delete + ISR revalidate); published slugs are left alone.
2. **No orphans** — exported == published → nothing retired.
3. **Nothing exported** (or storage unconfigured → ``list_keys`` returns []) →
   ok, no-op.
4. **Missing ``_site_config``** → ok=False, no storage calls.
5. **max_per_run cap** — never retire more than the cap in one cycle; the
   remainder is reported, not silently dropped.
6. **DB query failure** → ok=False.

The DB pool and the two ``static_export_service`` helpers are mocked — no
real network, storage, or DB calls.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.static_export_orphan_sweep import StaticExportOrphanSweepJob
from services.site_config import SiteConfig

_EXPORTED = "services.static_export_service._list_exported_post_slugs"
_RETIRE = "services.static_export_service._retire_slug"


def _make_pool(published_slugs, *, fetch_error: Exception | None = None) -> Any:
    """asyncpg pool whose ``conn.fetch`` returns the published slug rows."""
    conn = AsyncMock()
    if fetch_error is not None:
        conn.fetch = AsyncMock(side_effect=fetch_error)
    else:
        conn.fetch = AsyncMock(
            return_value=[{"slug": s} for s in published_slugs]
        )
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def _retired_slugs(retire_mock: AsyncMock) -> list[str]:
    """Pull the positional slug arg out of each _retire_slug call."""
    return [call.args[0] for call in retire_mock.await_args_list]


@pytest.mark.unit
class TestStaticExportOrphanSweep:

    @pytest.mark.asyncio
    async def test_retires_only_unpublished_slugs(self):
        pool = _make_pool(["alive-1", "alive-2"])
        exported = ["alive-1", "ghost-a", "alive-2", "ghost-b"]

        with patch(_EXPORTED, AsyncMock(return_value=exported)), patch(
            _RETIRE, AsyncMock()
        ) as retire:
            result = await StaticExportOrphanSweepJob().run(
                pool, {"_site_config": SiteConfig()}
            )

        assert result.ok is True
        assert result.changes_made == 2
        assert sorted(_retired_slugs(retire)) == ["ghost-a", "ghost-b"]
        # Published slugs must never be retired.
        assert "alive-1" not in _retired_slugs(retire)
        assert result.metrics["orphans_retired"] == 2
        assert result.metrics["published"] == 2
        assert result.metrics["exported"] == 4

    @pytest.mark.asyncio
    async def test_no_orphans_is_noop(self):
        pool = _make_pool(["a", "b"])
        with patch(_EXPORTED, AsyncMock(return_value=["a", "b"])), patch(
            _RETIRE, AsyncMock()
        ) as retire:
            result = await StaticExportOrphanSweepJob().run(
                pool, {"_site_config": SiteConfig()}
            )

        assert result.ok is True
        assert result.changes_made == 0
        retire.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_nothing_exported_returns_ok_noop(self):
        """list_keys returns [] when storage is empty OR unconfigured."""
        pool = _make_pool(["a"])
        with patch(_EXPORTED, AsyncMock(return_value=[])), patch(
            _RETIRE, AsyncMock()
        ) as retire:
            result = await StaticExportOrphanSweepJob().run(
                pool, {"_site_config": SiteConfig()}
            )

        assert result.ok is True
        assert result.changes_made == 0
        retire.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_missing_site_config_fails_loud(self):
        pool = _make_pool(["a"])
        with patch(_EXPORTED, AsyncMock()) as exported, patch(
            _RETIRE, AsyncMock()
        ) as retire:
            result = await StaticExportOrphanSweepJob().run(pool, {})

        assert result.ok is False
        assert "_site_config" in result.detail
        exported.assert_not_awaited()
        retire.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_max_per_run_caps_and_reports_remainder(self):
        pool = _make_pool([])  # nothing published → everything is an orphan
        exported = [f"ghost-{i}" for i in range(5)]

        with patch(_EXPORTED, AsyncMock(return_value=exported)), patch(
            _RETIRE, AsyncMock()
        ) as retire:
            result = await StaticExportOrphanSweepJob().run(
                pool, {"_site_config": SiteConfig(), "max_per_run": 2}
            )

        assert result.ok is True
        assert result.changes_made == 2
        assert len(_retired_slugs(retire)) == 2
        # The remainder must be surfaced, not silently dropped.
        assert "more remain" in result.detail

    @pytest.mark.asyncio
    async def test_db_failure_fails_loud(self):
        pool = _make_pool([], fetch_error=RuntimeError("boom"))
        with patch(_EXPORTED, AsyncMock()) as exported, patch(_RETIRE, AsyncMock()):
            result = await StaticExportOrphanSweepJob().run(
                pool, {"_site_config": SiteConfig()}
            )

        assert result.ok is False
        assert "DB query failed" in result.detail
        # Never touch storage when we can't read the source of truth.
        exported.assert_not_awaited()
