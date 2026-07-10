"""FlagBotPageViewsJob honors the master switch without touching the DB."""
from __future__ import annotations

import pytest

from services.jobs.flag_bot_page_views import FlagBotPageViewsJob, _rowcount


def test_rowcount_parses_command_tags():
    # asyncpg returns command tags like "UPDATE 25" / "INSERT 0 3".
    assert _rowcount("UPDATE 25") == 25
    assert _rowcount("INSERT 0 3") == 3
    assert _rowcount("UPDATE 0") == 0
    assert _rowcount(None) == 0
    assert _rowcount("") == 0


class _FakeSiteConfig:
    def __init__(self, values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


class _ExplodingPool:
    def acquire(self):  # pragma: no cover - must never be called
        raise AssertionError("pool.acquire() called while disabled")


@pytest.mark.asyncio
async def test_disabled_switch_skips_without_db():
    job = FlagBotPageViewsJob()
    config = {"_site_config": _FakeSiteConfig({"beacon_bot_flag_enabled": "false"})}
    result = await job.run(_ExplodingPool(), config)
    assert result.ok is True
    assert result.changes_made == 0
    assert "disabled" in result.detail.lower() or "false" in result.detail.lower()


@pytest.mark.asyncio
async def test_missing_site_config_skips():
    job = FlagBotPageViewsJob()
    result = await job.run(_ExplodingPool(), {})
    assert result.ok is True
    assert result.changes_made == 0
