"""Unit tests — pr_staleness_probe silent-failure surfacing.

Gap-site burn-down (best-effort-failure-visibility, batch 2).
``_is_pr_deduped`` reads ``alert_dedup_state`` to decide whether to
suppress a repeat PR-staleness alert. A swallowed read used to log at
``logger.debug`` and return ``False`` (not deduped) — so a broken read
silently bypassed dedup and re-fired the alert, invisibly. It now WARNs
(the brain tree can't ``emit_finding``, so ``warning`` is the bar).

Mirrors the caplog assertion pattern in
``test_alert_dispatcher_silent_failures.py``.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
_REPO_ROOT = next(
    p for p in Path(__file__).resolve().parents
    if (p / "pyproject.toml").exists() and (p / "src").exists()
)
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import pr_staleness_probe as psp  # noqa: E402

_LOGGER = "brain.pr_staleness_probe"


@pytest.mark.unit
@pytest.mark.asyncio
class TestPrDedupLookupFailureVisible:
    """A failed dedup lookup must WARN — it fails open (alert re-fires),
    so a broken read is otherwise indistinguishable from a real repeat."""

    async def test_is_pr_deduped_logs_warning_on_db_failure(self, caplog):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=RuntimeError("dedup read exploded"))

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            result = await psp._is_pr_deduped(
                pool,
                fingerprint="pr_stale_42",
                now_utc=datetime.now(timezone.utc),
                dedup_hours=12,
            )

        # Fail-open sentinel — the alert is allowed to fire.
        assert result is False
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "PR dedup lookup failure must be visible at WARNING"
        assert "dedup read exploded" in " ".join(r.getMessage() for r in warnings)
