"""Unit tests for ``services/jobs/sync_cloudflare_analytics.py``.

Covers the CF AE SQL HTTP API → page_views pull that replaced the
silent-since-2026-04-09 ``/api/track/view`` beacon path.

Mirrors the pattern in test_sync_page_views_job.py — SiteConfig DI seam,
fake ``httpx`` module, fake asyncpg pool.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.sync_cloudflare_analytics import (
    SyncCloudflareAnalyticsJob,
    _parse_iso,
)


def _sc(
    account_id: str = "test-account",
    api_token: str = "test-token",
    last_sync: str = "",
) -> MagicMock:
    """Mock SiteConfig — wired through the job's `_site_config` config kwarg."""
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "cloudflare_account_id": account_id,
    }.get(key, default)
    sc.get_secret = AsyncMock(return_value=api_token)
    return sc


def _make_pool(last_sync_row: dict | None = None, exists_results: list | None = None):
    """Build an asyncpg-shaped pool whose ``acquire()`` yields a connection
    with execute / fetchrow / fetchval / transaction stubs.
    """
    conn = AsyncMock()
    conn.execute = AsyncMock(return_value="OK")
    conn.fetchrow = AsyncMock(return_value=last_sync_row)
    # fetchval returns None by default (no dedup hit); each row in
    # exists_results pre-seeds the queue if the caller wants dedup behaviour.
    if exists_results is not None:
        conn.fetchval = AsyncMock(side_effect=exists_results)
    else:
        conn.fetchval = AsyncMock(return_value=None)

    tx_ctx = AsyncMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    acquire_ctx = AsyncMock()
    acquire_ctx.__aenter__ = AsyncMock(return_value=conn)
    acquire_ctx.__aexit__ = AsyncMock(return_value=False)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_ctx)
    return pool, conn


def _fake_httpx(rows: list[dict] | None = None, status: int = 200, raises=None):
    """Build a fake ``httpx`` module that returns the given rows from POST."""
    resp = MagicMock()
    resp.status_code = status
    resp.text = "fake-response"
    resp.json = MagicMock(return_value={"data": rows or []})

    client = AsyncMock()
    if raises is not None:
        client.post = AsyncMock(side_effect=raises)
    else:
        client.post = AsyncMock(return_value=resp)

    class _AsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return client

        async def __aexit__(self, *args: Any) -> None:
            return None

    fake = MagicMock()
    fake.AsyncClient = _AsyncClient
    return fake, client


def _row(
    slug: str = "test-slug",
    path: str = "/posts/test-slug",
    referrer: str = "https://google.com",
    user_agent: str = "Mozilla/5.0",
    ts: str = "2026-05-28 22:00:00",
) -> dict:
    return {
        "slug": slug,
        "path": path,
        "referrer": referrer,
        "user_agent": user_agent,
        "created_at": ts,
    }


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSyncCloudflareAnalyticsJobMetadata:
    def test_name(self):
        assert SyncCloudflareAnalyticsJob.name == "sync_cloudflare_analytics"

    def test_idempotent(self):
        assert SyncCloudflareAnalyticsJob.idempotent is True

    def test_schedule(self):
        assert "every" in SyncCloudflareAnalyticsJob.schedule.lower()
        assert "5" in SyncCloudflareAnalyticsJob.schedule


# ---------------------------------------------------------------------------
# Skip conditions — missing config / secrets
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestSyncCloudflareAnalyticsSkips:
    async def test_skips_when_site_config_missing(self):
        pool, _ = _make_pool()
        result = await SyncCloudflareAnalyticsJob().run(pool, {})
        assert result.ok is True
        assert result.changes_made == 0
        assert "_site_config" in result.detail

    async def test_skips_when_account_id_missing(self):
        pool, _ = _make_pool()
        sc = _sc(account_id="")
        result = await SyncCloudflareAnalyticsJob().run(
            pool, {"_site_config": sc}
        )
        assert result.ok is True
        assert result.changes_made == 0
        assert "cloudflare_account_id" in result.detail

    async def test_token_missing_while_account_set_is_degraded(self):
        # poindexter#555: account_id is configured but the token is empty
        # = a half-configured ingest. It must surface as DEGRADED (ok=False)
        # + emit a finding, NOT mask the dead ingest green (the bug that
        # hid a ~54-day page_views outage).
        pool, _ = _make_pool()
        sc = _sc(api_token="")
        with patch(
            "services.jobs.sync_cloudflare_analytics.emit_finding"
        ) as mock_finding:
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is False
        assert result.changes_made == 0
        assert "cloudflare_analytics_api_token" in result.detail
        assert "DEGRADED" in result.detail
        mock_finding.assert_called_once()
        assert mock_finding.call_args.kwargs["severity"] == "warn"

    async def test_skips_when_httpx_unavailable(self):
        pool, _ = _make_pool()
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": None}):
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is False
        assert "httpx" in result.detail


