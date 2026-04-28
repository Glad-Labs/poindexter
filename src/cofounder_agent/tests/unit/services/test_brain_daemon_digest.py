"""Unit tests for brain/brain_daemon.py daily-digest queries — GH-158.

GH-158 (same bug class as GH-150) requires the digest's ``published_24h``
count to filter on ``status = 'published'`` rather than relying on
``published_at IS NOT NULL`` / a bare ``published_at > NOW() - INTERVAL``
predicate. ``published_at`` gets stamped during pipeline work and is
*never cleared* when a post is later moved to draft / archived, so the
unfiltered query overcounts (drafts + archived bleed in).

The digest message is the operator's daily Telegram + Discord summary —
the bug surfaced as inflated "X new today" numbers.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# brain/ is a standalone package outside the poindexter distro.
# brain_daemon.py uses bare ``from health_probes import ...`` for
# runtime-container compatibility, so we add brain/ directly to sys.path
# BEFORE importing. Tests without this prelude fail to collect with
# ``ModuleNotFoundError: No module named 'health_probes'``.
#
# Walk up to find the repo root rather than hardcoding a parents[N]
# index — the docker worker mounts `src/cofounder_agent` as `/app` so
# only 5 parent levels exist and `brain/` is not present at all in
# that tree. The test skips cleanly there; runs on the host.
_BRAIN_DIR = None
for _parent in Path(__file__).resolve().parents:
    _candidate = _parent / "brain"
    if (_candidate / "brain_daemon.py").is_file():
        _BRAIN_DIR = _candidate
        if str(_parent) not in sys.path:
            sys.path.insert(0, str(_parent))
        if str(_BRAIN_DIR) not in sys.path:
            sys.path.insert(0, str(_BRAIN_DIR))
        break

pytestmark = pytest.mark.skipif(
    _BRAIN_DIR is None,
    reason="brain/ directory not present — docker worker only mounts "
    "src/cofounder_agent as /app; this test runs on the host.",
)

if _BRAIN_DIR is not None:
    from brain import brain_daemon as bd  # noqa: E402
else:
    bd = None  # type: ignore[assignment]


def _digest_pool(stats_row: dict, *, last_sent: str | None = None):
    """Build a mock pool that walks generate_daily_digest's queries.

    Query order in generate_daily_digest:

    1. SELECT value FROM brain_knowledge WHERE entity='digest' ...
       (last-sent guard, fetchrow → returns ``None`` so we always proceed)
    2. SELECT value FROM app_settings WHERE key='brain_digest_window_hours'
       (via _setting_int → fetchval, default 24)
    3. SELECT ...big stats CTE... (fetchrow, returns ``stats_row``)
    4. INSERT INTO brain_knowledge ... last_sent
       (execute, swallowed)
    """
    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=[
        {"value": last_sent} if last_sent else None,
        stats_row,
    ])
    pool.fetchval = AsyncMock(return_value="24")  # window hours
    pool.execute = AsyncMock()
    return pool


@pytest.mark.unit
@pytest.mark.asyncio
class TestDailyDigestPostCounts:
    """GH-158: published_24h must filter by status='published', not
    rely on published_at alone (which lingers on drafts + archived)."""

    async def test_published_24h_query_filters_by_status(self, monkeypatch):
        """The fetchrow SQL for the stats block must include
        ``status = 'published'`` inside the published_24h sub-select.

        Without this guard the operator's daily digest overcounts by
        every draft / archived post that ever had ``published_at`` set
        during pipeline work. Same bug class as GH-150 (the doc-sync
        agent counting ``published_at IS NOT NULL``)."""
        # Force the 13:00-14:00 UTC window check to pass deterministically.
        # generate_daily_digest only sends inside that hour; we monkeypatch
        # ``datetime.now`` indirectly by faking the hour via a stub that
        # returns 13. The cleanest way is to patch the module-level
        # ``datetime`` import path used inside the function.
        from datetime import datetime, timezone

        class _FrozenDatetime:
            @staticmethod
            def now(tz=None):
                return datetime(2026, 4, 27, 13, 30, tzinfo=timezone.utc)

        # The function does ``from datetime import datetime, timezone``
        # at runtime — patch the bd module's globals after import.
        monkeypatch.setattr(bd, "send_telegram", lambda *_a, **_k: None)
        monkeypatch.setattr(bd, "send_discord", lambda *_a, **_k: None)

        # Patch datetime within the function via injecting into builtins
        # is brittle; instead patch ``datetime`` on the brain_daemon
        # module if present, otherwise rely on the runtime hour.
        import datetime as _dt_mod
        real_datetime = _dt_mod.datetime

        class _PatchDT(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime(2026, 4, 27, 13, 30, tzinfo=timezone.utc)

        monkeypatch.setattr(_dt_mod, "datetime", _PatchDT)

        stats_row = {
            "total_posts": 46,
            "approval_queue": 5,
            "pending": 2,
            "failed_24h": 0,
            "published_24h": 1,
            "views_today": 100,
            "month_spend": 0.0,
        }
        pool = _digest_pool(stats_row)

        await bd.generate_daily_digest(pool)

        # Find the stats fetchrow call — it's the second fetchrow
        # (first is the last-sent guard). Its SQL must contain the
        # status filter inside the published_24h sub-select.
        assert pool.fetchrow.await_count >= 2, (
            "expected the stats fetchrow to fire (digest reached the "
            "main query)"
        )
        stats_sql = pool.fetchrow.call_args_list[1].args[0]

        # GH-158 core assertion: the published_24h sub-select must
        # gate on status, not only on published_at.
        assert "published_24h" in stats_sql, (
            "sanity check: this is the stats query"
        )
        # The bug pattern was a sub-select like:
        #   FROM posts WHERE published_at > NOW() - INTERVAL '...'
        # The fix adds ``status = 'published'`` to that same sub-select.
        # Locate the published_24h block and verify status filtering.
        # Splitting on the alias gives us the predicate region.
        before_alias = stats_sql.split("as published_24h")[0]
        published_24h_block = before_alias.rsplit("(SELECT", 1)[-1]

        assert "status = 'published'" in published_24h_block, (
            "published_24h sub-select must filter by status='published'. "
            "Without it, drafts + archived posts (which still carry a "
            "stale published_at) inflate the digest's 'new today' count. "
            f"Got block: {published_24h_block!r}"
        )
        # Belt-and-suspenders: total_posts already filters status —
        # confirm we didn't accidentally regress it.
        before_total = stats_sql.split("as total_posts")[0]
        total_posts_block = before_total.rsplit("(SELECT", 1)[-1]
        assert "status = 'published'" in total_posts_block

    async def test_digest_not_sent_outside_window(self, monkeypatch):
        """Cleanroom regression check: outside 13:00-14:00 UTC the
        digest exits before running the stats query, so the count
        bug can't fire either. We verify the stats fetchrow is NOT
        called when the hour is wrong."""
        import datetime as _dt_mod
        from datetime import timezone

        real_datetime = _dt_mod.datetime

        class _PatchDT(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime(2026, 4, 27, 7, 0, tzinfo=timezone.utc)

        monkeypatch.setattr(_dt_mod, "datetime", _PatchDT)

        pool = _digest_pool({"total_posts": 0})
        # Override fetchrow side_effect — only the last-sent guard
        # should fire; the stats query must NOT be reached.
        pool.fetchrow = AsyncMock(return_value=None)

        await bd.generate_daily_digest(pool)

        # Only the last_sent guard fetchrow ran (1 call), not the stats.
        assert pool.fetchrow.await_count == 1


@pytest.mark.unit
class TestDigestSqlSnapshotsMixedStatuses:
    """Documents the expected behaviour given a fixture with mixed
    statuses. This is a SQL-shape test — we assert the literal SQL
    string read by ``generate_daily_digest`` would correctly count
    only ``status='published'`` rows.

    Fixture (illustrative, mirrors the issue's acceptance criteria):
      - 3 posts with status='published' (all with published_at NOW)
      - 2 posts with status='draft'    (published_at set during pipeline)
      - 1 post  with status='archived' (published_at lingering)

    Expected digest counts:
      total_posts   = 3
      published_24h = 3   (NOT 6 — drafts + archived must be excluded)
    """

    def test_digest_sql_filters_each_post_subquery_by_status(self):
        """Read the SQL the digest would issue and verify both the
        ``total_posts`` and ``published_24h`` sub-selects filter on
        ``status = 'published'``. A snapshot-style test — the SQL is
        a string literal in brain_daemon.py so we can inspect it
        without needing a live pool."""
        # Locate brain_daemon.py and read its source.
        if _BRAIN_DIR is None:
            pytest.skip("brain/ not present in this checkout")
        source = (_BRAIN_DIR / "brain_daemon.py").read_text(encoding="utf-8")

        # Find the digest stats block. We look for the literal
        # ``as total_posts`` and ``as published_24h`` markers and
        # require ``status = 'published'`` to appear in the SQL
        # region between them and their preceding ``(SELECT``.
        for alias in ("total_posts", "published_24h"):
            marker = f"as {alias}"
            assert marker in source, f"expected `{marker}` in brain_daemon.py"
            before = source.split(marker, 1)[0]
            sub_select = before.rsplit("(SELECT", 1)[-1]
            assert "FROM posts" in sub_select, (
                f"sanity check: {alias} block reads from posts"
            )
            assert "status = 'published'" in sub_select, (
                f"GH-158: {alias} sub-select must filter by "
                f"status='published'. Drafts and archived posts carry "
                f"lingering published_at timestamps and would inflate "
                f"this count otherwise. Block was: {sub_select!r}"
            )
