"""Unit tests for ``services/jobs/sync_newsletter_subscribers.py``.

Cloud asyncpg connection + local pool are mocked. Focus: env-var
gate, watermark behavior, upsert pass-through, and per-stage error
isolation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.sync_newsletter_subscribers import SyncNewsletterSubscribersJob


def _make_pool(
    watermark: Any = None,
    ddl_raises: BaseException | None = None,
    upsert_raises: BaseException | None = None,
) -> Any:
    """Local pool. fetchrow returns watermark. execute routes by SQL."""
    conn = AsyncMock()

    async def _execute(query: str, *args: Any) -> str:
        if ddl_raises is not None and "CREATE TABLE" in query:
            raise ddl_raises
        if upsert_raises is not None and "INSERT INTO newsletter_subscribers" in query:
            raise upsert_raises
        return "INSERT 0 1"

    conn.execute = AsyncMock(side_effect=_execute)
    conn.fetchrow = AsyncMock(return_value={"max_ts": watermark})

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)

    # transaction() also returns an async context manager
    tx_ctx = AsyncMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool, conn


def _patched_asyncpg_connect(
    rows: list[dict] | None = None,
    connect_raises: BaseException | None = None,
    fetch_raises: BaseException | None = None,
):
    """Patch the ``import asyncpg; await asyncpg.connect(...)`` call.

    Returns a context-manager factory that yields a connection whose
    fetch() returns the given rows.
    """
    cloud_conn = AsyncMock()
    if fetch_raises is not None:
        cloud_conn.fetch = AsyncMock(side_effect=fetch_raises)
    else:
        cloud_conn.fetch = AsyncMock(return_value=rows or [])
    cloud_conn.close = AsyncMock(return_value=None)

    async def _connect(url: str) -> Any:
        if connect_raises is not None:
            raise connect_raises
        return cloud_conn

    fake_asyncpg = MagicMock()
    fake_asyncpg.connect = _connect
    return fake_asyncpg, cloud_conn


def _sample_row(id_: int = 1, email: str = "a@b.com") -> dict:
    """Realistic subscriber row with every column the upsert expects."""
    now = datetime(2026, 4, 20, 8, 0, tzinfo=timezone.utc)
    return {
        "id": id_,
        "email": email,
        "first_name": "Alice",
        "last_name": "Smith",
        "company": "Glad Labs",
        "interest_categories": None,
        "subscribed_at": now,
        "ip_address": "1.2.3.4",
        "user_agent": "Mozilla",
        "verified": True,
        "verification_token": None,
        "verified_at": now,
        "unsubscribed_at": None,
        "unsubscribe_reason": None,
        "created_at": now,
        "updated_at": now,
        "marketing_consent": True,
    }


class TestContract:
    def test_has_required_attrs(self):
        job = SyncNewsletterSubscribersJob()
        assert job.name == "sync_newsletter_subscribers"
        assert job.schedule == "every 30 minutes"
        assert job.idempotent is True


class TestRun:
    @pytest.mark.asyncio
    async def test_no_database_url_skips_cleanly(self):
        pool, _ = _make_pool()
        job = SyncNewsletterSubscribersJob()
        with patch.dict("os.environ", {}, clear=True):
            result = await job.run(pool, {})
        assert result.ok is True
        assert result.changes_made == 0
        assert "no DATABASE_URL" in result.detail

    @pytest.mark.asyncio
    async def test_no_new_rows_returns_ok_zero(self):
        pool, _ = _make_pool(watermark=None)
        fake_asyncpg, _ = _patched_asyncpg_connect(rows=[])
        with patch.dict("os.environ", {"DATABASE_URL": "postgres://cloud"}), \
             patch("builtins.__import__", side_effect=_patch_import("asyncpg", fake_asyncpg)):
            job = SyncNewsletterSubscribersJob()
            result = await job.run(pool, {})
        assert result.ok is True
        assert result.changes_made == 0

    @pytest.mark.asyncio
    async def test_successful_sync_upserts_rows(self):
        pool, conn = _make_pool(watermark=None)
        fake_asyncpg, cloud_conn = _patched_asyncpg_connect(rows=[
            _sample_row(1, "a@b.com"),
            _sample_row(2, "c@d.com"),
        ])
        with patch.dict("os.environ", {"DATABASE_URL": "postgres://cloud"}), \
             patch("builtins.__import__", side_effect=_patch_import("asyncpg", fake_asyncpg)):
            job = SyncNewsletterSubscribersJob()
            result = await job.run(pool, {})

        assert result.ok is True
        assert result.changes_made == 2
        assert result.metrics["rows_synced"] == 2
        # Two UPSERT executes, plus one CREATE TABLE — check upsert count.
        upserts = [
            c for c in conn.execute.await_args_list
            if "INSERT INTO newsletter_subscribers" in c.args[0]
        ]
        assert len(upserts) == 2

    @pytest.mark.asyncio
    async def test_watermark_threads_into_query(self):
        watermark = datetime(2026, 4, 15, 0, 0, tzinfo=timezone.utc)
        pool, _ = _make_pool(watermark=watermark)
        fake_asyncpg, cloud_conn = _patched_asyncpg_connect(rows=[])
        with patch.dict("os.environ", {"DATABASE_URL": "postgres://cloud"}), \
             patch("builtins.__import__", side_effect=_patch_import("asyncpg", fake_asyncpg)):
            job = SyncNewsletterSubscribersJob()
            await job.run(pool, {"batch_size": 123})
        # fetch should have been called with (query, watermark, batch_size)
        call = cloud_conn.fetch.await_args
        assert call.args[1] == watermark
        assert call.args[2] == 123
        assert "WHERE updated_at" in call.args[0]

    @pytest.mark.asyncio
    async def test_no_watermark_queries_without_where(self):
        pool, _ = _make_pool(watermark=None)
        fake_asyncpg, cloud_conn = _patched_asyncpg_connect(rows=[])
        with patch.dict("os.environ", {"DATABASE_URL": "postgres://cloud"}), \
             patch("builtins.__import__", side_effect=_patch_import("asyncpg", fake_asyncpg)):
            job = SyncNewsletterSubscribersJob()
            await job.run(pool, {})
        call = cloud_conn.fetch.await_args
        assert "WHERE" not in call.args[0]

    @pytest.mark.asyncio
    async def test_local_ddl_failure_returns_not_ok(self):
        pool, _ = _make_pool(ddl_raises=RuntimeError("permission denied"))
        with patch.dict("os.environ", {"DATABASE_URL": "postgres://cloud"}):
            job = SyncNewsletterSubscribersJob()
            result = await job.run(pool, {})
        assert result.ok is False
        assert "local setup failed" in result.detail

    @pytest.mark.asyncio
    async def test_cloud_connect_failure_returns_not_ok(self):
        pool, _ = _make_pool()
        fake_asyncpg, _ = _patched_asyncpg_connect(
            connect_raises=RuntimeError("dns fail"),
        )
        with patch.dict("os.environ", {"DATABASE_URL": "postgres://cloud"}), \
             patch("builtins.__import__", side_effect=_patch_import("asyncpg", fake_asyncpg)):
            job = SyncNewsletterSubscribersJob()
            result = await job.run(pool, {})
        assert result.ok is False
        assert "cloud pull failed" in result.detail

    @pytest.mark.asyncio
    async def test_upsert_failure_returns_not_ok(self):
        pool, _ = _make_pool(upsert_raises=RuntimeError("disk full"))
        fake_asyncpg, _ = _patched_asyncpg_connect(rows=[_sample_row(1)])
        with patch.dict("os.environ", {"DATABASE_URL": "postgres://cloud"}), \
             patch("builtins.__import__", side_effect=_patch_import("asyncpg", fake_asyncpg)):
            job = SyncNewsletterSubscribersJob()
            result = await job.run(pool, {})
        assert result.ok is False
        assert "upsert failed" in result.detail


# ---------------------------------------------------------------------------
# Helper: selectively patch ``import asyncpg`` to return a mock.
# ---------------------------------------------------------------------------


def _patch_import(target: str, replacement: Any):
    """Returns a side_effect for builtins.__import__ that substitutes
    ``target`` with ``replacement`` while letting every other import go
    through normally."""
    original_import = __import__

    def _hook(name, *args, **kwargs):
        if name == target:
            return replacement
        return original_import(name, *args, **kwargs)

    return _hook
