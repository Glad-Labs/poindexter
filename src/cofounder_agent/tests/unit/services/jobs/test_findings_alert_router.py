"""Unit tests for FindingsAlertRouterJob — pin the audit_log → alert_events
bridge contract that closes the long-standing silent-route gap.

Captured 2026-05-15: 108 critical findings written to ``audit_log`` in
7 days, zero ever reached the operator. These tests make sure the bridge
forwards severity>=warn findings, uses a stable fingerprint the existing
``alert_dispatcher`` dedup engine can consume, and advances the watermark
only past successfully-routed rows.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.jobs.findings_alert_router import (
    FindingsAlertRouterJob,
    _build_alertname,
    _build_fingerprint,
    _normalize_severity,
)

pytestmark = pytest.mark.asyncio


class _FakePoolCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        return False


def _pool_with(*, fetchrow=None, fetch=None, execute=None):
    """Build a MagicMock asyncpg-style pool. ``fetchrow`` is for watermark
    read; ``fetch`` is for unrouted-findings select; ``execute`` is for
    all the inserts/upserts."""
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow)
    conn.fetch = AsyncMock(return_value=fetch or [])
    conn.execute = AsyncMock(return_value=execute)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_FakePoolCtx(conn))
    return pool, conn


# ---- Pure helpers ----------------------------------------------------------


def test_normalize_severity_maps_warn_to_warning():
    """emit_finding accepts 'warn'; Prometheus / alert_dispatcher expect
    'warning'. The bridge normalizes so the dispatcher's severity matrix
    routes correctly."""
    assert _normalize_severity("warn") == "warning"
    assert _normalize_severity("WARN") == "warning"


def test_normalize_severity_passes_critical_through():
    assert _normalize_severity("critical") == "critical"


def test_normalize_severity_passes_unknown_through():
    """Unknown severities pass through so the dispatcher can log the
    mismatch instead of silently dropping the finding."""
    assert _normalize_severity("urgent") == "urgent"
    assert _normalize_severity("") == ""


def test_build_fingerprint_prefers_dedup_key():
    """Caller-provided ``dedup_key`` is the stable identity; the bridge
    must use it so repeated fires of the same logical alert collapse
    into one dispatcher row."""
    fp = _build_fingerprint("audit_published_quality", {"dedup_key": "post:abc123"})
    assert fp == "finding:audit_published_quality:post:abc123"


def test_build_fingerprint_falls_back_to_source_kind():
    """When no dedup_key, source+kind is the coarsest-but-stable shape."""
    fp = _build_fingerprint("media_reconciliation", {"kind": "media_drift"})
    assert fp == "finding:media_reconciliation:media_drift"


def test_build_alertname_uses_source_and_kind():
    """alertname is operator-facing in Discord/Telegram embeds — keep
    1-to-1 with the audit_log row's source:kind shape."""
    assert _build_alertname("flag_missing_seo", {"kind": "missing_seo"}) == (
        "flag_missing_seo:missing_seo"
    )


# ---- Job behavior ----------------------------------------------------------


async def test_run_returns_ok_with_no_findings_above_watermark():
    """Fresh poll with no new rows — quiet success, watermark unchanged."""
    pool, conn = _pool_with(fetchrow={"value": "100"}, fetch=[])
    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.ok is True
    assert result.changes_made == 0
    assert "watermark 100" in result.detail
    # Should NOT issue an UPDATE for watermark since nothing to advance.
    # (fetch + fetchrow happened, but no execute calls.)
    assert conn.execute.await_count == 0


async def test_run_forwards_critical_finding_to_alert_events():
    """The bug case — a critical finding in audit_log must result in
    exactly one alert_events insert AND a watermark bump."""
    rows = [{
        "id": 250,
        "source": "media_reconciliation",
        "severity": "critical",
        "details": json.dumps({
            "kind": "media_drift",
            "title": "11 videos missing",
            "body": "details here",
            "dedup_key": "media_drift:videos",
        }),
    }]
    pool, conn = _pool_with(fetchrow={"value": "100"}, fetch=rows)

    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.ok is True
    assert result.changes_made == 1
    # Two execute calls: insert into alert_events, then watermark UPSERT.
    assert conn.execute.await_count == 2

    # First execute is the alert_events INSERT — verify shape.
    insert_call = conn.execute.await_args_list[0]
    insert_sql = insert_call.args[0]
    assert "INSERT INTO alert_events" in insert_sql
    assert insert_call.args[1] == "media_reconciliation:media_drift"  # alertname
    assert insert_call.args[2] == "critical"                          # severity
    # fingerprint must come from dedup_key so dispatcher dedup works
    assert insert_call.args[5] == "finding:media_reconciliation:media_drift:videos"

    # Second execute is the watermark UPSERT.
    upsert_call = conn.execute.await_args_list[1]
    assert "INSERT INTO app_settings" in upsert_call.args[0]
    assert upsert_call.args[1] == "findings_alert_route_watermark"
    assert upsert_call.args[2] == "250"


