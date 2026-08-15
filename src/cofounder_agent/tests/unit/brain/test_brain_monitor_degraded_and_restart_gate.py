"""Unit tests — brain monitor failure classification + restart gating.

2026-08-15 api-down investigation. The brain was restarting a *busy*
worker: ``/api/health`` 503s while "degraded" (so load balancers can act
on the code), ``urlopen`` raises ``HTTPError`` for that, and the old
``check_json_status`` never read the JSON body — a pool-pressure blip
therefore read as DOWN and ``monitor_services`` docker-restarted the
worker on the first failed probe, cancelling in-flight pipeline work.

Pins the two fixes:

* ``check_json_status`` parses HTTPError bodies — a JSON body whose
  ``status`` is "degraded" (or "healthy"/"ok") is UP regardless of the
  HTTP status code; degraded surfaces as a notice, never a restart.
* ``monitor_services`` defers auto-restart until
  ``app_settings.brain_restart_consecutive_failures`` consecutive
  hard-down cycles (default 2), while the critical-alert path still
  fires from the first failed cycle.
"""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# brain/ is a standalone package outside the cofounder_agent distro.
# Mirror the path-prelude pattern from test_brain_daemon_silent_failures.py.
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


def _http_error(code: int, body: bytes, reason: str = "Service Unavailable"):
    return urllib.error.HTTPError(
        "http://worker:8002/api/health", code, reason, hdrs=None, fp=io.BytesIO(body)
    )


class TestCheckJsonStatus:
    def test_200_healthy_is_ok(self, monkeypatch):
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = json.dumps({"status": "healthy"}).encode()
        monkeypatch.setattr(bd.urllib.request, "urlopen", lambda *a, **k: resp)
        assert bd.check_json_status("http://x/api/health") == (True, 200, "healthy")

    def test_503_with_degraded_body_is_up(self, monkeypatch):
        """The incident case: 503 + {"status": "degraded"} must NOT read as down."""
        def _raise(*a, **k):
            raise _http_error(503, json.dumps({"status": "degraded"}).encode())
        monkeypatch.setattr(bd.urllib.request, "urlopen", _raise)
        assert bd.check_json_status("http://x/api/health") == (True, 503, "degraded")

    def test_503_with_unhealthy_body_is_down(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(503, json.dumps({"status": "unhealthy"}).encode())
        monkeypatch.setattr(bd.urllib.request, "urlopen", _raise)
        ok, code, detail = bd.check_json_status("http://x/api/health")
        assert (ok, code) == (False, 503)
        assert "unhealthy" in detail

    def test_503_with_non_json_body_is_down(self, monkeypatch):
        def _raise(*a, **k):
            raise _http_error(503, b"<html>proxy error</html>")
        monkeypatch.setattr(bd.urllib.request, "urlopen", _raise)
        ok, code, detail = bd.check_json_status("http://x/api/health")
        assert (ok, code) == (False, 503)
        assert detail.startswith("HTTP 503")

    def test_connection_refused_is_down_code_zero(self, monkeypatch):
        def _raise(*a, **k):
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        monkeypatch.setattr(bd.urllib.request, "urlopen", _raise)
        ok, code, _detail = bd.check_json_status("http://x/api/health")
        assert (ok, code) == (False, 0)


@pytest.fixture()
def monitor_env(monkeypatch):
    """Isolated monitor_services environment: controlled SERVICES, mocked
    side-effect surfaces, cleared per-service state."""
    bd._consecutive_down.clear()
    bd._degraded_since.clear()
    # Keep the periodic openclaw-doctor subprocess out of the loop.
    monkeypatch.setattr(bd, "_last_openclaw_doctor", time.time())

    pool = MagicMock()
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)  # no alert_actions row
    pool.fetchval = AsyncMock(return_value=None)  # _setting_int → defaults

    mocks = {
        "restart": AsyncMock(),
        "discord": AsyncMock(return_value="1"),
        "notify": AsyncMock(return_value={}),
        "pool": pool,
    }
    monkeypatch.setattr(bd, "restart_service", mocks["restart"])
    monkeypatch.setattr(bd, "send_discord", mocks["discord"])
    monkeypatch.setattr(bd, "notify", mocks["notify"])
    yield mocks
    bd._consecutive_down.clear()
    bd._degraded_since.clear()


def _set_services(monkeypatch, probe_results: dict[str, tuple], critical: dict[str, bool] | None = None):
    """Install a SERVICES dict + a check stub returning per-service tuples."""
    critical = critical or {}
    services = {
        name: {"url": f"http://{name}/api/health", "type": "json_status",
               "critical": critical.get(name, False)}
        for name in probe_results
    }
    monkeypatch.setattr(bd, "SERVICES", services)
    monkeypatch.setattr(
        bd, "check_json_status",
        lambda url: probe_results[url.split("//")[1].split("/")[0]],
    )


