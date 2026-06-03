"""Unit tests for ``services/jobs/sync_cloudflare_analytics.py``.

Covers the CF AE SQL HTTP API → page_views pull that replaced the
silent-since-2026-04-09 ``/api/track/view`` beacon path.

Mirrors the pattern in test_sync_page_views_job.py — SiteConfig DI seam,
fake ``httpx`` module, fake asyncpg pool.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.sync_cloudflare_analytics import SyncCloudflareAnalyticsJob


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

    async def test_inserts_rows_and_updates_view_count(self):
        """Each fetched row → one INSERT into page_views; each distinct
        slug → one UPDATE posts SET view_count += delta."""
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
        # Inspect execute calls — should include 3 INSERTs + 2 UPDATEs
        # + watermark UPSERT + CREATE TABLE precheck.
        sql_calls = [c.args[0] for c in conn.execute.await_args_list]
        inserts = [s for s in sql_calls if "INSERT INTO page_views" in s]
        updates = [s for s in sql_calls if "UPDATE posts" in s]
        assert len(inserts) == 3
        assert len(updates) == 2

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
        timestamp from the batch."""
        pool, conn = _make_pool()
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