# ---------------------------------------------------------------------------
# CF SQL API success path
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestSyncCloudflareAnalyticsHappyPath:
    async def test_empty_response_advances_watermark_no_inserts(self):
        """Empty CF response is a no-op success — watermark still advances
        so the next pull doesn't re-scan an empty window forever."""
        pool, conn = _make_pool()
        fake_httpx, _client = _fake_httpx(rows=[])
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is True
        assert result.changes_made == 0
        assert "no new rows" in result.detail
        # The high-water-mark UPSERT should have fired.
        sql_calls = [
            c.args[0] for c in conn.execute.await_args_list if c.args
        ]
        assert any(
            "cloudflare_analytics_last_sync" in sql for sql in sql_calls
        )

    async def test_inserts_rows_without_view_count_update(self):
        """Each fetched row → one INSERT into page_views. posts.view_count is
        owned by FlagBotPageViewsJob (recompute from page_views_human), so the
        sync ingest no longer issues any ``UPDATE posts SET view_count`` bump —
        which used to count bots before they could be flagged."""
        pool, conn = _make_pool()
        rows = [
            _row(slug="post-a", ts="2026-05-28 21:00:00"),
            _row(slug="post-a", ts="2026-05-28 21:05:00"),
            _row(slug="post-b", ts="2026-05-28 21:10:00"),
        ]
        fake_httpx, _ = _fake_httpx(rows=rows)
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is True
        assert result.changes_made == 3
        # 3 INSERTs into page_views, but NO UPDATE posts (view_count bump removed).
        sql_calls = [c.args[0] for c in conn.execute.await_args_list]
        inserts = [s for s in sql_calls if "INSERT INTO page_views" in s]
        updates = [s for s in sql_calls if "UPDATE posts" in s]
        assert len(inserts) == 3
        assert len(updates) == 0

    async def test_dedup_skips_already_present_rows(self):
        """Row whose (slug, path, ts, ua) already exists is skipped."""
        pool, conn = _make_pool(exists_results=[1, None])
        rows = [
            _row(slug="post-a", ts="2026-05-28 21:00:00"),
            _row(slug="post-b", ts="2026-05-28 21:05:00"),
        ]
        fake_httpx, _ = _fake_httpx(rows=rows)
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is True
        # Only the second row was new
        assert result.changes_made == 1

    async def test_watermark_advances_on_success(self):
        """The cloudflare_analytics_last_sync row is upserted with the max
        timestamp from the batch.

        The stored watermark has to sit BELOW the batch — since stack#3523 the
        cursor is monotonic, so a batch whose newest row predates the current
        watermark leaves it alone rather than rewinding it into an
        ever-widening re-scan (see ``TestNextHighWater``).
        """
        pool, conn = _make_pool(
            last_sync_row={"value": "2026-05-28T20:00:00+00:00"}
        )
        rows = [
            _row(ts="2026-05-28 21:00:00"),
            _row(ts="2026-05-28 22:30:00"),  # max
        ]
        fake_httpx, _ = _fake_httpx(rows=rows)
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            await SyncCloudflareAnalyticsJob().run(pool, {"_site_config": sc})

        # Find the UPSERT call to cloudflare_analytics_last_sync — the new
        # value should reflect 22:30 UTC (the max timestamp from the batch).
        upsert_call = next(
            c for c in conn.execute.await_args_list
            if c.args and "cloudflare_analytics_last_sync" in c.args[0]
        )
        # The value is passed as $1 → call.args[1]
        new_value = upsert_call.args[1]
        parsed = datetime.fromisoformat(new_value)
        assert parsed.replace(tzinfo=None) == datetime(2026, 5, 28, 22, 30, 0)