async def test_run_skips_info_severity():
    """``severity='info'`` findings stay in audit_log only — the bridge's
    SQL filters them out via ``severity = ANY(...)`` so they never
    appear in the fetch result. This test pins that contract from the
    job side: even if the fetch somehow returned an info row, the SQL
    is what blocks it (verified via the literal ``_ROUTABLE_SEVERITIES``
    tuple)."""
    from services.jobs.findings_alert_router import _ROUTABLE_SEVERITIES
    assert "info" not in _ROUTABLE_SEVERITIES
    assert set(_ROUTABLE_SEVERITIES) == {"warn", "warning", "critical"}


async def test_run_advances_watermark_past_successfully_routed_rows():
    """Watermark advances to the MAX id in the batch — guarantees no
    row is processed twice on the next cycle."""
    rows = [
        {"id": 101, "source": "src", "severity": "warn", "details": json.dumps({"kind": "k"})},
        {"id": 102, "source": "src", "severity": "warn", "details": json.dumps({"kind": "k"})},
        {"id": 103, "source": "src", "severity": "warn", "details": json.dumps({"kind": "k"})},
    ]
    pool, conn = _pool_with(fetchrow={"value": "100"}, fetch=rows)

    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.changes_made == 3
    # Last execute call is the watermark UPSERT; its 2nd arg is the new value.
    upsert_call = conn.execute.await_args_list[-1]
    assert upsert_call.args[2] == "103"


async def test_run_keeps_watermark_if_all_rows_fail():
    """If every insert errors, watermark must NOT advance — next cycle
    retries the same rows. Anti-foot-gun against losing findings on a
    transient DB hiccup."""
    rows = [{"id": 200, "source": "src", "severity": "warn", "details": json.dumps({"kind": "k"})}]
    pool, conn = _pool_with(fetchrow={"value": "150"}, fetch=rows)
    # First execute (the alert_events insert) raises; the loop catches it,
    # and there should be NO second execute (the watermark UPSERT) because
    # max_id didn't advance past watermark.
    conn.execute = AsyncMock(side_effect=RuntimeError("DB down"))

    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.ok is False
    assert result.changes_made == 0
    # Exactly 1 attempted insert; no watermark update.
    assert conn.execute.await_count == 1


async def test_run_normalizes_warn_to_warning_in_alert_events():
    """End-to-end check that emit_finding's 'warn' becomes 'warning' in
    the alert_events row, so the dispatcher's severity matrix routes
    correctly."""
    rows = [{
        "id": 50,
        "source": "flag_missing_seo",
        "severity": "warn",
        "details": json.dumps({"kind": "missing_seo", "title": "10 posts"}),
    }]
    pool, conn = _pool_with(fetchrow={"value": "0"}, fetch=rows)

    await FindingsAlertRouterJob().run(pool, {})

    insert_call = conn.execute.await_args_list[0]
    severity_arg = insert_call.args[2]
    assert severity_arg == "warning"  # NOT "warn"


async def test_run_handles_missing_dedup_key_with_source_kind_fallback():
    """When emit_finding callers don't set dedup_key, the bridge must
    still produce a STABLE fingerprint so dispatcher dedup works for
    those alert classes. ``source:kind`` is the agreed fallback."""
    rows = [{
        "id": 99,
        "source": "audit_published_quality",
        "severity": "critical",
        "details": json.dumps({"kind": "quality_regression", "title": "5 issues"}),
        # NO dedup_key field
    }]
    pool, conn = _pool_with(fetchrow={"value": "0"}, fetch=rows)

    await FindingsAlertRouterJob().run(pool, {})

    insert_call = conn.execute.await_args_list[0]
    fingerprint_arg = insert_call.args[5]
    assert fingerprint_arg == "finding:audit_published_quality:quality_regression"


async def test_watermark_missing_resets_to_zero():
    """Fresh install / corrupted row — bridge should replay everything
    from id=0 (which is what we want; alert_dispatcher dedup will collapse
    historical duplicates)."""
    pool, conn = _pool_with(fetchrow=None, fetch=[])
    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.ok is True
    assert "watermark 0" in result.detail


async def test_watermark_unparseable_resets_to_zero():
    """Defensive: a manual psql write with garbage in the value column
    shouldn't crash the bridge."""
    pool, conn = _pool_with(fetchrow={"value": "not-a-number"}, fetch=[])
    result = await FindingsAlertRouterJob().run(pool, {})

    assert result.ok is True
    assert "watermark 0" in result.detail
