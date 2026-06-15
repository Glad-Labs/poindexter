"""Unit tests — brain_daemon silent-failure surfacing.

Silent-failure audit follow-up. The brain daemon is the self-healing
watchdog AND the operator-paging plane; a swallowed exception in its
own cycle code can take outage detection / self-healing / liveness
signalling dark while the daemon still *looks* healthy. These tests pin
the handlers escalated away from ``logger.debug`` (below the prod log
level and below GlitchTip's ERROR event gate):

* wholesale cycle-step failures now log at ERROR (matching the existing
  ``process_queue`` / ``self_maintain`` precedent):
    - ``auto_remediate``        — the self-healing sweeper
    - ``generate_daily_digest`` — operator digest
    - ``update_system_metrics`` — knowledge-graph metrics
* targeted observability writes now log at WARNING:
    - ``_stamp_auto_cancelled`` call site — the sweeper-cancel metric
    - ``_record_operator_paged``         — feeds the silent-alerter watchdog

Mirrors the caplog assertion pattern in
``tests/unit/services/test_publish_service_bg_exceptions.py``.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
# Mirror the path-prelude pattern from test_brain_daemon_auto_remediate.py.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import brain_daemon as bd  # noqa: E402

_LOGGER = "brain"


@pytest.mark.unit
@pytest.mark.asyncio
class TestCycleStepWholesaleFailuresLogError:
    """A wholesale failure of a cycle step must log at ERROR (not debug),
    so a dead self-healing / metrics path reaches Loki + GlitchTip rather
    than vanishing below the prod log level."""

    async def test_auto_remediate_logs_error_on_db_failure(self, caplog):
        pool = MagicMock()
        # _setting_int reads tolerate any value; the sweeper UPDATE is what
        # blows up, propagating to the wholesale handler.
        pool.fetchval = AsyncMock(return_value="180")
        pool.fetch = AsyncMock(side_effect=RuntimeError("pipeline_tasks query exploded"))
        pool.fetchrow = AsyncMock(return_value=None)
        pool.execute = AsyncMock()

        with caplog.at_level(logging.ERROR, logger=_LOGGER):
            await bd.auto_remediate(pool)

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors, "auto_remediate wholesale failure must be visible at ERROR"
        combined = " ".join(r.getMessage() for r in errors)
        assert "Auto-remediation failed" in combined
        assert "pipeline_tasks query exploded" in combined

    async def test_generate_daily_digest_logs_error_on_db_failure(self, caplog):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("brain_knowledge read exploded"))
        pool.fetchval = AsyncMock(return_value="6")
        pool.execute = AsyncMock()

        with caplog.at_level(logging.ERROR, logger=_LOGGER):
            await bd.generate_daily_digest(pool)

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors, "digest wholesale failure must be visible at ERROR"
        combined = " ".join(r.getMessage() for r in errors)
        assert "Operator digest failed" in combined
        assert "brain_knowledge read exploded" in combined

    async def test_update_system_metrics_logs_error_on_db_failure(self, caplog):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("posts count exploded"))
        pool.fetch = AsyncMock(return_value=[])
        pool.execute = AsyncMock()

        with caplog.at_level(logging.ERROR, logger=_LOGGER):
            await bd.update_system_metrics(pool)

        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors, "metrics-update wholesale failure must be visible at ERROR"
        combined = " ".join(r.getMessage() for r in errors)
        assert "Metrics update failed" in combined
        assert "posts count exploded" in combined


@pytest.mark.unit
@pytest.mark.asyncio
class TestTargetedObservabilityWritesLogWarning:
    """Targeted observability writes whose failure is otherwise invisible
    must log at WARNING."""

    async def test_record_operator_paged_logs_warning_on_db_failure(self, caplog):
        """The operator_paged audit row feeds the silent-alerter watchdog
        (it distinguishes 'alerter broken' from 'nothing wrong'). A failed
        insert must not be invisible."""
        pool = MagicMock()
        # async-with pool.acquire() — make acquire() raise synchronously.
        pool.acquire = MagicMock(side_effect=RuntimeError("acquire failed"))

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            await bd._record_operator_paged(
                pool,
                {"source": "brain", "severity": "critical", "title": "t", "channels": {}},
                "detail body",
            )

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "operator_paged audit failure must be visible at WARNING"
        combined = " ".join(r.getMessage() for r in warnings)
        assert "operator_paged" in combined
        assert "acquire failed" in combined

    async def test_auto_cancel_stamp_failure_logs_warning(self, caplog, monkeypatch):
        """When the sweeper cancels a stuck task but the auto_cancelled_at
        stamp write fails, the metric loss must WARN — and must stay
        isolated (not fall through to the wholesale ERROR handler)."""
        # Stub notify so the end-of-function escalation send is a no-op.
        monkeypatch.setattr(bd, "notify", AsyncMock())

        pool = MagicMock()
        pool.fetchval = AsyncMock(return_value="180")
        pool.fetch = AsyncMock(side_effect=[
            [{"task_id": "abc12345", "topic": "A stuck topic"}],  # sweeper UPDATE
            [],  # awaiting_approval expire
        ])
        pool.fetchrow = AsyncMock(side_effect=[
            {"pending": 0, "active": 1, "last_task": None},   # stall query
            {"recent_fails": 0, "recent_total": 0},           # failure-rate query
        ])
        # Every execute (the stamp is the only one reached here) blows up.
        pool.execute = AsyncMock(side_effect=RuntimeError("stamp exploded"))

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            await bd.auto_remediate(pool)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        combined = " ".join(r.getMessage() for r in warnings)
        assert "stamp" in combined.lower(), (
            f"stamp failure must be visible at WARNING; got: {combined}"
        )
        assert "stamp exploded" in combined

        # The stamp failure must be isolated — auto_remediate must NOT have
        # fallen through to its wholesale ERROR handler.
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert not any("Auto-remediation failed" in r.getMessage() for r in errors), (
            "stamp failure should be caught locally, not abort the whole sweep"
        )