# ---------------------------------------------------------------------------
# Failure modes — all soft (job never raises)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestSyncCloudflareAnalyticsFailures:
    async def test_cf_api_error_returns_soft_failure(self):
        pool, _ = _make_pool()
        fake_httpx, _ = _fake_httpx(status=500)
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is False
        assert "500" in result.detail
        assert result.changes_made == 0

    async def test_cf_api_request_exception_returns_soft_failure(self):
        pool, _ = _make_pool()
        fake_httpx, _ = _fake_httpx(raises=ConnectionError("DNS fail"))
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is False
        assert "DNS fail" in result.detail
        assert result.changes_made == 0

    async def test_db_precheck_failure_returns_soft_failure(self):
        """If the CREATE TABLE / fetchrow precheck fails, the job returns
        ok=False and doesn't proceed to the API call."""
        pool, conn = _make_pool()
        conn.execute = AsyncMock(side_effect=RuntimeError("DB down"))
        sc = _sc()
        result = await SyncCloudflareAnalyticsJob().run(
            pool, {"_site_config": sc}
        )
        assert result.ok is False
        assert "db precheck" in result.detail.lower()

    async def test_secret_lookup_failure_is_degraded(self):
        """A failure reading the api token surfaces as DEGRADED (ok=False)
        + a finding. poindexter#555: it used to return ok=True and mask
        the dead ingest as green."""
        pool, _ = _make_pool()
        sc = _sc()
        sc.get_secret = AsyncMock(side_effect=RuntimeError("decrypt fail"))
        with patch(
            "services.jobs.sync_cloudflare_analytics.emit_finding"
        ) as mock_finding:
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is False
        assert "get_secret failed" in result.detail
        mock_finding.assert_called_once()
        assert mock_finding.call_args.kwargs["severity"] == "warn"


# ---------------------------------------------------------------------------
# Watermark behaviour
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestSyncCloudflareAnalyticsWatermark:
    async def test_first_run_uses_lookback_window(self):
        """No watermark row → falls back to 24h lookback by default."""
        pool, conn = _make_pool(last_sync_row=None)
        fake_httpx, client = _fake_httpx(rows=[])
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            await SyncCloudflareAnalyticsJob().run(pool, {"_site_config": sc})
        # The POST should have happened
        client.post.assert_awaited()
        sent_sql = client.post.await_args.kwargs.get("content") or ""
        assert "analytics_events" in sent_sql
        # poindexter#555 regression guard: ORDER BY must reference the SELECT
        # alias (created_at), never the raw `timestamp` column — CF Analytics
        # Engine rejects the aliased-column-by-original-name form with
        # "unable to find type of column: timestamp", which silently killed
        # the entire ingest.
        assert "ORDER BY created_at" in sent_sql
        assert "ORDER BY timestamp" not in sent_sql

    async def test_subsequent_run_uses_stored_watermark(self):
        """Watermark row present → query bounds by that timestamp."""
        pool, conn = _make_pool(
            last_sync_row={"value": "2026-05-28T20:00:00+00:00"}
        )
        fake_httpx, client = _fake_httpx(rows=[])
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            await SyncCloudflareAnalyticsJob().run(pool, {"_site_config": sc})
        sent_sql = client.post.await_args.kwargs.get("content") or ""
        # Watermark is formatted as 'YYYY-MM-DD HH:MM:SS' UTC in the query.
        assert "2026-05-28 20:00:00" in sent_sql

    async def test_malformed_watermark_falls_back_to_lookback(self):
        pool, conn = _make_pool(last_sync_row={"value": "not-a-date"})
        fake_httpx, client = _fake_httpx(rows=[])
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": sc}
            )
        # Should not crash — falls back, hits the API, returns ok
        assert result.ok is True
        client.post.assert_awaited()


