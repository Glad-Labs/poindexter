"""Unit tests — alert_dispatcher silent-failure surfacing.

Silent-failure audit follow-up. The alert dispatcher is one half of the
operator paging plane; a swallowed exception there can degrade alert
dedup/coalescing without the operator ever seeing why. These tests pin
the three ``alert_dedup_state`` write handlers that were escalated from
``logger.debug`` (invisible at prod log level) to ``logger.warning``:

* ``_insert_dedup_state`` — first-fire baseline write.
* ``_bump_dedup_state``   — repeat-count increment on a suppressed fire.
* the reset branch in ``_evaluate_dedup_decision`` — window-expired reset.

A persistent failure in any of these means dedup state is wrong, so the
operator gets re-paged on every cycle. That is exactly the kind of
alerting-plane degradation that must NOT be invisible.

Mirrors the caplog assertion pattern in
``tests/unit/services/test_publish_service_bg_exceptions.py``.
"""

from __future__ import annotations

import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
# Mirror the path-prelude pattern from test_alert_dispatcher_dedup.py.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import alert_dispatcher as ad  # noqa: E402

_LOGGER = "brain.alert_dispatcher"


@pytest.mark.unit
@pytest.mark.asyncio
class TestDedupStateWriteFailuresAreVisible:
    """The three alert_dedup_state writes must WARN (not debug) on DB error."""

    async def test_insert_dedup_state_logs_warning_on_db_failure(self, caplog):
        pool = MagicMock()
        pool.execute = AsyncMock(side_effect=RuntimeError("dedup table gone"))

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            await ad._insert_dedup_state(
                pool,
                fingerprint="abc123def456",
                severity="critical",
                source="OpenclawDown",
                sample_message="openclaw is down",
                now=datetime.now(UTC),
            )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "dedup-state insert failure must be visible at WARNING"
        combined = " ".join(r.getMessage() for r in warnings)
        assert "dedup" in combined.lower()
        assert "dedup table gone" in combined, "underlying error must be surfaced"

    async def test_bump_dedup_state_logs_warning_on_db_failure(self, caplog):
        pool = MagicMock()
        pool.execute = AsyncMock(side_effect=RuntimeError("bump exploded"))

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            await ad._bump_dedup_state(
                pool, fingerprint="abc123def456", now=datetime.now(UTC)
            )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "dedup-state bump failure must be visible at WARNING"
        combined = " ".join(r.getMessage() for r in warnings)
        assert "bump exploded" in combined

    async def test_dedup_reset_logs_warning_on_db_failure(self, caplog):
        """Window-expired reset UPDATE failing must WARN. The decision still
        returns ``dispatch`` (fail-open — a broken dedup write must never
        swallow the alert), but the failure is no longer invisible."""
        old = datetime(2026, 1, 1, tzinfo=UTC)
        now = datetime(2026, 1, 2, tzinfo=UTC)  # 24h later — far past the window

        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value={
            "fingerprint": "fp",
            "first_seen_at": old,
            "last_seen_at": old,
            "repeat_count": 3,
            "summary_dispatched_at": None,
            "severity": "warning",
            "source": "ServiceX",
            "sample_message": "ServiceX down",
        })
        pool.execute = AsyncMock(side_effect=RuntimeError("reset exploded"))

        config = {"suppress_window_minutes": 30, "summarize_threshold_minutes": 30}

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            decision = await ad._evaluate_dedup_decision(
                pool,
                message="ServiceX down",
                severity="warning",
                alertname="ServiceX",
                category="infra",
                config=config,
                now_fn=lambda: now,
            )

        # Fail-open: the alert still dispatches despite the write failure.
        assert decision["action"] == "dispatch"

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "dedup-state reset failure must be visible at WARNING"
        assert "reset exploded" in " ".join(r.getMessage() for r in warnings)
