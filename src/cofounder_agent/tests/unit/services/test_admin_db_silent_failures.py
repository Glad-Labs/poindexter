"""Unit tests — admin_db router-learning write-failure visibility.

Silent-excepts OLD-debt burn-down (batch 10). ``admin_db.py`` feeds the model
router's learning substrate via three best-effort DB writes that were swallowed
at DEBUG only (invisible in prod, which ships INFO+):

* ``_mirror_model_performance`` — the per-call ``model_performance`` mirror in
  ``log_cost`` (router-learning signal).
* ``_mirror_routing_outcome`` — the per-call ``routing_outcomes`` mirror in
  ``log_cost`` (the ML gateway's ``(task_type, model) -> outcome`` signal).
* ``mark_model_performance_outcome`` — the downstream ``human_approved`` /
  ``post_published`` outcome flip (the approval/publish signal the router
  learns from — arguably the most important of the three).

Each now emits a non-paging ``info`` finding on failure so a persistent
schema/constraint break in the learning substrate becomes visible on the
Findings dashboard instead of degrading the router in the dark. None raise — a
learning-signal failure must never break the hot-path ``log_cost`` or the
primary accounting write (batch-6 ``_record_capability_outcomes`` precedent).
The two ``log_cost`` mirrors were lifted into module-level helpers purely for
this testability (batch-1 ``_snapshot_initial_draft`` precedent); ``log_cost``
calls them after the primary ``cost_logs`` write, preserving the ``"system"``
pseudo-model skip.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import services.admin_db as admin_db

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _capture(monkeypatch) -> list[dict]:
    """Intercept emit_finding at its definition module so the function-local
    ``from utils.findings import emit_finding`` inside each handler resolves to
    our stub (attribute looked up on the module at call time)."""
    calls: list[dict] = []
    import utils.findings as findings_module

    monkeypatch.setattr(findings_module, "emit_finding", lambda **kw: calls.append(kw))
    return calls


class _AcquireCtx:
    """Mimic asyncpg ``pool.acquire()``'s async context manager."""

    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


# A cost_log carrying every key the mirror INSERTs read without ``.get`` — so a
# failure test fails ONLY from the mocked execute side-effect, never a KeyError.
_COST_LOG = {"model": "gemma-4-31B", "task_id": "t-1", "phase": "draft"}


# --------------------------------------------------------------------------
# _mirror_model_performance
# --------------------------------------------------------------------------


async def test_mirror_model_performance_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=RuntimeError("mp insert boom"))

    # Must not raise — the mirror is additive observability.
    await admin_db._mirror_model_performance(conn, _COST_LOG)

    findings = [c for c in calls if c["kind"] == "model_performance_mirror_write_failed"]
    assert len(findings) == 1
    assert findings[0].get("severity") == "info"
    assert "model_performance_mirror_write_failed" in findings[0]["dedup_key"]


async def test_mirror_model_performance_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    await admin_db._mirror_model_performance(conn, _COST_LOG)

    assert calls == []


# --------------------------------------------------------------------------
# _mirror_routing_outcome
# --------------------------------------------------------------------------


async def test_mirror_routing_outcome_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=RuntimeError("ro insert boom"))

    await admin_db._mirror_routing_outcome(conn, _COST_LOG)

    findings = [c for c in calls if c["kind"] == "routing_outcomes_mirror_write_failed"]
    assert len(findings) == 1
    assert findings[0].get("severity") == "info"
    assert "routing_outcomes_mirror_write_failed" in findings[0]["dedup_key"]


async def test_mirror_routing_outcome_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="INSERT 0 1")

    await admin_db._mirror_routing_outcome(conn, _COST_LOG)

    assert calls == []


# --------------------------------------------------------------------------
# mark_model_performance_outcome
# --------------------------------------------------------------------------


async def test_mark_outcome_failure_emits_finding(monkeypatch):
    calls = _capture(monkeypatch)
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=RuntimeError("outcome update boom"))
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireCtx(conn))
    db = admin_db.AdminDatabase(pool=pool)

    # Must not raise — analytics, not accounting.
    await db.mark_model_performance_outcome("task-9", human_approved=True)

    findings = [c for c in calls if c["kind"] == "model_performance_outcome_update_failed"]
    assert len(findings) == 1
    assert findings[0].get("severity") == "info"
    assert "model_performance_outcome_update_failed" in findings[0]["dedup_key"]


async def test_mark_outcome_success_emits_no_finding(monkeypatch):
    calls = _capture(monkeypatch)
    conn = MagicMock()
    conn.execute = AsyncMock(return_value="UPDATE 1")
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=_AcquireCtx(conn))
    db = admin_db.AdminDatabase(pool=pool)

    await db.mark_model_performance_outcome("task-9", human_approved=True)

    assert calls == []