# ---------------------------------------------------------------------------
# Bot filtering
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestBotFiltering:
    async def test_bingbot_rows_are_skipped(self):
        pool, conn = _make_pool()
        rows = [
            _row(
                slug="post-a",
                user_agent=(
                    "Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko; "
                    "compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm) "
                    "Chrome/136.0.0.0 Safari/537.36"
                ),
            ),
        ]
        fake_httpx, _ = _fake_httpx(rows=rows)
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is True
        assert result.changes_made == 0
        sql_calls = [c.args[0] for c in conn.execute.await_args_list]
        assert not any("INSERT INTO page_views" in s for s in sql_calls)

    async def test_known_bot_tokens_are_skipped(self):
        bot_uas = [
            "Googlebot/2.1 (+http://www.google.com/bot.html)",
            "Mozilla/5.0 (compatible; YandexBot/3.0)",
            "CCBot/2.0 (https://commoncrawl.org/faq/)",
            "LinkedInBot/1.0 (compatible; Mozilla/5.0)",
            "facebookexternalhit/1.1",
            "Sogou web spider/4.0",
        ]
        for ua in bot_uas:
            pool, conn = _make_pool()
            rows = [_row(slug="post-a", user_agent=ua)]
            fake_httpx, _ = _fake_httpx(rows=rows)
            sc = _sc()
            with patch.dict("sys.modules", {"httpx": fake_httpx}):
                result = await SyncCloudflareAnalyticsJob().run(
                    pool, {"_site_config": sc}
                )
            assert result.changes_made == 0, f"bot UA not filtered: {ua}"

    async def test_human_rows_are_still_inserted(self):
        pool, conn = _make_pool()
        rows = [
            _row(slug="post-a", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/136.0.0.0 Safari/537.36"),
            _row(slug="post-b", user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15"),
        ]
        fake_httpx, _ = _fake_httpx(rows=rows)
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.ok is True
        assert result.changes_made == 2

    async def test_mixed_human_and_bot_rows_only_inserts_human(self):
        pool, conn = _make_pool()
        rows = [
            _row(slug="post-a", user_agent="Mozilla/5.0 (Windows NT 10.0) Chrome/136.0.0.0"),
            _row(slug="post-b", user_agent="compatible; bingbot/2.0"),
            _row(slug="post-c", user_agent="Googlebot/2.1"),
        ]
        fake_httpx, _ = _fake_httpx(rows=rows)
        sc = _sc()
        with patch.dict("sys.modules", {"httpx": fake_httpx}):
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": sc}
            )
        assert result.changes_made == 1


class TestTransientNetworkDeferral:
    """stack#3161: a resolver blip that survives the connect-retry budget is a
    NETWORK fault, not a job fault — deferred (ok=True, tap-runner posture)
    with ONE shared network_unreachable finding, so the fault that used to
    page both CF jobs pages once."""

    async def test_resolver_failure_defers_with_shared_finding(self):
        pool, _ = _make_pool()
        fake_httpx, _ = _fake_httpx(
            raises=ConnectionError("[Errno -3] Temporary failure in name resolution"),
        )
        findings: list = []
        with patch.dict("sys.modules", {"httpx": fake_httpx}), patch(
            "services.jobs.sync_cloudflare_analytics.emit_finding",
            lambda **kw: findings.append(kw),
        ):
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": _sc()}
            )
        assert result.ok is True
        assert "deferred" in result.detail
        assert result.changes_made == 0
        (f,) = findings
        assert f["kind"] == "network_unreachable"
        assert f["severity"] == "warning"
        # The dedup key is SHARED across every CF consumer — the whole point.
        assert f["dedup_key"] == "network_unreachable:cloudflare"

    async def test_non_transient_failure_still_fails_the_job(self):
        """A failure with no couldn't-connect shape keeps the honest ok=False
        (and its job_failure page) — deferral must not swallow real faults."""
        pool, _ = _make_pool()
        fake_httpx, _ = _fake_httpx(raises=ValueError("CF rejected the query"))
        findings: list = []
        with patch.dict("sys.modules", {"httpx": fake_httpx}), patch(
            "services.jobs.sync_cloudflare_analytics.emit_finding",
            lambda **kw: findings.append(kw),
        ):
            result = await SyncCloudflareAnalyticsJob().run(
                pool, {"_site_config": _sc()}
            )
        assert result.ok is False
        assert findings == []

    def test_affiliate_job_shares_the_posture(self):
        """Both CF jobs must route through the shared classifier and the SAME
        dedup key — a divergent copy re-splits the page."""
        from pathlib import Path

        import services.jobs.sync_affiliate_clicks as aff

        src = Path(aff.__file__).read_text(encoding="utf-8")
        assert "is_transient_network_error" in src
        assert "transient_retry_transport" in src
        assert '"network_unreachable:cloudflare"' in src


