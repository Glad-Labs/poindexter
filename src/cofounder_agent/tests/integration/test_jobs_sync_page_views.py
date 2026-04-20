"""Integration test for SyncPageViewsJob against the real test DB."""

from __future__ import annotations

import asyncpg
import pytest

from plugins import Job
from services.jobs.sync_page_views import SyncPageViewsJob
from tests.integration.conftest import requires_real_services

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, requires_real_services]


class TestSyncPageViewsJob:
    def test_conforms_to_job_protocol(self):
        assert isinstance(SyncPageViewsJob(), Job)

    def test_attributes(self):
        job = SyncPageViewsJob()
        assert job.name == "sync_page_views"
        assert job.schedule == "every 30 minutes"
        assert job.idempotent is True

    async def test_returns_ok_when_database_url_missing(
        self, migrations_applied, clean_test_tables: asyncpg.Pool, monkeypatch
    ):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        result = await SyncPageViewsJob().run(clean_test_tables, {})
        assert result.ok is True
        assert "no DATABASE_URL" in result.detail
        assert result.changes_made == 0

    async def test_creates_table_idempotently(
        self, migrations_applied, clean_test_tables: asyncpg.Pool, monkeypatch
    ):
        # Point the cloud DSN at the same test DB so sync is a no-op
        # but the CREATE TABLE path runs.
        async with clean_test_tables.acquire() as conn:
            str(conn._addr) if hasattr(conn, "_addr") else None
        # Use an actual DSN for the test DB via asyncpg connect string.
        # The real services fixture exposes it via environment.
        import os
        test_dsn = os.getenv("TEST_ADMIN_DSN") or os.getenv("POSTGRES_ADMIN_DSN") or ""
        if not test_dsn:
            pytest.skip("no test DSN available")
        test_dsn = test_dsn.rsplit("/", 1)[0] + "/poindexter_test"
        monkeypatch.setenv("DATABASE_URL", test_dsn)

        result = await SyncPageViewsJob().run(clean_test_tables, {})

        # Table should now exist.
        async with clean_test_tables.acquire() as conn:
            exists = await conn.fetchval(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='page_views'"
            )
        assert exists == 1
        assert result.ok is True
