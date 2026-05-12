"""Unit tests for ``services/jobs/static_export_reconciliation.py``.

The watchdog has three outcomes worth pinning:

1. **In sync** — DB count == manifest post_count AND manifest is
   not stale → no rebuild, no finding, ok=True.
2. **Count drift** — DB has more published posts than R2 → trigger
   ``export_full_rebuild``, emit a finding, ok mirrors the rebuild.
3. **Manifest fetch failure** — R2 unreachable → treat as drift,
   fire rebuild + finding so the next poll re-evaluates against a
   fresh manifest.

DB pool, httpx client, and ``export_full_rebuild`` are all mocked.
No real network or DB calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.jobs.static_export_reconciliation import (
    StaticExportReconciliationJob,
)


def _make_pool(db_count: int, db_latest: datetime | None) -> Any:
    """asyncpg pool whose conn.fetchrow returns the given DB summary."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        return_value={"db_count": db_count, "db_latest": db_latest}
    )
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=ctx)
    return pool


def _patch_manifest(payload: dict | None, *, http_error: Exception | None = None):
    """Patch httpx.AsyncClient so its GET returns ``payload`` as JSON, or
    raises ``http_error`` if provided.
    """
    async def _stub_get(self, url, *a, **kw):  # noqa: ANN001, ARG001
        if http_error is not None:
            raise http_error
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.json = MagicMock(return_value=payload)
        resp.raise_for_status = MagicMock()
        return resp

    return patch.object(httpx.AsyncClient, "get", _stub_get)


@pytest.mark.unit
class TestStaticExportReconciliation:

    @pytest.mark.asyncio
    async def test_in_sync_returns_ok_no_rebuild(self):
        """DB count == R2 count AND manifest fresh → no rebuild."""
        now = datetime.now(timezone.utc)
        pool = _make_pool(db_count=59, db_latest=now - timedelta(minutes=5))
        manifest = {
            "post_count": 59,
            "exported_at": now.isoformat(),
        }

        with _patch_manifest(manifest), patch(
            "services.static_export_service.export_full_rebuild",
            new=AsyncMock(),
        ) as rebuild_mock:
            job = StaticExportReconciliationJob()
            result = await job.run(pool, config={"alert_on_drift": False})

        assert result.ok is True
        assert "in sync" in result.detail
        assert result.changes_made == 0
        assert result.metrics["drift"] == 0
        rebuild_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_count_drift_triggers_rebuild(self):
        """DB has 60 published posts, R2 manifest claims 56 → rebuild."""
        now = datetime.now(timezone.utc)
        pool = _make_pool(db_count=60, db_latest=now)
        manifest = {"post_count": 56, "exported_at": now.isoformat()}

        with _patch_manifest(manifest), patch(
            "services.static_export_service.export_full_rebuild",
            new=AsyncMock(return_value={"success": True}),
        ) as rebuild_mock, patch(
            "services.jobs.static_export_reconciliation.emit_finding",
        ) as finding_mock:
            job = StaticExportReconciliationJob()
            result = await job.run(pool, config={"alert_on_drift": True})

        assert result.ok is True  # Rebuild succeeded
        assert "drift detected" in result.detail
        assert result.changes_made == 1
        assert result.metrics["drift"] == 1
        assert result.metrics["rebuild_ok"] == 1
        rebuild_mock.assert_awaited_once_with(pool)
        finding_mock.assert_called_once()
        finding_kwargs = finding_mock.call_args.kwargs
        assert finding_kwargs["dedup_key"] == "r2_static_drift"
        # Drift warning, not critical, because rebuild succeeded
        assert finding_kwargs["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_manifest_fetch_failure_treated_as_drift(self):
        """R2 unreachable → assume drift, rebuild + finding."""
        pool = _make_pool(db_count=42, db_latest=datetime.now(timezone.utc))

        with _patch_manifest(None, http_error=httpx.ConnectError("dns")), patch(
            "services.static_export_service.export_full_rebuild",
            new=AsyncMock(return_value={"success": False, "error": "boom"}),
        ) as rebuild_mock, patch(
            "services.jobs.static_export_reconciliation.emit_finding",
        ) as finding_mock:
            job = StaticExportReconciliationJob()
            result = await job.run(pool, config={"alert_on_drift": True})

        # Rebuild failed in this scenario, so ok=False (matches rebuild outcome)
        assert result.ok is False
        rebuild_mock.assert_awaited_once_with(pool)
        # Finding should be critical when rebuild also failed
        finding_kwargs = finding_mock.call_args.kwargs
        assert finding_kwargs["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_alert_on_drift_false_skips_finding(self):
        """When the operator opts out, drift still rebuilds but no finding fires."""
        now = datetime.now(timezone.utc)
        pool = _make_pool(db_count=10, db_latest=now)
        manifest = {"post_count": 8, "exported_at": now.isoformat()}

        with _patch_manifest(manifest), patch(
            "services.static_export_service.export_full_rebuild",
            new=AsyncMock(return_value={"success": True}),
        ), patch(
            "services.jobs.static_export_reconciliation.emit_finding",
        ) as finding_mock:
            job = StaticExportReconciliationJob()
            await job.run(pool, config={"alert_on_drift": False})

        finding_mock.assert_not_called()