# ---------------------------------------------------------------------------
# Ingestion-lag race (stack#3523)
# ---------------------------------------------------------------------------


def _at(hhmmss: str) -> datetime:
    """2026-08-31 <hh:mm:ss> UTC — the day the loss was caught in the wild."""
    return datetime.strptime(
        f"2026-08-31 {hhmmss}", "%Y-%m-%d %H:%M:%S"
    ).replace(tzinfo=timezone.utc)


def _frozen_datetime(now_value: datetime):
    """A ``datetime`` subclass whose ``now()`` is pinned.

    Subclass rather than MagicMock because the job also calls
    ``datetime.strptime`` / ``fromisoformat`` on the same module-level name.
    """

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return now_value if tz is not None else now_value.replace(tzinfo=None)

    return _Frozen


class _FakeAnalyticsEngine:
    """Cloudflare AE with its ingestion delay modelled explicitly.

    A data point is *written* at ``ts`` but is only *queryable* from
    ``visible_at``. That gap — not any error — is what silently drops rows:
    a poll in the gap sees nothing, and if it then advances the high-water
    mark past ``ts`` the row is below the cursor forever once it appears.
    """

    def __init__(self) -> None:
        self._points: list[tuple[datetime, datetime, dict]] = []
        self.queries: list[datetime] = []

    def write(self, row: dict, *, visible_at: datetime) -> None:
        ts = datetime.strptime(row["created_at"], "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        self._points.append((ts, visible_at, row))

    def query(self, sql: str, now: datetime) -> list[dict]:
        m = re.search(r"toDateTime\('([^']+)'", sql)
        assert m, f"could not find the since-clause in {sql!r}"
        since = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc
        )
        self.queries.append(since)
        return [
            row
            for ts, visible_at, row in sorted(self._points, key=lambda p: p[0])
            if ts > since and now >= visible_at
        ]


class _StatefulPool:
    """asyncpg-shaped pool that PERSISTS the high-water mark across runs, so a
    test can drive several 5-minute cycles the way the scheduler does."""

    def __init__(self, watermark: str | None = None) -> None:
        self.watermark = watermark
        self.inserted: list[tuple] = []
        conn = AsyncMock()

        async def _execute(sql, *args):
            if "cloudflare_analytics_last_sync" in sql:
                self.watermark = args[0]
            elif "INSERT INTO page_views" in sql:
                self.inserted.append(args)
            return "OK"

        async def _fetchrow(sql, *args):
            if "cloudflare_analytics_last_sync" in sql:
                return {"value": self.watermark} if self.watermark else None
            return None

        async def _fetchval(sql, *args):
            # Dedup probe: a row already ingested must not be inserted twice.
            if "SELECT 1 FROM page_views" in sql:
                slug, path, ts, ua = args
                return 1 if any(
                    row[0] == path and row[1] == slug and row[4] == ts
                    for row in self.inserted
                ) else None
            return None

        conn.execute = AsyncMock(side_effect=_execute)
        conn.fetchrow = AsyncMock(side_effect=_fetchrow)
        conn.fetchval = AsyncMock(side_effect=_fetchval)

        tx = AsyncMock()
        tx.__aenter__ = AsyncMock(return_value=None)
        tx.__aexit__ = AsyncMock(return_value=False)
        conn.transaction = MagicMock(return_value=tx)

        acquire = AsyncMock()
        acquire.__aenter__ = AsyncMock(return_value=conn)
        acquire.__aexit__ = AsyncMock(return_value=False)
        self.acquire = MagicMock(return_value=acquire)
        self.conn = conn

    @property
    def paths(self) -> list:
        return [row[0] for row in self.inserted]


