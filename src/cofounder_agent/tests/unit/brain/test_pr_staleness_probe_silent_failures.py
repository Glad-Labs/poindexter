"""Unit tests — pr_staleness_probe silent-failure surfacing.

Gap-site burn-down (best-effort-failure-visibility, batch 2).
``_is_pr_deduped`` reads ``alert_dedup_state`` to decide whether to
suppress a repeat PR-staleness alert. A swallowed read used to log at
``logger.debug`` and return ``False`` (not deduped) — so a broken read
silently bypassed dedup and re-fired the alert, invisibly. It now WARNs
(the brain tree can't ``emit_finding``, so ``warning`` is the bar).

Silent-excepts OLD-debt burn-down (batch 24) adds the second class below:
when the GitHub call fails, the probe notifies the operator that the probe
ITSELF is broken. That ``notify_fn`` call was wrapped in
``except Exception: pass``.

Scope it honestly: the probe failure is NOT invisible — the line above the
notify already does ``logger.warning("[PR_STALENESS] GitHub round-trip
failed: ...")``. What was lost is narrower but still real: the operator
expects to be *paged*, and a dead notifier means the page never arrives with
nothing saying so. Since this system is run from a phone via Telegram/Discord
alerts, silence reads as "no stale PRs"; the Loki line only helps someone who
already went looking. So the delivery failure gets its own WARNING naming the
notifier error.

(Not the audit_log circularity case — a failing notifier and a failing logger
are independent, so ``logger.warning`` genuinely survives here.)

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


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeFailureNotifyFailureVisible:
    """If the probe-failure notification itself raises, the DELIVERY failure
    must WARN. The probe failure itself is already logged; what was silent is
    that the operator's page never went out."""

    async def test_failed_probe_notification_is_visible(self, caplog, monkeypatch):
        # Stub the *setting reader* and let the real _read_config assemble the
        # dict, so this test can't drift out of sync with the config shape.
        async def _setting(_pool, _key, default=""):
            return default

        monkeypatch.setattr(psp, "_read_setting", _setting)
        monkeypatch.setattr(psp, "_read_token", AsyncMock(return_value="ghp_test"))
        # Force the GitHub path to fail so the probe takes its notify-on-failure branch.
        monkeypatch.setattr(
            psp, "_fetch_open_prs",
            AsyncMock(side_effect=RuntimeError("github unreachable")),
        )

        def _exploding_notify(**_kwargs):
            raise RuntimeError("telegram transport down")

        pool = MagicMock()
        pool.execute = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)
        pool.fetch = AsyncMock(return_value=[])

        with caplog.at_level(logging.WARNING, logger=_LOGGER):
            result = await psp.run_pr_staleness_probe(pool, notify_fn=_exploding_notify)

        # Best-effort semantics preserved: a dead notifier must not crash the cycle.
        assert result["ok"] is False
        assert result["status"] == "github_error"

        combined = " ".join(
            r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING
        )
        assert "telegram transport down" in combined, (
            "a failed probe-failure notification must be visible at WARNING — "
            "otherwise the operator never learns the probe stopped working"
        )