@pytest.mark.unit
@pytest.mark.asyncio
class TestMonitorServicesDegraded:
    async def test_degraded_never_restarts_and_notifies_discord_once(self, monkeypatch, monitor_env):
        _set_services(monkeypatch, {"worker": (True, 503, "degraded")})

        issues = await bd.monitor_services(monitor_env["pool"])

        monitor_env["restart"].assert_not_awaited()
        monitor_env["notify"].assert_not_awaited()  # no Telegram page
        assert issues and issues[0]["state"] == "degraded"
        assert monitor_env["discord"].await_count == 1
        assert "DEGRADED" in monitor_env["discord"].await_args_list[0].args[0]

        # Second degraded cycle: still surfaced, but no repeat notice.
        issues2 = await bd.monitor_services(monitor_env["pool"])
        assert issues2 and issues2[0]["state"] == "degraded"
        assert monitor_env["discord"].await_count == 1

    async def test_degraded_critical_service_does_not_page(self, monkeypatch, monitor_env):
        _set_services(monkeypatch, {"api": (True, 503, "degraded")}, critical={"api": True})

        await bd.monitor_services(monitor_env["pool"])

        monitor_env["notify"].assert_not_awaited()
        monitor_env["restart"].assert_not_awaited()
        assert monitor_env["discord"].await_count == 1

    async def test_degraded_writes_degraded_to_knowledge_graph(self, monkeypatch, monitor_env):
        _set_services(monkeypatch, {"worker": (True, 503, "degraded")})

        await bd.monitor_services(monitor_env["pool"])

        kg_calls = [c for c in monitor_env["pool"].execute.await_args_list
                    if "brain_knowledge" in c.args[0]]
        assert kg_calls and kg_calls[0].args[3] == "degraded"

    async def test_recovery_from_degraded_sends_recovery_notice(self, monkeypatch, monitor_env):
        _set_services(monkeypatch, {"worker": (True, 503, "degraded")})
        await bd.monitor_services(monitor_env["pool"])

        _set_services(monkeypatch, {"worker": (True, 200, "healthy")})
        issues = await bd.monitor_services(monitor_env["pool"])

        assert issues == []
        assert bd._degraded_since == {}
        assert monitor_env["discord"].await_count == 2
        assert "recovered" in monitor_env["discord"].await_args_list[1].args[0]


@pytest.mark.unit
@pytest.mark.asyncio
class TestMonitorServicesRestartGate:
    async def test_first_down_cycle_defers_restart_but_still_alerts(self, monkeypatch, monitor_env):
        _set_services(
            monkeypatch,
            {"api": (False, 0, "timed out"), "worker": (False, 0, "timed out")},
            critical={"api": True},
        )

        issues = await bd.monitor_services(monitor_env["pool"])

        monitor_env["restart"].assert_not_awaited()
        assert {i["service"]: i["state"] for i in issues} == {"api": "down", "worker": "down"}
        # Critical alerting is NOT gated on the restart threshold.
        assert monitor_env["notify"].await_count == 1
        assert "api is DOWN" in monitor_env["notify"].await_args_list[0].args[0]

    async def test_second_consecutive_down_cycle_restarts(self, monkeypatch, monitor_env):
        _set_services(monkeypatch, {"worker": (False, 0, "timed out")})

        await bd.monitor_services(monitor_env["pool"])
        monitor_env["restart"].assert_not_awaited()

        await bd.monitor_services(monitor_env["pool"])
        monitor_env["restart"].assert_awaited_once_with("worker", pool=monitor_env["pool"])

    async def test_recovery_resets_the_consecutive_counter(self, monkeypatch, monitor_env):
        _set_services(monkeypatch, {"worker": (False, 0, "timed out")})
        await bd.monitor_services(monitor_env["pool"])

        _set_services(monkeypatch, {"worker": (True, 200, "healthy")})
        await bd.monitor_services(monitor_env["pool"])
        assert bd._consecutive_down == {}

        _set_services(monkeypatch, {"worker": (False, 0, "timed out")})
        await bd.monitor_services(monitor_env["pool"])
        monitor_env["restart"].assert_not_awaited()

    async def test_degraded_also_resets_the_counter(self, monkeypatch, monitor_env):
        _set_services(monkeypatch, {"worker": (False, 0, "timed out")})
        await bd.monitor_services(monitor_env["pool"])

        _set_services(monkeypatch, {"worker": (True, 503, "degraded")})
        await bd.monitor_services(monitor_env["pool"])
        assert bd._consecutive_down == {}

    async def test_threshold_is_db_tunable(self, monkeypatch, monitor_env):
        monitor_env["pool"].fetchval = AsyncMock(return_value="3")
        _set_services(monkeypatch, {"worker": (False, 0, "timed out")})

        await bd.monitor_services(monitor_env["pool"])
        await bd.monitor_services(monitor_env["pool"])
        monitor_env["restart"].assert_not_awaited()

        await bd.monitor_services(monitor_env["pool"])
        monitor_env["restart"].assert_awaited_once()

    async def test_restart_keeps_firing_while_down_persists(self, monkeypatch, monitor_env):
        """At/above the threshold the heal retries every cycle — the gate
        adds a first-cycle grace, it must not weaken persistence."""
        _set_services(monkeypatch, {"worker": (False, 0, "timed out")})

        for _ in range(4):
            await bd.monitor_services(monitor_env["pool"])
        assert monitor_env["restart"].await_count == 3