async def _run_cycle(pool, ae: _FakeAnalyticsEngine, now: datetime, **cfg):
    """One scheduler fire at wall-clock ``now``, against the fake AE."""
    resp = MagicMock()
    resp.status_code = 200
    resp.text = ""

    client = AsyncMock()

    async def _post(url, headers=None, content=None):
        resp.json = MagicMock(return_value={"data": ae.query(content, now)})
        return resp

    client.post = AsyncMock(side_effect=_post)

    class _AsyncClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> Any:
            return client

        async def __aexit__(self, *a: Any) -> None:
            return None

    fake_httpx = MagicMock()
    fake_httpx.AsyncClient = _AsyncClient

    with patch.dict("sys.modules", {"httpx": fake_httpx}), patch(
        "services.jobs.sync_cloudflare_analytics.datetime", _frozen_datetime(now)
    ):
        return await SyncCloudflareAnalyticsJob().run(
            pool, {"_site_config": _sc(), **cfg}
        )


@pytest.mark.unit
@pytest.mark.asyncio
class TestIngestionLagRace:
    """stack#3523 — the permanent data-loss window.

    Reproduces the 2026-08-31 incident exactly. Two identical beacons to
    ``/__origin-enforcement-e2e``: the 19:32:46 write landed 23 s before an
    empty poll and was lost forever; the 19:45:04 write landed after its
    preceding poll and survived. 40 genuine reader page views were missing
    from August for the same reason.
    """

    @staticmethod
    def _lost_row() -> dict:
        return _row(
            slug="",
            path="/__origin-enforcement-e2e",
            referrer="",
            user_agent="Mozilla/5.0 (X11; Linux x86_64) origin-enforcement-e2e-check",
            ts="2026-08-31 19:32:46",
        )

    async def test_row_written_before_an_empty_poll_is_still_ingested(self):
        """THE regression test. The row is written at 19:32:46 but is not
        queryable until 19:34:30. The 19:33:09 poll therefore sees nothing;
        the row must still be ingested on a later cycle."""
        ae = _FakeAnalyticsEngine()
        ae.write(self._lost_row(), visible_at=_at("19:34:30"))
        pool = _StatefulPool(watermark="2026-08-31T19:28:00+00:00")

        # Cycle 1 — inside the ingestion delay: AE returns nothing.
        r1 = await _run_cycle(pool, ae, _at("19:33:09"))
        assert r1.ok is True
        assert r1.changes_made == 0
        assert pool.paths == []

        # Cycle 2 — the row is visible now. The watermark must NOT have been
        # advanced past 19:32:46, or this poll can never select it.
        r2 = await _run_cycle(pool, ae, _at("19:38:09"))
        assert r2.changes_made == 1
        assert pool.paths == ["/__origin-enforcement-e2e"]

        # Cycle 3 — re-pull overlap is expected; the insert-time dedup is what
        # makes it free. The row must not be counted twice.
        r3 = await _run_cycle(pool, ae, _at("19:43:09"))
        assert r3.changes_made == 0
        assert pool.paths == ["/__origin-enforcement-e2e"]

    async def test_margin_zero_reproduces_the_loss(self):
        """Pins the MECHANISM, not just the fix: with the margin disabled the
        same row is lost forever — the empty poll advances past its timestamp
        and no later cycle can ever select it again."""
        ae = _FakeAnalyticsEngine()
        ae.write(self._lost_row(), visible_at=_at("19:34:30"))
        pool = _StatefulPool(watermark="2026-08-31T19:28:00+00:00")

        await _run_cycle(pool, ae, _at("19:33:09"), ingestion_lag_seconds=0)
        for minute in ("19:38:09", "19:43:09", "19:48:09"):
            await _run_cycle(pool, ae, _at(minute), ingestion_lag_seconds=0)

        assert pool.paths == []
        # The cursor is the reason: it stepped past the row's own timestamp.
        assert _parse_iso(pool.watermark) > _at("19:32:46")

    async def test_empty_poll_advances_but_stops_short_of_now(self):
        ae = _FakeAnalyticsEngine()
        pool = _StatefulPool(watermark="2026-08-31T19:20:00+00:00")

        result = await _run_cycle(pool, ae, _at("19:33:09"))

        assert result.detail == "no new rows"
        assert _parse_iso(pool.watermark) == _at("19:28:09")

    async def test_all_rows_filtered_still_advances_the_cursor(self):
        """A batch of pure bot traffic inserts nothing, but the cursor must
        still move past what it READ — otherwise the window grows every cycle
        and the same bots are re-scanned forever."""
        ae = _FakeAnalyticsEngine()
        ae.write(
            _row(
                slug="post-a",
                ts="2026-08-31 19:25:00",
                user_agent="Mozilla/5.0 (compatible; bingbot/2.0)",
            ),
            visible_at=_at("19:26:00"),
        )
        pool = _StatefulPool(watermark="2026-08-31T19:20:00+00:00")

        result = await _run_cycle(pool, ae, _at("19:33:09"))

        assert result.changes_made == 0
        assert pool.paths == []
        # Past the bot row it read, and no further — nothing between 19:25:00
        # and the horizon has been examined yet.
        assert _parse_iso(pool.watermark) == _at("19:25:00")

    async def test_fully_deduped_batch_does_not_skip_the_unread_remainder(self):
        """The backfill shape, and the sharpest edge on the horizon cap.

        Rewind the cursor and re-pull a mostly-ingested window: the batch is
        entirely dedup-skips, so nothing is inserted. If the cursor took its
        position from INSERTS it would read ``None`` here, the horizon would
        apply, and every row the batch had not yet reached would be jumped —
        turning a recovery into a fresh loss. It must advance only as far as
        it actually read.
        """
        ae = _FakeAnalyticsEngine()
        old_row = _row(slug="post-a", path="/posts/post-a", ts="2026-08-31 18:00:00")
        later_row = _row(slug="post-b", path="/posts/post-b", ts="2026-08-31 19:20:00")
        ae.write(old_row, visible_at=_at("18:01:00"))
        pool = _StatefulPool(watermark="2026-08-31T17:00:00+00:00")

        # Cycle 1 ingests the old row and leaves the cursor on it.
        assert (await _run_cycle(pool, ae, _at("18:30:00"))).changes_made == 1

        # Now rewind (the documented recovery) and add a row the first pass
        # never saw. The re-pull is all dedup for the row it already has.
        pool.watermark = "2026-08-31T17:00:00+00:00"
        ae.write(later_row, visible_at=_at("19:21:00"))

        r = await _run_cycle(pool, ae, _at("19:25:00"))

        # The previously-unseen row is recovered, not skipped, and the already
        # ingested one is not duplicated.
        assert pool.paths == ["/posts/post-a", "/posts/post-b"]
        assert r.changes_made == 1
        assert _parse_iso(pool.watermark) == _at("19:20:00")

    async def test_future_watermark_does_not_wedge_the_ingest(self):
        """A cursor ahead of wall-clock — a typo on the documented recovery
        command — must not wedge the ingest.

        The rule is monotonic, so the cursor can never walk back on its own;
        the pre-fix code repaired such a value by accident, overwriting it with
        `now()` on the next empty poll. It is now rejected at read time
        instead, and the job falls back to its lookback window.
        """
        ae = _FakeAnalyticsEngine()
        ae.write(
            _row(slug="post-a", path="/posts/post-a", ts="2026-08-31 19:20:00"),
            visible_at=_at("19:21:00"),
        )
        # Fat-fingered a year on `poindexter settings set`.
        pool = _StatefulPool(watermark="2027-08-31T19:00:00+00:00")

        result = await _run_cycle(pool, ae, _at("19:33:09"))

        assert result.changes_made == 1
        assert pool.paths == ["/posts/post-a"]
        assert _parse_iso(pool.watermark) == _at("19:20:00")

    async def test_second_beacon_after_its_poll_was_never_at_risk(self):
        """The incident's control: the 19:45:04 write landed AFTER the
        19:43:09 poll, so no empty poll ever stepped over it. Same job, same
        config — only the timing differs."""
        ae = _FakeAnalyticsEngine()
        ae.write(
            _row(
                slug="",
                path="/__origin-enforcement-e2e",
                referrer="",
                user_agent="Mozilla/5.0 (X11; Linux x86_64) origin-enforcement-e2e-check",
                ts="2026-08-31 19:45:04",
            ),
            visible_at=_at("19:47:00"),
        )
        pool = _StatefulPool(watermark="2026-08-31T19:38:00+00:00")

        await _run_cycle(pool, ae, _at("19:43:09"))
        result = await _run_cycle(pool, ae, _at("19:48:16"))

        assert result.changes_made == 1
        assert pool.paths == ["/__origin-enforcement-e2e"]
