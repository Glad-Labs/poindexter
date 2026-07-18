"""Unit tests — shot-render audit visibility.

Silent-excepts OLD-debt burn-down (batch 19). ``_log_shot_audit`` writes one
``video_shot_rendered`` row per shot, and its own docstring states the purpose:
"Operator-visible in the audit-log dashboard so the per-source success rate can
be monitored without grepping container logs." The insert was swallowed at DEBUG
only — invisible in prod (Loki ships INFO+) — so a persistent failure silently
blanked exactly the monitoring the write exists for, including the
``severity="warning"`` rows written for FAILED shots.

The signal is ``logger.warning``, NOT ``emit_finding``: findings are themselves
``audit_log`` rows (``emit_finding`` -> ``audit_log_bg``), so a finding about a
broken audit_log would vanish in the very outage it reports. Loki survives a DB
outage. (Contrast batch 16, where the failing write was ``cost_logs`` — a
different table — so a finding was correct there.)

Per-shot volume is bounded (a shot list is single-digit-to-~20 shots), so a
plain warning is acceptable here without restructuring the caller into the
aggregate pattern.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.video_renderers.shot_list_renderer import (
    ShotRenderResult,
    _log_shot_audit,
)

_LOGGER_NAME = "services.video_renderers.shot_list_renderer"


def _audit_warnings(caplog) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and "audit_log" in r.getMessage()
    ]


def _shot(success: bool = False) -> ShotRenderResult:
    return ShotRenderResult(
        idx=3,
        source="wan2_1",
        success=success,
        duration_s=4.5,
        error=None if success else "render timeout",
    )


@pytest.mark.asyncio
async def test_shot_audit_write_failure_emits_warning(caplog):
    """A failed shot-audit insert is surfaced at WARNING (Loki)."""
    pool = MagicMock()
    pool.execute = AsyncMock(side_effect=RuntimeError("audit_log unavailable"))
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)

    # Never raises — audit logging is best-effort and must not fail a render.
    await _log_shot_audit(
        pool,
        post_id="post-1",
        shot_result=_shot(success=False),
        qa_score=0.4,
        qa_outcome="rejected",
        rung="fallback",
    )

    warnings = _audit_warnings(caplog)
    assert len(warnings) == 1
    # The shot index is what an operator needs to correlate the gap.
    assert "3" in warnings[0]


@pytest.mark.asyncio
async def test_shot_audit_write_success_emits_no_warning(caplog):
    """The healthy path stays quiet."""
    pool = MagicMock()
    pool.execute = AsyncMock(return_value="INSERT 0 1")
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)

    await _log_shot_audit(
        pool,
        post_id="post-1",
        shot_result=_shot(success=True),
        qa_score=0.9,
        qa_outcome="approved",
        rung="primary",
    )

    pool.execute.assert_awaited_once()
    assert _audit_warnings(caplog) == []
