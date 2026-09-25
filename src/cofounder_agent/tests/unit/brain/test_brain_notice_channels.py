"""Which channel each of the brain's own notices reaches (2026-09-25).

``brain_daemon.notify`` pages Telegram AND Discord and takes no severity,
so every notice routed through it reached the operator's phone. That
included self-heal successes, recoveries, and a "pipeline idle" line
repeated every 5-minute cycle (41 Telegram pages in the 30 days to
2026-09-25). The rule is Telegram = critical/error only
(docs/operations/self-healing.md, "Which brain notices page"). Success,
recovery, info and warning notices go through ``notify_discord_ops`` to
Discord #ops.

These tests stub only the two leaf senders, ``send_telegram`` and
``send_discord``, and run everything above them for real. So they show
where a notice LANDS, not which helper a call site named. The difference
is the bug: ``send_discord(msg, pool=pool)`` looked Discord-bound in
review, but with no webhook it resolves the lab-logs channel, which prod
does not configure, and every "degraded" notice was dropped.
"""

from __future__ import annotations

import ast
import inspect
import logging
import sys
import time
import urllib.error
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.brain import alert_sync as asx
from poindexter.brain import brain_daemon as bd
from poindexter.brain import health_probes as hp

OPS_URL = "https://discord.test/ops-webhook"
PAGE = ["discord-ops", "telegram"]
NOTICE = ["discord-ops"]


