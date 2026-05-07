"""Unit tests for brain/docker_port_forward_probe.py
(Glad-Labs/poindexter#222).

Covers the nine acceptance scenarios:

1. Both probes succeed (internal AND external 200) → ``ok``, no
   restart, no alert.
2. Internal 200 + external connection-closed → restart triggered,
   audit row written, recovery confirmed.
3. Internal 200 + external 200 after recovery wait → audit row says
   ``recovered=true``.
4. Internal 200 + external still failing after restart → audit row
   says ``recovered=false``, ``alert_events`` row written so the
   operator knows.
5. Internal NOT reachable + external NOT reachable → NOT a port-forward
   bug; skip restart, log as ``service_down``.
6. Container not in ``docker ps`` output → return ``unwatched``,
   don't crash.
7. Restart cap reached → suppress further restarts in window, emit
   ``docker_port_forward_restart_capped`` alert.
8. Per-service exception isolation — one service raising doesn't
   skip the rest.
9. Probe disabled via app_setting → no-op return.

All external I/O (subprocess, urllib.request, asyncpg pool) is mocked.
The pool is a ``MagicMock`` whose async methods are ``AsyncMock``s; we
seed app_settings reads via the ``setting_values`` dict passed to
``_make_pool``.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package
# resolves the same way the backup_watcher tests import it.
from brain import docker_port_forward_probe as pf


# ---------------------------------------------------------------------------
# Helpers — pool builder + canned config
# ---------------------------------------------------------------------------


_DEFAULT_WATCH_LIST_JSON = json.dumps([
    {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
    {"container": "poindexter-glitchtip-web", "port": 8080, "path": "/"},
])


def _default_settings() -> dict[str, str]:
    """Match the migration's seed values (subset for tests)."""
    return {
        pf.ENABLED_KEY: "true",
        pf.POLL_INTERVAL_MINUTES_KEY: "5",
        pf.WATCH_LIST_KEY: _DEFAULT_WATCH_LIST_JSON,
        pf.PROBE_TIMEOUT_SECONDS_KEY: "3",
        pf.RECOVERY_WAIT_SECONDS_KEY: "5",
        pf.RESTART_CAP_PER_WINDOW_KEY: "3",
        pf.RESTART_CAP_WINDOW_MINUTES_KEY: "60",
    }


def _make_pool(*, setting_values: Optional[dict[str, str]] = None):
    """Build an asyncpg-style mock pool that:

    - returns ``setting_values[key]`` for ``SELECT value FROM app_settings``
      lookups (via ``fetchval``),
    - records every ``execute`` call so tests can assert on what was
      written (alert_events rows, audit_log rows).
    """
    pool = MagicMock()
    settings = {**_default_settings(), **(setting_values or {})}

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.execute = AsyncMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    return pool


def _executed_alertnames(pool) -> list[str]:
    """Pull the alertname positional arg from every alert_events INSERT."""
    out: list[str] = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO alert_events" in sql:
            out.append(call.args[1])
    return out


def _executed_audit_events(pool) -> list[str]:
    """Pull every event_type written to audit_log by the probe."""
    out: list[str] = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO audit_log" in sql:
            out.append(call.args[1])
    return out


