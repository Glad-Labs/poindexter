"""Unit tests — check_memory_staleness best-effort-failure visibility.

Silent-excepts OLD-debt burn-down (batch 5). This job's entire purpose is to
alert the operator when a pgvector memory writer stops syncing. Two of its five
flagged handlers silently defeated that purpose and now emit a non-paging
``info`` finding:

* the Discord ``notify_operator`` call — if it fails silently, the operator
  never learns a writer went stale (the finding is the backup channel, and it
  persists to the Findings board even when Discord itself is down).
* the cooldown-state persist — if it fails silently, the anti-spam dedup map is
  not saved, so the next run can re-alert (duplicate pages) or lose track.

The other three are documented ``# silent-ok``: two narrow parse guards (a
malformed threshold keeps the default; a corrupt cooldown timestamp fails OPEN
so the alert still fires — the safe direction) and the audit-row write (the
operator-facing alert still goes out via Discord; only the audit trail row is
lost).

Mirrors ``tests/unit/modules/content/test_writer_core_silent_failures.py`` and
reuses the harness style of ``test_check_memory_staleness_job.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.jobs.check_memory_staleness import CheckMemoryStalenessJob

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _make_pool(execute_side_effect=None):
    async def _fetchrow(query: str, *args: Any) -> Any:
        return None  # no stored last-alerts / setting overrides

    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock(
        side_effect=execute_side_effect, return_value="INSERT 0 1",
    )
    return pool


def _stale_stats(writer: str = "openclaw", hours: int = 10) -> dict:
    now = datetime.now(timezone.utc)
    return {"by_writer": {writer: {"newest": now - timedelta(hours=hours), "count": 100}}}


def _fake_memory_client(stats):
    mc = MagicMock()
    mc.stats = AsyncMock(return_value=stats)

    class _CtxMgr:
        async def __aenter__(self):
            return mc

        async def __aexit__(self, *a):
            return False

    return MagicMock(return_value=_CtxMgr())


def _capture(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


async def test_notify_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)
    pool = _make_pool()
    memory_cls = _fake_memory_client(_stale_stats())

    with patch("poindexter.memory.MemoryClient", memory_cls), patch(
        "services.integrations.operator_notify.notify_operator",
        new=AsyncMock(side_effect=RuntimeError("discord down")),
    ), patch("services.audit_log.audit_log_bg", new=MagicMock()):
        result = await CheckMemoryStalenessJob().run(pool, {})

    # The job still completes; the alert just didn't reach Discord.
    assert result.ok is True
    findings = [c for c in calls if c["kind"] == "memory_stale_notify_failed"]
    assert len(findings) == 1
    assert findings[0]["dedup_key"] == "memory_stale_notify_failed:openclaw"
    assert findings[0].get("severity", "info") == "info"


async def test_state_persist_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)
    # notify succeeds, so new_last_alerts changes and the persist is attempted —
    # then the persist itself raises.
    pool = _make_pool(execute_side_effect=RuntimeError("app_settings write boom"))
    memory_cls = _fake_memory_client(_stale_stats())

    with patch("poindexter.memory.MemoryClient", memory_cls), patch(
        "services.integrations.operator_notify.notify_operator", new=AsyncMock(),
    ), patch("services.audit_log.audit_log_bg", new=MagicMock()):
        result = await CheckMemoryStalenessJob().run(pool, {})

    assert result.ok is True
    findings = [c for c in calls if c["kind"] == "memory_stale_state_persist_failed"]
    assert len(findings) == 1
    assert findings[0]["dedup_key"] == "memory_stale_state_persist_failed"
    assert findings[0].get("severity", "info") == "info"


async def test_clean_run_emits_no_finding(monkeypatch):
    """A stale writer whose notify + persist both succeed emits no finding."""
    calls = _capture(monkeypatch)
    pool = _make_pool()
    memory_cls = _fake_memory_client(_stale_stats())

    with patch("poindexter.memory.MemoryClient", memory_cls), patch(
        "services.integrations.operator_notify.notify_operator", new=AsyncMock(),
    ), patch("services.audit_log.audit_log_bg", new=MagicMock()):
        await CheckMemoryStalenessJob().run(pool, {})

    assert calls == []
