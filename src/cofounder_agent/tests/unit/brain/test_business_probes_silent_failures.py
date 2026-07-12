"""Unit tests — business_probes silent-failure surfacing.

Gap-site burn-down (best-effort-failure-visibility, batch 2).
``_row_age_days`` reads the newest-row timestamp for a table to drive the
business freshness probes. A swallowed read used to log at ``logger.debug``
(invisible at prod level) and return ``None`` — indistinguishable from a
genuinely-missing timestamp, so a broken read silently SKIPPED the
freshness check for that table with no operator signal. It now WARNs
(the brain tree can't ``emit_finding`` — no ``services`` import — so
``warning`` is the visibility bar).

Mirrors the caplog assertion pattern in
``test_alert_dispatcher_silent_failures.py``.
"""

from __future__ import annotations

import logging
import sys
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

from brain import business_probes as bp  # noqa: E402

_LOGGER = "brain.business_probes"


@pytest.mark.unit
@pytest.mark.asyncio
class TestRowAgeDaysFailureVisible:
    """A failed newest-row read must WARN — otherwise the freshness probe
    silently skips the table and the operator can't tell a broken read
    from a genuinely-fresh table."""

    async def test_row_age_days_logs_warning_on_db_failure(self, caplog):
        pool = MagicMock()
        pool.fetchval = AsyncMock(side_effect=RuntimeError("max(created_at) exploded"))

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            result = await bp._row_age_days(pool, "posts")

        # Sentinel preserved — probe skips this table, never crashes.
        assert result is None
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings, "row-age read failure must be visible at WARNING"
        combined = " ".join(r.getMessage() for r in warnings)
        assert "_row_age_days" in combined
        assert "max(created_at) exploded" in combined