def _executed_audit_payloads(pool) -> list[dict[str, Any]]:
    """Pull every (event_type, payload) tuple written to audit_log.

    Returns a list of dicts: {"event": event_type, "payload": parsed}.
    """
    out: list[dict[str, Any]] = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO audit_log" in sql:
            event = call.args[1]
            payload_json = call.args[3]
            try:
                payload = json.loads(payload_json)
            except (TypeError, ValueError):
                payload = {}
            out.append({"event": event, "payload": payload})
    return out


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset per-container restart bookkeeping between tests."""
    pf._reset_state()
    yield
    pf._reset_state()


# ---------------------------------------------------------------------------
# Scenario 1 — both probes succeed → ok, no restart, no alert
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHappyPath:
    @pytest.mark.asyncio
    async def test_both_probes_ok_no_restart(self):
        pool = _make_pool()

        def fake_http(url, _timeout):
            return True  # everything reachable

        restart_calls: list[str] = []

        def fake_restart(container):
            restart_calls.append(container)
            return True, "should not be called"

        summary = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        assert summary["ok"] is True, summary
        assert summary["status"] == "ok"
        assert all(
            s["status"] == "ok" for s in summary["services"].values()
        ), summary
        assert restart_calls == []
        assert _executed_alertnames(pool) == []


# ---------------------------------------------------------------------------
# Scenario 2 + 3 — stuck pattern triggers restart, recovery confirmed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStuckPortForwardRecovers:
    @pytest.mark.asyncio
    async def test_internal_ok_external_fail_triggers_restart_and_recovers(self):
        """Internal probe returns 200, external returns connection-closed
        → probe restarts the container, post-wait re-probe of the
        external URL returns 200, audit row says recovered=true.
        """
        # Use a single-service watch list so the assertions are easier.
        pool = _make_pool(setting_values={
            pf.WATCH_LIST_KEY: json.dumps([
                {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
            ]),
        })

        # External probe fails the first time, succeeds the second
        # (post-restart) — this simulates the stuck-then-recovered flow.
        external_responses = iter([False, True])

        def fake_http(url, _timeout):
            if "host.docker.internal" in url:
                return next(external_responses)
            return True  # internal hostname always ok

        restart_calls: list[str] = []

        def fake_restart(container):
            restart_calls.append(container)
            return True, f"Restarted {container}"

        sleeps: list[float] = []

        def fake_sleep(seconds):
            sleeps.append(seconds)

        # Monotonically advance ``now`` so recovery_ms is positive.
        now_box = {"t": 1_000_000.0}

        def fake_now():
            now_box["t"] += 0.1
            return now_box["t"]

        summary = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=fake_restart,
            sleep_fn=fake_sleep,
            notify_fn=lambda **k: None,
            now_fn=fake_now,
        )

        assert summary["ok"] is True, summary
        svc = summary["services"]["poindexter-pyroscope"]
        assert svc["status"] == "recovered"
        assert restart_calls == ["poindexter-pyroscope"]
        assert sleeps == [5.0]
        assert svc["recovery_ms"] >= 0

        # Audit row tagged with the issue's required event_type.
        events = _executed_audit_events(pool)
        assert "docker_port_forward_recovered" in events
        # Payload contains container, port, recovery_ms, retried_n.
        recovered_payloads = [
            p for p in _executed_audit_payloads(pool)
            if p["event"] == "docker_port_forward_recovered"
        ]
        assert recovered_payloads, recovered_payloads
        payload = recovered_payloads[0]["payload"]
        assert payload["container"] == "poindexter-pyroscope"
        assert payload["port"] == 4040
        assert payload["recovered"] is True
        assert payload["retried_n"] == 1
        assert "recovery_ms" in payload

        # No alert_events row written — the auto-recovery worked.
        assert _executed_alertnames(pool) == []


# ---------------------------------------------------------------------------
# Scenario 4 — restart attempted but external still failing → page op
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRecoveryFailedPagesOperator:
    @pytest.mark.asyncio
    async def test_restart_does_not_recover_writes_alert_events_row(self):
        pool = _make_pool(setting_values={
            pf.WATCH_LIST_KEY: json.dumps([
                {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
            ]),
        })

        # External never recovers; internal always ok.
        def fake_http(url, _timeout):
            if "host.docker.internal" in url:
                return False
            return True

        summary = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=lambda c: (True, f"Restarted {c}"),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        svc = summary["services"]["poindexter-pyroscope"]
        assert svc["status"] == "recovery_failed"
        assert summary["ok"] is False

        # Audit log carries recovered=false.
        recovered_payloads = [
            p for p in _executed_audit_payloads(pool)
            if p["event"] == "docker_port_forward_recovered"
        ]
        assert recovered_payloads
        assert recovered_payloads[0]["payload"]["recovered"] is False

        # alert_events row was written so the dispatcher pages.
        alertnames = _executed_alertnames(pool)
        assert "docker_port_forward_recovery_failed" in alertnames


# ---------------------------------------------------------------------------
# Scenario 5 — both fail → real outage, skip restart
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestServiceDownIsNotPortForwardBug:
    @pytest.mark.asyncio
    async def test_both_probes_fail_do_not_restart(self):
        pool = _make_pool(setting_values={
            pf.WATCH_LIST_KEY: json.dumps([
                {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
            ]),
        })

        restart_calls: list[str] = []

        def fake_restart(container):
            restart_calls.append(container)
            return True, "should not be called"

        summary = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=lambda url, t: False,  # both fail
            container_exists_fn=lambda c: True,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        svc = summary["services"]["poindexter-pyroscope"]
        assert svc["status"] == "service_down"
        assert restart_calls == []
        # Audit row logged so the operator can see the probe noticed.
        assert "docker_port_forward_service_down" in _executed_audit_events(pool)
        # No restart-related alerts.
        for a in _executed_alertnames(pool):
            assert "restart" not in a and "recovery" not in a


# ---------------------------------------------------------------------------
# Scenario 6 — container not running → unwatched, don't crash
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnwatchedContainerSkipsCleanly:
    @pytest.mark.asyncio
    async def test_missing_container_returns_unwatched(self):
        pool = _make_pool(setting_values={
            pf.WATCH_LIST_KEY: json.dumps([
                {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
                {"container": "poindexter-glitchtip-web", "port": 8080, "path": "/"},
            ]),
        })

        # Pyroscope container is absent; glitchtip is running.
        def fake_exists(container):
            return container != "poindexter-pyroscope"

        http_calls: list[str] = []

        def fake_http(url, _timeout):
            http_calls.append(url)
            return True

        summary = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=fake_exists,
            restart_fn=lambda c: (True, ""),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        assert summary["services"]["poindexter-pyroscope"]["status"] == "unwatched"
        assert summary["services"]["poindexter-glitchtip-web"]["status"] == "ok"
        # No HTTP probes ran for the unwatched service.
        assert all("4040" not in url for url in http_calls)


# ---------------------------------------------------------------------------
# Scenario 7 — restart cap suppresses further restarts and pages
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRestartCapEnforced:
    @pytest.mark.asyncio
    async def test_cap_reached_emits_alert_and_skips_restart(self):
        # Cap of 2 so we can blow through it in two cycles.
        pool = _make_pool(setting_values={
            pf.WATCH_LIST_KEY: json.dumps([
                {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
            ]),
            pf.RESTART_CAP_PER_WINDOW_KEY: "2",
            pf.RESTART_CAP_WINDOW_MINUTES_KEY: "60",
        })

        # Always stuck — external fails forever, internal ok forever.
        def fake_http(url, _timeout):
            return "host.docker.internal" not in url

        restart_calls: list[str] = []

        def fake_restart(container):
            restart_calls.append(container)
            return True, f"Restarted {container}"

        # All within the same minute so the rolling window holds.
        now = 1_000_000.0

        # Cycle 1 — restart attempt 1 (cap not yet reached).
        await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: now,
        )
        # Cycle 2 — restart attempt 2 (now at cap).
        await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: now + 1.0,
        )
        assert restart_calls == ["poindexter-pyroscope", "poindexter-pyroscope"]

        # Cycle 3 — cap reached, must NOT restart again.
        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        s3 = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=fake_notify,
            now_fn=lambda: now + 2.0,
        )
        # No new restart on the capped cycle.
        assert restart_calls == ["poindexter-pyroscope", "poindexter-pyroscope"]
        svc = s3["services"]["poindexter-pyroscope"]
        assert svc["status"] == "restart_capped"
        # alert_events row + audit_log row + notify_fn fired.
        assert "docker_port_forward_restart_capped" in _executed_alertnames(pool)
        assert "docker_port_forward_restart_capped" in _executed_audit_events(pool)
        assert any(
            "restart cap" in (c.get("title") or "").lower()
            for c in notify_calls
        ), notify_calls


# ---------------------------------------------------------------------------
# Scenario 8 — one service raising doesn't kill the others
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPerServiceExceptionIsolation:
    @pytest.mark.asyncio
    async def test_exploding_service_does_not_skip_the_rest(self):
        pool = _make_pool(setting_values={
            pf.WATCH_LIST_KEY: json.dumps([
                {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
                {"container": "poindexter-glitchtip-web", "port": 8080, "path": "/"},
            ]),
        })

        def fake_http(url, _timeout):
            if ":4040" in url:
                raise RuntimeError("synthetic explosion")
            return True

        summary = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=lambda c: (True, ""),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        assert summary["services"]["poindexter-pyroscope"]["status"] == "exception"
        assert summary["services"]["poindexter-glitchtip-web"]["status"] == "ok"
        # Overall ok=False because pyroscope failed; glitchtip preserved.
        assert summary["ok"] is False


# ---------------------------------------------------------------------------
# Scenario 9 — disabled short-circuits
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDisabledShortCircuits:
    @pytest.mark.asyncio
    async def test_disabled_does_no_io(self):
        pool = _make_pool(setting_values={pf.ENABLED_KEY: "false"})

        http_calls: list[str] = []
        restart_calls: list[str] = []

        def fake_http(url, _timeout):
            http_calls.append(url)
            return True

        def fake_restart(container):
            restart_calls.append(container)
            return True, ""

        summary = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        assert summary["status"] == "disabled"
        assert http_calls == []
        assert restart_calls == []


# ---------------------------------------------------------------------------
# Edge — internal-only-down (inverse) doesn't restart
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInverseInternalOnlyDownDoesNotRestart:
    @pytest.mark.asyncio
    async def test_internal_fail_external_ok_skips_restart(self):
        pool = _make_pool(setting_values={
            pf.WATCH_LIST_KEY: json.dumps([
                {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
            ]),
        })

        def fake_http(url, _timeout):
            if "host.docker.internal" in url:
                return True
            return False  # internal fails

        restart_calls: list[str] = []

        def fake_restart(container):
            restart_calls.append(container)
            return True, ""

        summary = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        svc = summary["services"]["poindexter-pyroscope"]
        assert svc["status"] == "internal_only_down"
        assert restart_calls == []


# ---------------------------------------------------------------------------
# Helpers — watch list parsing + restart cap window math
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWatchListParsing:
    def test_strips_poindexter_prefix_for_internal_hostname(self):
        out = pf._parse_watch_list(json.dumps([
            {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
        ]))
        # ``host_port`` defaults to ``port`` for 1:1 compose mappings; an
        # explicit ``host_port`` override only appears when the entry
        # opts in (see test_explicit_host_port_override below).
        assert out == [{
            "container": "poindexter-pyroscope",
            "port": 4040,
            "host_port": 4040,
            "path": "/",
            "internal_hostname": "pyroscope",
        }]

    def test_explicit_internal_hostname_overrides_heuristic(self):
        out = pf._parse_watch_list(json.dumps([
            {
                "container": "weird-name",
                "port": 1234,
                "path": "/health",
                "internal_hostname": "actual-dns-name",
            },
        ]))
        assert out[0]["internal_hostname"] == "actual-dns-name"

    def test_explicit_host_port_override(self):
        out = pf._parse_watch_list(json.dumps([
            {
                "container": "poindexter-prometheus",
                "port": 9090,
                "host_port": 9091,
                "path": "/-/healthy",
            },
        ]))
        assert out[0]["host_port"] == 9091
        assert out[0]["port"] == 9090

    def test_invalid_json_returns_empty_list(self):
        assert pf._parse_watch_list("{not json") == []

    def test_non_array_returns_empty_list(self):
        assert pf._parse_watch_list('{"container": "x", "port": 1}') == []

    def test_skips_entries_missing_required_fields(self):
        out = pf._parse_watch_list(json.dumps([
            {"port": 1234},                       # no container
            {"container": "ok", "port": "abc"},   # bad port
            {"container": "ok2", "port": 80},     # ok
        ]))
        assert len(out) == 1
        assert out[0]["container"] == "ok2"


@pytest.mark.unit
class TestRestartCapWindow:
    def test_old_timestamps_pruned(self):
        pf._restart_state["x"] = [100.0, 200.0, 1_000_000.0]
        # Window of 60 s ago from now=1_000_005 → only 1_000_000 stays.
        n = pf._restarts_in_window("x", now=1_000_005.0, window_seconds=60.0)
        assert n == 1
        assert pf._restart_state["x"] == [1_000_000.0]

    def test_empty_window_clears_cap_alert_flag(self):
        pf._restart_state["x"] = [100.0]
        pf._cap_alert_emitted["x"] = True
        # Now is well after the window — list goes empty, flag clears.
        pf._restarts_in_window("x", now=1_000_000.0, window_seconds=60.0)
        assert "x" not in pf._cap_alert_emitted


# ---------------------------------------------------------------------------
# Probe-Protocol wrapper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestProbeWrapper:
    @pytest.mark.asyncio
    async def test_probe_protocol_wrapper_returns_proberesult(self):
        pool = _make_pool()

        async def fake_probe(_pool, **_kwargs):
            return {
                "ok": True,
                "status": "ok",
                "detail": "fake",
                "services": {
                    "poindexter-pyroscope": {"status": "ok"},
                },
            }

        import brain.docker_port_forward_probe as _pf_mod
        original = _pf_mod.run_docker_port_forward_probe
        _pf_mod.run_docker_port_forward_probe = fake_probe  # type: ignore[assignment]
        try:
            probe = pf.DockerPortForwardProbe()
            result = await probe.check(pool, {})
        finally:
            _pf_mod.run_docker_port_forward_probe = original  # type: ignore[assignment]

        assert result.ok is True
        assert result.detail == "fake"
        assert result.metrics["status"] == "ok"
        assert result.metrics["services"]["poindexter-pyroscope"] == "ok"
