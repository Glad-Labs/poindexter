"""Integration test for ``SyncCloudflareAnalyticsJob`` against a real test DB.

Mocks only the CF AE SQL HTTP API surface; everything else (page_views
INSERTs, posts.view_count UPDATEs, app_settings high-water-mark UPSERTs)
runs against the actual ``poindexter_test`` Postgres. Mirrors the harness
pattern from ``test_jobs_sync_page_views.py``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from plugins import Job
from services.jobs.sync_cloudflare_analytics import SyncCloudflareAnalyticsJob
from tests.integration.conftest import requires_real_services

# asyncio mark dropped — ``asyncio_mode = "auto"`` (pyproject.toml) auto-marks
# coroutine tests, and the explicit mark wrongly tagged sync tests in this
# module (Glad-Labs/glad-labs-stack#997). The integration marks stay.
pytestmark = [pytest.mark.integration, requires_real_services]


def _sc(
    account_id: str = "test-account",
    api_token: str = "test-token",
) -> MagicMock:
    sc = MagicMock()
    sc.get.side_effect = lambda key, default="": {
        "cloudflare_account_id": account_id,
    }.get(key, default)
    sc.get_secret = AsyncMock(return_value=api_token)
    return sc


def _fake_httpx_with_rows(rows: list[dict], status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.text = "fake"
    resp.json = MagicMock(return_value={"data": rows})

    client = AsyncMock()
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
    return fake


class TestSyncCloudflareAnalyticsJob:
    """Real-DB tests for the sync_cloudflare_analytics job."""

    def test_conforms_to_job_protocol(self):
        assert isinstance(SyncCloudflareAnalyticsJob(), Job)

    def test_attributes(self):
        job = SyncCloudflareAnalyticsJob()
        assert job.name == "sync_cloudflare_analytics"
        assert job.schedule == "every 5 minutes"
        assert job.idempotent is True

    async def test_token_missing_while_account_set_is_degraded(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        """poindexter#555: account configured but token empty → DEGRADED
        (ok=False) + a finding, no rows inserted. Used to mask the dead
        ingest green for ~54 days."""
        sc = _sc(api_token="")
        with patch(
            "services.jobs.sync_cloudflare_analytics.emit_finding"
        ) as mock_finding:
            result = await SyncCloudflareAnalyticsJob().run(
                clean_test_tables, {"_site_config": sc}
            )
        assert result.ok is False
        assert result.changes_made == 0
        mock_finding.assert_called_once()
        async with clean_test_tables.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM page_views")
        assert count == 0

    async def test_skips_cleanly_when_account_id_missing(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        sc = _sc(account_id="")
        result = await SyncCloudflareAnalyticsJob().run(
            clean_test_tables, {"_site_config": sc}
        )
        assert result.ok is True
        assert result.changes_made == 0

    async def test_empty_response_advances_watermark(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        """Empty CF response: no inserts, but the high-water mark advances
        so the next pull is bounded."""
        sc = _sc()
        fake = _fake_httpx_with_rows(rows=[])
        with patch.dict("sys.modules", {"httpx": fake}):
            result = await SyncCloudflareAnalyticsJob().run(
                clean_test_tables, {"_site_config": sc}
            )
        assert result.ok is True
        assert result.changes_made == 0
        async with clean_test_tables.acquire() as conn:
            value = await conn.fetchval(
                "SELECT value FROM app_settings "
                "WHERE key = 'cloudflare_analytics_last_sync'"
            )
        # A run that found no rows still sets the watermark to "now"
        # — never the epoch.
        assert value is not None
        assert not value.startswith("1970")

    async def test_rows_inserted_into_page_views(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        """Happy path: rows from CF land in page_views."""
        rows = [
            {
                "slug": "post-a",
                "path": "/posts/post-a",
                "referrer": "https://google.com",
                "user_agent": "Mozilla/5.0",
                "created_at": "2026-05-28 21:00:00",
            },
            {
                "slug": "post-b",
                "path": "/posts/post-b",
                "referrer": "",
                "user_agent": "curl/8",
                "created_at": "2026-05-28 21:05:00",
            },
        ]
        sc = _sc()
        fake = _fake_httpx_with_rows(rows=rows)
        with patch.dict("sys.modules", {"httpx": fake}):
            result = await SyncCloudflareAnalyticsJob().run(
                clean_test_tables, {"_site_config": sc}
            )
        assert result.ok is True
        assert result.changes_made == 2

        async with clean_test_tables.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM page_views")
        assert count == 2

    async def test_watermark_only_advances_on_full_success(
        self, migrations_applied, clean_test_tables: asyncpg.Pool
    ):
        """A 500 from CF leaves the watermark untouched."""
        # Seed an existing watermark we can compare against.
        async with clean_test_tables.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO app_settings
                    (key, value, category, description, is_active, is_secret)
                VALUES (
                    'cloudflare_analytics_last_sync',
                    '2026-05-28T00:00:00+00:00',
                    'cloudflare',
                    'test seed',
                    true,
                    false
                )
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value
                """
            )
        sc = _sc()
        fake = _fake_httpx_with_rows(rows=[], status=500)
        with patch.dict("sys.modules", {"httpx": fake}):
            result = await SyncCloudflareAnalyticsJob().run(
                clean_test_tables, {"_site_config": sc}
            )
        assert result.ok is False
        async with clean_test_tables.acquire() as conn:
            value = await conn.fetchval(
                "SELECT value FROM app_settings "
                "WHERE key = 'cloudflare_analytics_last_sync'"
            )
        # Watermark unchanged from the seed
        assert value == "2026-05-28T00:00:00+00:00"
