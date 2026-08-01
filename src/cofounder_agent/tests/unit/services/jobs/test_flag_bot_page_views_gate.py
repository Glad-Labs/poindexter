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


class _NullTxn:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _RecordingConn:
    """Capture every execute() so the pass wiring is pinned without a DB."""

    def __init__(self):
        self.calls = []

    async def execute(self, sql, *args):
        self.calls.append((sql, args))
        return "UPDATE 0"

    async def fetchval(self, _sql, *args):
        return "true"  # backfill sentinel already set → backfill pass skipped

    def transaction(self):
        return _NullTxn()


class _RecordingPool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool._conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


@pytest.mark.asyncio
async def test_sweep_pass_runs_with_configured_cap():
    """The path-sweep pass (poindexter#973) executes after the flood pass,
    window-scoped, with beacon_sweep_max_distinct_paths as its cap."""
    conn = _RecordingConn()
    config = {
        "_site_config": _FakeSiteConfig(
            {
                "beacon_flood_window_hours": "24",
                "beacon_flood_cap_per_window": "20",
                "beacon_sweep_max_distinct_paths": "25",
            }
        )
    }
    result = await FlagBotPageViewsJob().run(_RecordingPool(conn), config)
    assert result.ok is True

    sweep_calls = [c for c in conn.calls if "sweep:ua_distinct_paths" in c[0]]
    assert len(sweep_calls) == 1
    sql, args = sweep_calls[0]
    assert args == ("24", 25)
    # Window-scoped on BOTH sides: the CTE and the UPDATE filter by created_at.
    assert sql.count("created_at >= now()") == 2
    # Ordering: flood pass first, then sweep, then view-count recompute.
    order = [
        next(
            k
            for k in ("flood:ua_path", "sweep:ua_distinct_paths", "view_count")
            if k in sql_text
        )
        for sql_text, _ in conn.calls
        if any(
            k in sql_text
            for k in ("flood:ua_path", "sweep:ua_distinct_paths", "view_count")
        )
    ]
    assert order == ["flood:ua_path", "sweep:ua_distinct_paths", "view_count"]
    assert result.metrics["sweep_flagged"] == 0
