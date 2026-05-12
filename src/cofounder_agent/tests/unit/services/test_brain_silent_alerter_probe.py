"""Unit tests for ``brain/business_probes.probe_silent_alerter``.

Task #45 — meta-watchdog that pages when the alerter pipeline itself
appears silently dead. Matt's 2026-05-12 05:25 UTC directive: "If we
find silent failures we should add at least a way to make it fail
loud, ideally make it self healing."

Three classes of behaviour pinned:

1. **Suppression** — disabled / not due / never-received-an-alert paths
   return ok without paging.
2. **Quiet-but-healthy** — alerts are stale but every brain probe
   passed in the last hour. We do NOT page (false-positive avoidance);
   the system is genuinely idle.
3. **Quiet-and-red** — alerts are stale AND ≥1 brain probe failed
   recently. This is the silent-failure shape the watchdog exists to
   catch. We MUST page exactly once per cycle.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain import business_probes as bp


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    settings: dict[str, str] | None = None,
    last_alert_received: datetime | None = None,
    probe_failures: list[str] | None = None,
) -> Any:
    """Async pool stub serving:

    1. ``SELECT value FROM app_settings WHERE key = $1`` (interval, etc.)
    2. ``SELECT MAX(received_at) FROM alert_events``
    3. ``SELECT DISTINCT event_type FROM audit_log WHERE … probe.%``
    """
    settings = settings or {}
    probe_failures = probe_failures or []

    async def _fetchval(query: str, *args: Any) -> Any:
        if query.startswith("SELECT value FROM app_settings"):
            return settings.get(args[0])
        if "MAX(received_at)" in query:
            return last_alert_received
        return None

    async def _fetch(query: str, *args: Any) -> Any:  # noqa: ARG001
        if "probe." in query:
            return [{"event_type": name} for name in probe_failures]
        return []

    pool = MagicMock()
    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetch = AsyncMock(side_effect=_fetch)
    return pool


@pytest.fixture(autouse=True)
def _reset_probe_state():
    bp._last_run.clear()
    yield
    bp._last_run.clear()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeSilentAlerterSuppression:
    async def test_disabled_via_app_settings(self):
        pool = _make_pool(settings={"silent_alerter_probe_enabled": "false"})
        notify = AsyncMock()
        result = await bp.probe_silent_alerter(pool, notify)
        assert result["ok"] is True
        assert "disabled" in result["detail"]
        notify.assert_not_called()

    async def test_not_due_returns_early(self):
        pool = _make_pool(
            settings={"silent_alerter_probe_interval_minutes": "1440"},
            last_alert_received=datetime.now(timezone.utc),
            probe_failures=["probe.compose_drift_detected"],
        )
        notify = AsyncMock()
        first = await bp.probe_silent_alerter(pool, notify)
        assert first["ok"] is True

        second = await bp.probe_silent_alerter(pool, notify)
        assert second["ok"] is True
        assert "not due" in second["detail"]


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeSilentAlerterQuietButHealthy:
    async def test_recent_alert_no_page(self):
        """Alert received within threshold — system is alerting fine."""
        recent = datetime.now(timezone.utc) - timedelta(hours=1)
        pool = _make_pool(
            last_alert_received=recent,
            probe_failures=["probe.compose_drift_detected"],
        )
        notify = AsyncMock()
        result = await bp.probe_silent_alerter(pool, notify)
        assert result["ok"] is True
        assert "recent alert" in result["detail"]
        notify.assert_not_called()

    async def test_quiet_but_no_probe_failures_no_page(self):
        """Long quiet but every probe passed — system is genuinely
        idle. The watchdog must NOT page in this case (false-positive
        avoidance — Matt installed it to catch silent failures, not to
        cry wolf during weekends).
        """
        long_ago = datetime.now(timezone.utc) - timedelta(hours=24)
        pool = _make_pool(
            last_alert_received=long_ago,
            probe_failures=[],
        )
        notify = AsyncMock()
        result = await bp.probe_silent_alerter(pool, notify)
        assert result["ok"] is True
        assert "no probe failures" in result["detail"]
        notify.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
class TestProbeSilentAlerterQuietAndRed:
    async def test_quiet_and_red_fires_one_page(self):
        """The load-bearing case: alerts are stale AND ≥1 probe is red.
        This is the silent-failure shape — the alerter itself is gone
        but the rest of the system knows things are broken. We MUST
        page so the operator finds out within one probe cycle.
        """
        long_ago = datetime.now(timezone.utc) - timedelta(hours=12)
        pool = _make_pool(
            last_alert_received=long_ago,
            probe_failures=[
                "probe.compose_drift_detected",
                "probe.migration_drift_unknown",
            ],
        )
        notify = AsyncMock()
        result = await bp.probe_silent_alerter(pool, notify)

        assert result["ok"] is True
        assert "paged" in result["detail"]
        assert result["probe_failure_count"] == 2
        assert result["quiet_hours"] > 6
        # Critical contract: exactly one notify call AND it was awaited.
        # await_count vs call_count catches the silent-failure regression
        # the broader fix in this branch exists to prevent.
        assert notify.await_count == 1
        body = notify.call_args.args[0]
        assert "ALERTER APPEARS SILENT" in body
        assert "probe.compose_drift_detected" in body
        assert "probe.migration_drift_unknown" in body

    async def test_never_received_an_alert_with_failures_pages(self):
        """``MAX(received_at)`` is NULL on a fresh install. If probes
        are also failing, that's still a silent-alerter case worth
        paging — the threshold-actual collapses to "effectively
        infinite" so any failure crosses it.
        """
        pool = _make_pool(
            last_alert_received=None,
            probe_failures=["probe.compose_drift_detected"],
        )
        notify = AsyncMock()
        result = await bp.probe_silent_alerter(pool, notify)
        assert result["ok"] is True
        assert "paged" in result["detail"]
        assert notify.await_count == 1

    async def test_custom_threshold_honored(self):
        """Operator-tightened threshold pages sooner."""
        # 4h ago, threshold 1h → page.
        recent_ish = datetime.now(timezone.utc) - timedelta(hours=4)
        pool = _make_pool(
            settings={"silent_alerter_quiet_hours": "1"},
            last_alert_received=recent_ish,
            probe_failures=["probe.compose_drift_detected"],
        )
        notify = AsyncMock()
        result = await bp.probe_silent_alerter(pool, notify)
        assert "paged" in result["detail"]
        assert notify.await_count == 1