class _Channels:
    """The brain's two leaf senders, recording which channel each message reached."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.references: list[Any] = []
        self.discord_accepts = True

    async def send_telegram(self, message, *, pool=None, reply_to_message_id=None):
        self.sent.append(("telegram", message))
        return 1

    async def send_discord(
        self,
        message,
        webhook_url=None,
        *,
        pool=None,
        message_reference_id=None,
    ):
        if webhook_url == OPS_URL:
            channel = "discord-ops"
        else:
            channel = f"discord:{webhook_url or 'lab-logs'}"
        self.sent.append((channel, message))
        self.references.append(message_reference_id)
        return "1" if self.discord_accepts else None

    def reached(self, needle: str) -> list[str]:
        """The channels a message containing ``needle`` reached, sorted."""
        return sorted(channel for channel, message in self.sent if needle in message)

    def count(self, needle: str) -> int:
        return sum(1 for _channel, message in self.sent if needle in message)


class _Pool:
    """A pool whose ``app_settings`` table is empty, so the #ops webhook
    resolves from the env fallback and every ``_setting_int`` takes its
    default. Other reads answer from ``rows``, keyed by a SQL fragment."""

    def __init__(self, rows: dict[str, Any] | None = None) -> None:
        self.rows = rows or {}
        self.executed: list[str] = []

    def _answer(self, sql: str, default: Any) -> Any:
        for fragment, value in self.rows.items():
            if fragment in sql:
                return value() if callable(value) else value
        return default

    async def fetch(self, sql, *args):
        return self._answer(sql, [])

    async def fetchrow(self, sql, *args):
        return self._answer(sql, None)

    async def fetchval(self, sql, *args):
        return None

    async def execute(self, sql, *args):
        self.executed.append(sql)
        return "OK"


def _reset_brain_state() -> None:
    bd._consecutive_down.clear()
    bd._degraded_since.clear()
    bd._prev_external_status.clear()
    bd._external_outage_paged.clear()
    bd._pipeline_states_announced.clear()
    hp._failure_counts.clear()
    hp._last_run.clear()
    hp._last_remediation.clear()


@pytest.fixture
def channels(monkeypatch) -> Iterator[_Channels]:
    recorder = _Channels()
    monkeypatch.setattr(bd, "send_telegram", recorder.send_telegram)
    monkeypatch.setattr(bd, "send_discord", recorder.send_discord)
    monkeypatch.setenv("DISCORD_OPS_WEBHOOK_URL", OPS_URL)
    # A pool registered by another test would answer the webhook lookup.
    monkeypatch.delitem(sys.modules, bd._POOL_REGISTRY_KEY, raising=False)
    # A settled process: boot grace off, and no openclaw doctor subprocess.
    monkeypatch.setattr(bd, "_DAEMON_STARTED_AT", time.monotonic() - 10_000)
    monkeypatch.setattr(bd, "_last_openclaw_doctor", time.time())
    _reset_brain_state()
    yield recorder
    _reset_brain_state()


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestNotifyDiscordOps:
    async def test_reaches_discord_ops_and_never_telegram(self, channels):
        result = await bd.notify_discord_ops("hello ops", pool=None)

        assert channels.reached("hello ops") == NOTICE
        assert result == {
            "telegram_message_id": None,
            "discord_message_id": "1",
            "ok": True,
        }

    async def test_ops_webhook_comes_from_app_settings_before_the_env(self, channels):
        pool = _Pool(
            {"app_settings": {"value": "https://discord.test/from-db", "is_secret": False}}
        )

        await bd.notify_discord_ops("from db", pool=pool)

        assert channels.reached("from db") == ["discord:https://discord.test/from-db"]

    async def test_falls_back_to_lab_logs_exactly_like_notify(self, channels, monkeypatch):
        """No #ops webhook: the notice and the page's Discord half take the
        same fallback, because they share one resolver."""
        monkeypatch.delenv("DISCORD_OPS_WEBHOOK_URL")

        await bd.notify_discord_ops("notice without ops", pool=None)
        await bd.notify("page without ops", pool=None)

        assert channels.reached("notice without ops") == ["discord:lab-logs"]
        assert channels.reached("page without ops") == ["discord:lab-logs", "telegram"]

    async def test_a_notice_nobody_received_is_a_warning(self, channels, caplog):
        channels.discord_accepts = False

        with caplog.at_level(logging.WARNING, logger="brain"):
            result = await bd.notify_discord_ops("lost notice", pool=None)

        assert result["ok"] is False
        assert any(
            "not delivered" in r.getMessage() and "lost notice" in r.getMessage()
            for r in caplog.records
        )

    async def test_notify_still_pages_both_channels(self, channels):
        result = await bd.notify("a real page", pool=None)

        assert channels.reached("a real page") == PAGE
        assert result["ok"] is True

    async def test_followup_threads_under_the_ops_channel(self, channels):
        await bd.send_followup(
            "[triage] diagnosis",
            parent_discord_message_id="555",
            pool=None,
        )

        assert channels.reached("diagnosis") == NOTICE
        assert channels.references == ["555"]


# ---------------------------------------------------------------------------
# restart_service — a heal that worked is a notice; one that failed pages
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
class TestRestartService:
    async def test_successful_restart_is_a_notice(self, channels, monkeypatch):
        monkeypatch.setattr(bd, "IS_DOCKER", True)
        run = MagicMock(side_effect=[_completed(0, "running\n"), _completed(0)])
        monkeypatch.setattr(bd.subprocess, "run", run)

        await bd.restart_service("worker", pool=None)

        assert channels.reached("Auto-restarted poindexter-worker") == NOTICE

    @pytest.mark.parametrize(
        ("name", "run_effect", "needle"),
        [
            (
                "worker",
                [_completed(0, "running\n"), _completed(1, stderr="permission denied")],
                "Failed to restart poindexter-worker",
            ),
            ("worker", FileNotFoundError("docker"), "Docker CLI not found"),
            ("worker", RuntimeError("socket gone"), "Restart failed: socket gone"),
            ("redis", None, "no container mapping"),
        ],
    )
    async def test_a_heal_that_failed_still_pages(
        self,
        channels,
        monkeypatch,
        name,
        run_effect,
        needle,
    ):
        monkeypatch.setattr(bd, "IS_DOCKER", True)
        monkeypatch.setattr(bd.subprocess, "run", MagicMock(side_effect=run_effect))

        await bd.restart_service(name, pool=None)

        assert channels.reached(needle) == PAGE


# ---------------------------------------------------------------------------
# monitor_services — degraded is a notice to #ops; a critical DOWN pages
# ---------------------------------------------------------------------------


def _services(monkeypatch, results: dict[str, tuple], critical: dict[str, bool]) -> None:
    monkeypatch.setattr(
        bd,
        "SERVICES",
        {
            name: {
                "url": f"http://{name}/api/health",
                "type": "json_status",
                "critical": critical.get(name, False),
            }
            for name in results
        },
    )
    monkeypatch.setattr(
        bd,
        "check_json_status",
        lambda url: results[url.split("//")[1].split("/")[0]],
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestMonitorServices:
    async def test_degraded_and_its_recovery_reach_ops_not_lab_logs(self, channels, monkeypatch):
        pool = _Pool()
        _services(monkeypatch, {"worker": (True, 503, "degraded")}, {})
        await bd.monitor_services(pool)
        _services(monkeypatch, {"worker": (True, 200, "healthy")}, {})
        await bd.monitor_services(pool)

        assert channels.reached("DEGRADED") == NOTICE
        assert channels.reached("recovered from degraded") == NOTICE

    async def test_a_critical_service_down_pages(self, channels, monkeypatch):
        _services(monkeypatch, {"api": (False, 0, "timed out")}, {"api": True})

        await bd.monitor_services(_Pool())

        assert channels.reached("api is DOWN") == PAGE


# ---------------------------------------------------------------------------
# monitor_external_services — a MAJOR outage pages; the all-clear is a notice
# ---------------------------------------------------------------------------


_STATUS = {
    "none": (True, "none", "All Systems Operational"),
    "minor": (False, "minor", "Partially Degraded Service"),
    "major": (False, "major", "Partial System Outage"),
}


async def _poll_status_page(monkeypatch, sequence: list[str]) -> None:
    monkeypatch.setattr(
        bd,
        "EXTERNAL_SERVICES",
        {
            "github": {"url": "https://status.test/api/v2/status.json", "type": "statuspage"},
        },
    )
    pool = _Pool()
    for state in sequence:
        monkeypatch.setattr(bd, "check_statuspage", lambda _url, s=state: _STATUS[s])
        await bd.monitor_external_services(pool)


@pytest.mark.unit
@pytest.mark.asyncio
class TestMonitorExternalServices:
    async def test_all_clear_after_the_page_steps_down_through_minor(self, channels, monkeypatch):
        """The prod shape: githubstatus went major -> minor -> none on
        2026-08-27 and 2026-09-13. The old test ("the last poll said major")
        never saw major -> none, so neither page got its all-clear."""
        await _poll_status_page(monkeypatch, ["none", "major", "minor", "none", "none"])

        assert channels.reached("GITHUB MAJOR OUTAGE") == PAGE
        assert channels.reached("GITHUB recovered") == NOTICE

    async def test_straight_back_to_operational_also_sends_the_all_clear(
        self, channels, monkeypatch
    ):
        await _poll_status_page(monkeypatch, ["major", "none"])

        assert channels.reached("GITHUB recovered") == NOTICE

    async def test_no_all_clear_for_an_outage_that_never_paged(self, channels, monkeypatch):
        await _poll_status_page(monkeypatch, ["none", "minor", "none"])

        assert channels.sent == []


# ---------------------------------------------------------------------------
# auto_remediate — notices, and a state is announced once per episode
# ---------------------------------------------------------------------------


def _pipeline_pool(
    *,
    idle_hours: float | None = None,
    failures: tuple[int, int] = (0, 0),
    stuck: list[dict] | None = None,
    expired: list[dict] | None = None,
) -> _Pool:
    last_task = datetime.now(UTC) - timedelta(hours=idle_hours or 1)
    active = 0 if idle_hours else 1
    return _Pool(
        {
            "Auto-cancelled: stuck": stuck or [],
            "Auto-rejected: awaiting_approval": expired or [],
            "MAX(created_at)": {"pending": 0, "active": active, "last_task": last_task},
            "recent_fails": {"recent_fails": failures[0], "recent_total": failures[1]},
        }
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestAutoRemediate:
    async def test_cancelled_stuck_tasks_are_a_notice(self, channels):
        await bd.auto_remediate(
            _pipeline_pool(
                stuck=[{"task_id": "t-1", "topic": "A stuck topic"}],
            )
        )

        assert channels.reached("cancelled 1 stuck task(s)") == NOTICE

    async def test_an_idle_pipeline_is_announced_once_per_episode(self, channels):
        for _ in range(3):
            await bd.auto_remediate(_pipeline_pool(idle_hours=49))
        assert channels.count("pipeline idle") == 1
        assert channels.reached("pipeline idle") == NOTICE

        await bd.auto_remediate(_pipeline_pool())  # a task ran: the episode ends
        await bd.auto_remediate(_pipeline_pool(idle_hours=50))
        assert channels.count("pipeline idle") == 2

    async def test_a_high_failure_rate_is_announced_once_per_episode(self, channels):
        for _ in range(3):
            await bd.auto_remediate(_pipeline_pool(failures=(4, 5)))

        assert channels.count("high failure rate: 4/5") == 1
        assert channels.reached("high failure rate") == NOTICE

    async def test_an_undelivered_state_notice_is_retried(self, channels):
        channels.discord_accepts = False
        await bd.auto_remediate(_pipeline_pool(idle_hours=49))
        channels.discord_accepts = True
        await bd.auto_remediate(_pipeline_pool(idle_hours=49))
        await bd.auto_remediate(_pipeline_pool(idle_hours=49))

        assert channels.count("pipeline idle") == 2  # the lost one, then one delivery

    async def test_a_stale_approval_whose_topic_says_idle_is_not_posted(self, channels):
        """The old filter matched words in the action text, which embeds task
        topics, so this auto-reject went out as an 'idle' alert."""
        await bd.auto_remediate(
            _pipeline_pool(
                expired=[{"task_id": "t-2", "topic": "Why idle GPUs still draw power"}],
            )
        )

        assert channels.sent == []


# ---------------------------------------------------------------------------
# PSU watchdog — info notes to #ops (not lab-logs); critical pages
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_psu_watchdog_info_is_an_ops_notice_and_critical_pages(channels, monkeypatch):
    def _exporter_down(*_a, **_k):
        raise urllib.error.URLError("exporter down")

    monkeypatch.setattr(bd.urllib.request, "urlopen", _exporter_down)
    monkeypatch.setattr(bd, "fetch_icue_psu_watts", AsyncMock(return_value=None))
    monkeypatch.setattr(
        bd,
        "psu_watchdog_transition",
        lambda *a, **k: (
            [
                {"severity": "info", "message": "↗️ PSU partial recovery"},
                {"severity": "critical", "message": "🚨 No metered PSU power"},
            ],
            0,
        ),
    )

    await bd.log_electricity_cost(_Pool({"pipeline_tasks_view": {"c": 0}}))

    assert channels.reached("PSU partial recovery") == NOTICE
    assert channels.reached("No metered PSU power") == PAGE


# ---------------------------------------------------------------------------
# Health probes — recovery and a heal that worked are notices
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_run_cycle_hands_the_probes_notify_for_pages_and_the_notice_sender_for_info():
    """The probes can only keep recoveries off Telegram if run_cycle passes
    them a sender that doesn't page."""
    tree = ast.parse(inspect.getsource(bd.run_cycle))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "run_health_probes"
    ]
    assert len(calls) == 1
    keywords = {kw.arg: getattr(kw.value, "id", None) for kw in calls[0].keywords}
    assert keywords == {"notify_fn": "notify", "info_fn": "notify_discord_ops"}


