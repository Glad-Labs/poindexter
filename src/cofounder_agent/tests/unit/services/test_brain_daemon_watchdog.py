"""Unit tests for brain watchdog self-protection helpers (audit C2 + H1).

The brain daemon is the watchdog that pages the operator when services break.
Two audit findings: (C2) a DB blip in run_cycle could silently abort outage
detection with no operator page; (H1) the alert_dispatch_loop task could die
while the daemon still looks healthy, going dark on Grafana-sourced pages.

These helpers are the testable core of the fixes:

- ``_should_page_cycle_failure`` — when consecutive run_cycle failures cross the
  threshold, page the operator via the DB-independent failsafe.
- ``_alert_dispatch_died`` — detect that the dispatch loop task exited
  unexpectedly (vs cleanly cancelled at shutdown) so the main loop can restart
  it and page.
- ``_page_operator_failsafe`` — page through the stdlib operator_notifier (env-
  based, works when the DB pool is dead).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# brain/ is a standalone package outside the poindexter distro — add it to
# sys.path before importing (mirrors test_brain_daemon_auto_remediate.py).
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


@pytest.mark.unit
class TestShouldPageCycleFailure:
    def test_below_threshold_does_not_page(self):
        assert bd._should_page_cycle_failure(1) is False
        assert bd._should_page_cycle_failure(2) is False

    def test_pages_on_crossing_threshold(self):
        assert bd._should_page_cycle_failure(bd.CYCLE_FAILURE_ALERT_THRESHOLD) is True

    def test_does_not_spam_every_cycle_after_threshold(self):
        t = bd.CYCLE_FAILURE_ALERT_THRESHOLD
        # Pages on t, then quiet until the next multiple of t (re-ping a
        # persistent outage without paging every single cycle).
        assert bd._should_page_cycle_failure(t + 1) is False
        assert bd._should_page_cycle_failure(2 * t) is True

    def test_zero_is_quiet(self):
        assert bd._should_page_cycle_failure(0) is False


class _FakeTask:
    """Stand-in for an asyncio.Task with controllable done()/exception()."""

    def __init__(self, *, done: bool, exc=None, cancelled: bool = False):
        self._done = done
        self._exc = exc
        self._cancelled = cancelled

    def done(self) -> bool:
        return self._done

    def exception(self):
        if self._cancelled:
            raise asyncio.CancelledError()
        return self._exc


@pytest.mark.unit
class TestAlertDispatchDied:
    def test_none_task_is_not_dead(self):
        assert bd._alert_dispatch_died(None) == (False, None)

    def test_running_task_is_not_dead(self):
        died, exc = bd._alert_dispatch_died(_FakeTask(done=False))
        assert died is False and exc is None

    def test_task_that_raised_is_dead_with_exc(self):
        boom = RuntimeError("pool closed")
        died, exc = bd._alert_dispatch_died(_FakeTask(done=True, exc=boom))
        assert died is True and exc is boom

    def test_task_that_returned_unexpectedly_is_dead(self):
        """The dispatch loop should run until shutdown; a clean exit while the
        daemon runs is still a death that must restart it."""
        died, exc = bd._alert_dispatch_died(_FakeTask(done=True, exc=None))
        assert died is True and exc is None

    def test_cancelled_task_is_not_dead(self):
        """Cancelled = graceful shutdown, not a failure."""
        died, exc = bd._alert_dispatch_died(_FakeTask(done=True, cancelled=True))
        assert died is False and exc is None


@pytest.mark.unit
class TestPageOperatorFailsafe:
    def test_invokes_notify_operator_with_kwargs(self, monkeypatch):
        mock = MagicMock(return_value={"telegram": "sent"})
        monkeypatch.setattr("operator_notifier.notify_operator", mock)
        ok = bd._page_operator_failsafe(
            title="t", detail="d", source="brain:test", severity="critical"
        )
        assert ok is True
        mock.assert_called_once()
        kwargs = mock.call_args.kwargs
        assert kwargs["title"] == "t"
        assert kwargs["source"] == "brain:test"
        assert kwargs["severity"] == "critical"

    def test_swallows_notify_failure(self, monkeypatch):
        """The failsafe must never raise — a failed page can't crash the loop."""
        mock = MagicMock(side_effect=RuntimeError("network down"))
        monkeypatch.setattr("operator_notifier.notify_operator", mock)
        assert bd._page_operator_failsafe(title="t", detail="d", source="s") is False


@pytest.mark.unit
class TestInitSentry:
    """Brain daemon Sentry/GlitchTip init (audit H5). The self-healing watchdog
    + alert dispatcher previously had no Sentry SDK — its crashes reached no
    error tracker."""

    def _patch_read(self, monkeypatch, dsn):
        async def fake_read(pool, key, default=""):
            return dsn if key == "sentry_dsn" else (default or "production")

        monkeypatch.setattr(bd, "_read_app_setting", fake_read)

    async def test_skips_when_dsn_empty(self, monkeypatch):
        self._patch_read(monkeypatch, "")
        assert await bd._init_sentry(MagicMock()) is False

    async def test_graceful_when_sdk_missing(self, monkeypatch):
        """sentry-sdk absent from the image → degrade cleanly, don't crash boot."""
        self._patch_read(monkeypatch, "https://k@glitchtip.local/1")
        monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # forces ImportError
        assert await bd._init_sentry(MagicMock()) is False

    async def test_initialises_when_dsn_set(self, monkeypatch):
        self._patch_read(monkeypatch, "https://k@glitchtip.local/1")
        fake_sentry = MagicMock()
        integ_asyncio = MagicMock()
        integ_asyncio.AsyncioIntegration = MagicMock()
        integ_logging = MagicMock()
        integ_logging.LoggingIntegration = MagicMock()
        monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sentry)
        monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", MagicMock())
        monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.asyncio", integ_asyncio)
        monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.logging", integ_logging)

        ok = await bd._init_sentry(MagicMock())
        assert ok is True
        fake_sentry.init.assert_called_once()
        assert fake_sentry.init.call_args.kwargs["dsn"] == "https://k@glitchtip.local/1"