@pytest.mark.unit
@pytest.mark.asyncio
class TestHealthProbeNotices:
    """``run_health_probes`` wired as ``run_cycle`` wires it."""

    async def _cycle(self, probe_result: dict, *, heal: tuple[bool, str]) -> None:
        async def probe(_pool):
            return probe_result

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])
        pool.fetchrow = AsyncMock(return_value=None)
        pool.fetchval = AsyncMock(return_value=None)
        pool.execute = AsyncMock()
        with (
            patch.dict(hp.PROBES, {"grafana_datasources": probe}, clear=True),
            patch.object(hp, "_is_due", return_value=True),
            patch.object(hp, "ALERT_AFTER_FAILURES", 1),
            patch.object(hp, "_alertmanager_healthy", new=AsyncMock(return_value=False)),
            patch.object(hp, "_restart_container", return_value=heal),
        ):
            await hp.run_health_probes(pool, notify_fn=bd.notify, info_fn=bd.notify_discord_ops)

    async def test_failure_pages_the_heal_and_the_recovery_are_notices(self, channels):
        await self._cycle(
            {"ok": False, "detail": "Pyroscope: HTTP Error 400"},
            heal=(True, "Restarted poindexter-grafana"),
        )
        await self._cycle({"ok": True, "detail": "all datasources healthy"}, heal=(True, ""))

        assert channels.reached("Probe 'grafana_datasources' failed") == PAGE
        assert channels.reached("Self-heal 'grafana_datasources'") == NOTICE
        assert channels.reached("Probe 'grafana_datasources' recovered") == NOTICE

    async def test_a_heal_that_failed_pages(self, channels):
        await self._cycle(
            {"ok": False, "detail": "Pyroscope: HTTP Error 400"},
            heal=(False, "docker restart failed"),
        )

        assert channels.reached("Self-heal 'grafana_datasources': docker restart failed") == PAGE


# ---------------------------------------------------------------------------
# alert_sync — an empty Grafana token is a config gap, not an outage
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_grafana_token_alarm_is_an_ops_notice(channels):
    await asx._fire_empty_token_alarm(_Pool(), 4)

    assert channels.reached("grafana_api_token has been empty") == NOTICE
