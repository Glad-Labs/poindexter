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
import re
from pathlib import Path
from typing import Any
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


def _make_pool(*, setting_values: dict[str, str] | None = None):
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


def _executed_alert_fingerprints(pool) -> list[str]:
    """Pull the fingerprint positional arg from every alert_events INSERT.

    Probe inserts use $1..$4 for (alertname, labels, annotations,
    fingerprint) — fingerprint is therefore call.args[4].
    """
    out: list[str] = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO alert_events" in sql:
            out.append(call.args[4])
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
        """The rolling restart cap still bounds a *flapping* forward — one a
        restart genuinely recovers each cycle but which keeps re-wedging. (A
        forward a restart CAN'T recover now trips the adaptive alert-only
        backoff after the first failed recovery instead — see
        ``TestAdaptiveGiveUp``.) Here every cycle the external probe is wedged
        pre-restart and healthy post-restart, so the failure counter resets
        each cycle and the cap is what eventually limits the churn.
        """
        # Cap of 2 so we can blow through it in two cycles.
        pool = _make_pool(setting_values={
            pf.WATCH_LIST_KEY: json.dumps([
                {"container": "poindexter-pyroscope", "port": 4040, "path": "/"},
            ]),
            pf.RESTART_CAP_PER_WINDOW_KEY: "2",
            pf.RESTART_CAP_WINDOW_MINUTES_KEY: "60",
        })

        # Flapping: internal always ok; external fails on the pre-restart probe
        # and succeeds on the post-restart probe, every cycle.
        external_calls = {"n": 0}

        def fake_http(url, _timeout):
            if "host.docker.internal" not in url:
                return True  # internal hostname always reachable
            external_calls["n"] += 1
            return external_calls["n"] % 2 == 0  # odd=pre=fail, even=post=ok

        restart_calls: list[str] = []

        def fake_restart(container):
            restart_calls.append(container)
            return True, f"Restarted {container}"

        # All within the same minute so the rolling window holds.
        now = 1_000_000.0

        # Cycle 1 — restart attempt 1, recovers (counter resets).
        await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=fake_restart,
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: now,
        )
        # Cycle 2 — restart attempt 2, recovers (now at cap).
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
# Regression — external_url uses host_port end-to-end
# (Glad-Labs/poindexter#472)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExternalUrlUsesHostPort:
    """The watch_list ``host_port`` field exists because compose can map
    host:CONTAINER ports on different sides (e.g. ``3010:3000`` for
    langfuse-web). ``_parse_watch_list`` honours it correctly, but a
    24h false-positive incident proved nothing pinned that the value
    flows through to the actual external probe URL. Lock the contract.
    """

    @pytest.mark.asyncio
    async def test_external_url_uses_host_port_when_different_from_port(self):
        """Reproduces the langfuse-web / pgadmin scenario from #472.
        Without this contract, a regression in either ``_parse_watch_list``
        or ``_check_one_service`` would silently route the external probe
        at ``port`` instead of ``host_port``.
        """
        pool = _make_pool(setting_values={
            pf.WATCH_LIST_KEY: json.dumps([
                {
                    "container": "poindexter-langfuse-web",
                    "port": 3000,
                    "host_port": 3010,
                    "path": "/api/public/health",
                },
                {
                    "container": "poindexter-pgadmin",
                    "port": 80,
                    "host_port": 18443,
                    "path": "/misc/ping",
                },
            ]),
        })

        probed_urls: list[str] = []

        def fake_http(url, _timeout):
            probed_urls.append(url)
            return True  # happy path — we only care about which URLs were hit

        summary = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=lambda c: (True, ""),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        # External URLs MUST use host_port; internal URLs MUST use port.
        # If anyone re-introduces the #472 bug we'll see :3000 / :80 here.
        assert (
            "http://host.docker.internal:3010/api/public/health" in probed_urls
        ), probed_urls
        assert (
            "http://host.docker.internal:18443/misc/ping" in probed_urls
        ), probed_urls
        assert (
            "http://host.docker.internal:3000/api/public/health"
            not in probed_urls
        ), probed_urls
        assert (
            "http://host.docker.internal:80/misc/ping" not in probed_urls
        ), probed_urls

        # And the summary's recorded external_url echoes host_port too —
        # this is the field that gets written to audit_log details.
        lf = summary["services"]["poindexter-langfuse-web"]
        pg = summary["services"]["poindexter-pgadmin"]
        assert lf["external_url"] == (
            "http://host.docker.internal:3010/api/public/health"
        )
        assert pg["external_url"] == "http://host.docker.internal:18443/misc/ping"


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
            "probe_type": "http",
            "recovery_action": "restart",
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

    def test_unparseable_host_port_logs_warning_and_falls_back(self, caplog):
        """Regression for Glad-Labs/poindexter#472. A non-int ``host_port``
        used to silently fall back to ``port`` — that meant a typo in the
        watch_list config produced 24h of false-positive recovery_failed
        alerts before anyone noticed the external probe was hitting the
        wrong port. Surface the misconfiguration as a warning instead.
        """
        import logging
        with caplog.at_level(logging.WARNING, logger=pf.logger.name):
            out = pf._parse_watch_list(json.dumps([
                {
                    "container": "poindexter-langfuse-web",
                    "port": 3000,
                    "host_port": "not-an-int",
                    "path": "/api/public/health",
                },
            ]))
        assert out[0]["host_port"] == 3000  # fallback to ``port``
        assert any(
            "unparseable host_port" in rec.message
            and "poindexter-langfuse-web" in rec.message
            for rec in caplog.records
        ), [rec.message for rec in caplog.records]

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

    def test_probe_type_defaults_to_http(self):
        out = pf._parse_watch_list(json.dumps([
            {"container": "poindexter-grafana", "port": 3000, "path": "/"},
        ]))
        assert out[0]["probe_type"] == "http"

    def test_probe_type_postgres_preserved(self):
        out = pf._parse_watch_list(json.dumps([
            {
                "container": "poindexter-postgres-local",
                "internal_hostname": "postgres-local",
                "port": 5432,
                "host_port": 5433,
                "probe_type": "postgres",
            },
        ]))
        assert out[0]["probe_type"] == "postgres"
        assert out[0]["host_port"] == 5433
        # A DB entry defaults to alert-only recovery (2026-06-29 follow-up).
        assert out[0]["recovery_action"] == "alert_only"

    def test_unknown_probe_type_falls_back_to_http(self):
        out = pf._parse_watch_list(json.dumps([
            {"container": "poindexter-x", "port": 1, "probe_type": "carrier-pigeon"},
        ]))
        assert out[0]["probe_type"] == "http"

    # --- recovery_action resolution (2026-06-29 DB-wedge follow-up) ---
    # A `docker restart` re-establishes a stuck *per-container* wslrelay
    # forward (the HTTP case), but it CANNOT fix a wedge in Docker Desktop's
    # *host-side* port proxy and is destructive for a DB (it severs every
    # consumer's live connection). So the recovery action is a property of the
    # entry: HTTP → restart, DB → alert-only, with a per-entry override.

    def test_http_entry_defaults_recovery_action_restart(self):
        out = pf._parse_watch_list(json.dumps([
            {"container": "poindexter-grafana", "port": 3000, "path": "/"},
        ]))
        assert out[0]["recovery_action"] == "restart"

    def test_postgres_entry_defaults_recovery_action_alert_only(self):
        out = pf._parse_watch_list(json.dumps([
            {
                "container": "poindexter-postgres-local",
                "internal_hostname": "postgres-local",
                "port": 5432,
                "host_port": 5433,
                "probe_type": "postgres",
            },
        ]))
        assert out[0]["recovery_action"] == "alert_only"

    def test_explicit_recovery_action_override(self):
        # The action is a watch_list field, so it's app_settings-tunable: a DB
        # entry can opt back into restart; an HTTP entry can opt into alert-only.
        out = pf._parse_watch_list(json.dumps([
            {
                "container": "poindexter-postgres-local",
                "port": 5432,
                "host_port": 5433,
                "probe_type": "postgres",
                "recovery_action": "restart",
            },
            {
                "container": "poindexter-grafana",
                "port": 3000,
                "recovery_action": "alert_only",
            },
        ]))
        assert out[0]["recovery_action"] == "restart"
        assert out[1]["recovery_action"] == "alert_only"

    def test_unknown_recovery_action_falls_back_to_probe_default(self):
        # Unknown value → fall back to the probe_type default; never crash,
        # never silently pick the destructive action.
        out = pf._parse_watch_list(json.dumps([
            {"container": "poindexter-x", "port": 1, "recovery_action": "self-destruct"},
            {
                "container": "poindexter-postgres-local",
                "port": 5432,
                "probe_type": "postgres",
                "recovery_action": "nonsense",
            },
        ]))
        assert out[0]["recovery_action"] == "restart"      # http default
        assert out[1]["recovery_action"] == "alert_only"   # postgres default


# ---------------------------------------------------------------------------
# Seed contract — the watch_list seeded in 0000_baseline.seeds.sql must only
# name containers that exist AND publish a host port. A stale name silently
# no-ops (``_container_exists`` → False → "unwatched" → skipped), so the
# service gets ZERO coverage and nobody notices. Worse, a real container with
# NO published host port (e.g. langfuse-clickhouse / langfuse-minio, internal
# 8123/9000 only) would make the external probe fail every cycle → a
# false-positive restart loop. This static test (no DB) reads the actual
# seeded value and would have caught the 2026-06-21 drift.
# ---------------------------------------------------------------------------


def _baseline_seeds_path() -> Path:
    """Locate 0000_baseline.seeds.sql by walking up from this test file.

    Walking up (rather than a fixed ``parents[N]``) sidesteps the path-depth
    fragility that makes a few brain tests host-only.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "services" / "migrations" / "0000_baseline.seeds.sql"
        if candidate.exists():
            return candidate
    raise FileNotFoundError(
        "0000_baseline.seeds.sql not found above " + __file__
    )


def _seeded_watch_list() -> list[dict[str, Any]]:
    """Parse the docker_port_forward_watch_list value out of the baseline seed.

    The seeded JSON is single-line and single-quote-delimited in the SQL with
    no apostrophes inside, so a non-greedy capture to the next ``'`` is exact.
    """
    text = _baseline_seeds_path().read_text(encoding="utf-8")
    m = re.search(r"'docker_port_forward_watch_list',\s*'(.*?)'", text)
    assert m, "docker_port_forward_watch_list not seeded in baseline.seeds.sql"
    return pf._parse_watch_list(m.group(1))


# Containers that were seeded but are stale: clickhouse/minio were renamed
# under the langfuse- prefix AND publish no host port; redis-insight and
# pyroscope-grafana-frontend never existed in the running stack.
_RETIRED_WATCH_CONTAINERS = {
    "poindexter-clickhouse",
    "poindexter-minio",
    "poindexter-redis-insight",
    "poindexter-pyroscope-grafana-frontend",
}

# The host-port-publishing services this probe can actually recover.
_EXPECTED_WATCH_CONTAINERS = {
    "poindexter-pyroscope",
    "poindexter-glitchtip-web",
    "poindexter-alertmanager",
    "poindexter-pgadmin",
    "poindexter-grafana",
    "poindexter-prometheus",
    "poindexter-loki",
    "poindexter-langfuse-web",
    "poindexter-postgres-local",
}


@pytest.mark.unit
class TestSeededWatchListIsCurrent:
    def test_no_retired_containers_seeded(self):
        seeded = {e["container"] for e in _seeded_watch_list()}
        leaked = seeded & _RETIRED_WATCH_CONTAINERS
        assert not leaked, (
            f"stale containers still in the seeded watch_list: {sorted(leaked)} "
            "— they no-op (unwatched) or restart-loop (no host port); drop them"
        )

    def test_watch_list_matches_running_stack(self):
        """Exact-set lock (mirrors the #472 host_port contract test). Adding a
        new watched service must update this set — forcing a re-check that the
        container exists AND publishes a host port before it goes in."""
        seeded = {e["container"] for e in _seeded_watch_list()}
        assert seeded == _EXPECTED_WATCH_CONTAINERS, (
            f"seeded watch_list drifted from the running stack: "
            f"unexpected={sorted(seeded - _EXPECTED_WATCH_CONTAINERS)}, "
            f"missing={sorted(_EXPECTED_WATCH_CONTAINERS - seeded)}"
        )


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


# ---------------------------------------------------------------------------
# Regression — fingerprint stability (Glad-Labs/poindexter#428)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAlertFingerprintsAreStable:
    """The probe's alert_events fingerprints must be stable across cycles
    so downstream dedup (alert_dedup_state, dispatcher) collapses repeats
    into one logical event instead of N separate rows.

    Pre-fix the fingerprint was suffixed with ``int(time.time())`` and
    every cycle produced a unique value — see issue #428.
    """

    @pytest.mark.asyncio
    async def test_cap_alert_fingerprint_stable_across_cycles(self):
        pool = _make_pool()
        await pf._emit_cap_alert(
            pool,
            container="poindexter-prometheus",
            cap=3,
            window_minutes=60,
        )
        await pf._emit_cap_alert(
            pool,
            container="poindexter-prometheus",
            cap=3,
            window_minutes=60,
        )
        fingerprints = _executed_alert_fingerprints(pool)
        assert len(fingerprints) == 2
        assert fingerprints[0] == fingerprints[1], (
            "cap-alert fingerprint must be stable per (alertname, "
            "container) — got distinct values across two cycles"
        )
        assert fingerprints[0] == (
            "docker-port-forward-cap-poindexter-prometheus"
        )

    @pytest.mark.asyncio
    async def test_recovery_failed_fingerprint_stable_across_cycles(self):
        pool = _make_pool()
        await pf._emit_recovery_failed_alert(
            pool,
            container="poindexter-prometheus",
            detail="probe still failing after restart",
        )
        await pf._emit_recovery_failed_alert(
            pool,
            container="poindexter-prometheus",
            detail="probe still failing after restart",
        )
        fingerprints = _executed_alert_fingerprints(pool)
        assert len(fingerprints) == 2
        assert fingerprints[0] == fingerprints[1], (
            "recovery_failed fingerprint must be stable per "
            "(alertname, container) — got distinct values across two "
            "cycles"
        )
        assert fingerprints[0] == (
            "docker-port-forward-recovery-failed-poindexter-prometheus"
        )

    @pytest.mark.asyncio
    async def test_cap_fingerprints_distinguish_containers(self):
        pool = _make_pool()
        await pf._emit_cap_alert(
            pool,
            container="poindexter-prometheus",
            cap=3,
            window_minutes=60,
        )
        await pf._emit_cap_alert(
            pool,
            container="poindexter-grafana",
            cap=3,
            window_minutes=60,
        )
        fingerprints = _executed_alert_fingerprints(pool)
        assert len(fingerprints) == 2
        assert fingerprints[0] != fingerprints[1], (
            "cap-alert fingerprints for different containers must "
            "remain distinct"
        )


# ---------------------------------------------------------------------------
# Postgres reachability probe (_pg_probe) — SSLRequest framing
# ---------------------------------------------------------------------------


class _FakeSock:
    """Minimal context-manager socket stub for _pg_probe tests."""

    def __init__(self, reply: bytes = b"", *, raise_on: str | None = None):
        self._reply = reply
        self._raise_on = raise_on
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def settimeout(self, _t):
        pass

    def sendall(self, data):
        if self._raise_on == "sendall":
            raise ConnectionResetError("reset on send")
        self.sent += data

    def recv(self, _n):
        if self._raise_on == "recv":
            raise ConnectionResetError("reset on recv")
        return self._reply


@pytest.mark.unit
class TestPgProbe:
    def test_sends_ssl_request_and_true_on_S(self, monkeypatch):
        sock = _FakeSock(reply=b"S")
        monkeypatch.setattr(
            pf.socket, "create_connection", lambda addr, timeout: sock
        )
        assert pf._pg_probe("postgres-local", 5432, 3.0) is True
        # Mirrors asyncpg's first wire step exactly.
        assert sock.sent == pf._PG_SSL_REQUEST

    def test_true_on_N(self, monkeypatch):
        monkeypatch.setattr(
            pf.socket, "create_connection",
            lambda addr, timeout: _FakeSock(reply=b"N"),
        )
        assert pf._pg_probe("host.docker.internal", 5433, 3.0) is True

    def test_false_on_empty_reply_wedge(self, monkeypatch):
        # The wedge: TCP accepted, then dropped on first byte → empty recv.
        monkeypatch.setattr(
            pf.socket, "create_connection",
            lambda addr, timeout: _FakeSock(reply=b""),
        )
        assert pf._pg_probe("host.docker.internal", 5433, 3.0) is False

    def test_false_on_reset(self, monkeypatch):
        monkeypatch.setattr(
            pf.socket, "create_connection",
            lambda addr, timeout: _FakeSock(raise_on="recv"),
        )
        assert pf._pg_probe("host.docker.internal", 5433, 3.0) is False

    def test_false_on_connect_error(self, monkeypatch):
        def _boom(addr, timeout):
            raise OSError("connection refused")
        monkeypatch.setattr(pf.socket, "create_connection", _boom)
        assert pf._pg_probe("nope", 5432, 3.0) is False


# ---------------------------------------------------------------------------
# Postgres watch entry — wedge detection + recovery via pg_probe_fn
# ---------------------------------------------------------------------------


_PG_ENTRY = {
    "container": "poindexter-postgres-local",
    "internal_hostname": "postgres-local",
    "port": 5432,
    "host_port": 5433,
    "probe_type": "postgres",
}


@pytest.mark.unit
class TestPostgresWatchEntry:
    @pytest.mark.asyncio
    async def test_pg_wedge_defaults_to_alert_only_no_restart(self):
        """2026-06-29 follow-up: a postgres entry's host-port wedge must NOT
        trigger ``docker restart`` by default. Restarting the DB cannot fix a
        host-side Docker Desktop / WSL2 NAT port-proxy wedge and severs every
        internal consumer's live connection (the 2026-06-29 incident that hung
        the brain and took the alert plane down ~45 min). Instead the probe
        escalates via ``alert_events`` + ``notify_operator`` with guidance that
        only the host operator can clear the proxy.
        """
        pool = _make_pool(setting_values={pf.WATCH_LIST_KEY: json.dumps([_PG_ENTRY])})

        def fake_pg(host, _port, _timeout):
            # internal ok, external wedged — the stuck-port-forward signature.
            return host != "host.docker.internal"

        def fake_http(_url, _timeout):
            raise AssertionError("http probe must not run for postgres entry")

        restart_calls: list[str] = []
        notify_calls: list[dict] = []

        summary = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            pg_probe_fn=fake_pg,
            container_exists_fn=lambda c: True,
            restart_fn=lambda c: (restart_calls.append(c) or (True, "ok")),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: notify_calls.append(k),
            now_fn=lambda: 1_000_000.0,
        )

        svc = summary["services"]["poindexter-postgres-local"]
        assert svc["status"] == "alert_only", summary
        assert svc["reason"] == "db_recovery_policy"
        assert summary["ok"] is False
        # The DB was NOT restarted — the entire point of the change.
        assert restart_calls == []
        # alert_events row written so the dispatcher pages the operator.
        assert "docker_port_forward_restart_skipped" in _executed_alertnames(pool)
        # audit row carries the reason for the Grafana/timeline view.
        skip_payloads = [
            p for p in _executed_audit_payloads(pool)
            if p["event"] == "docker_port_forward_restart_skipped"
        ]
        assert skip_payloads, _executed_audit_events(pool)
        assert skip_payloads[0]["payload"]["reason"] == "db_recovery_policy"
        # operator paged with actionable host-side guidance.
        assert notify_calls, "expected a notify_operator page"
        guidance = (notify_calls[0].get("detail") or "").lower()
        assert "docker desktop" in guidance or "wsl" in guidance, notify_calls

    @pytest.mark.asyncio
    async def test_pg_wedge_restart_override_still_restarts(self):
        """An operator who explicitly sets ``recovery_action="restart"`` on a
        DB entry gets the old restart-and-recover path back — the per-entry
        override is honored, so the policy stays tunable.
        """
        entry = {**_PG_ENTRY, "recovery_action": "restart"}
        pool = _make_pool(setting_values={pf.WATCH_LIST_KEY: json.dumps([entry])})
        external = iter([False, True])  # fail, then recover post-restart

        def fake_pg(host, _port, _timeout):
            if host == "host.docker.internal":
                return next(external)
            return True  # internal always ok

        restart_calls: list[str] = []

        summary = await pf.run_docker_port_forward_probe(
            pool,
            pg_probe_fn=fake_pg,
            container_exists_fn=lambda c: True,
            restart_fn=lambda c: (restart_calls.append(c) or (True, "ok")),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        svc = summary["services"]["poindexter-postgres-local"]
        assert svc["status"] == "recovered", summary
        assert restart_calls == ["poindexter-postgres-local"]
        assert "docker_port_forward_recovered" in _executed_audit_events(pool)

    @pytest.mark.asyncio
    async def test_pg_both_down_does_not_restart(self):
        pool = _make_pool(setting_values={pf.WATCH_LIST_KEY: json.dumps([_PG_ENTRY])})
        restart_calls: list[str] = []

        summary = await pf.run_docker_port_forward_probe(
            pool,
            pg_probe_fn=lambda h, p, t: False,  # both down
            container_exists_fn=lambda c: True,
            restart_fn=lambda c: (restart_calls.append(c) or (True, "ok")),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        assert summary["services"]["poindexter-postgres-local"]["status"] == "service_down"
        assert restart_calls == []

    @pytest.mark.asyncio
    async def test_pg_both_ok_no_restart(self):
        pool = _make_pool(setting_values={pf.WATCH_LIST_KEY: json.dumps([_PG_ENTRY])})
        restart_calls: list[str] = []

        summary = await pf.run_docker_port_forward_probe(
            pool,
            pg_probe_fn=lambda h, p, t: True,
            container_exists_fn=lambda c: True,
            restart_fn=lambda c: (restart_calls.append(c) or (True, "ok")),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: 1_000_000.0,
        )

        svc = summary["services"]["poindexter-postgres-local"]
        assert svc["status"] == "ok"
        assert svc["external_url"] == "postgres://host.docker.internal:5433"
        assert restart_calls == []


# ---------------------------------------------------------------------------
# Adaptive give-up — a restart that does NOT recover the forward switches the
# container to alert-only with a backoff, instead of burning the full restart
# cap on a remedy already proven ineffective (2026-06-29 DB-wedge follow-up).
# Protects HTTP entries too: on 2026-06-29 the host port-publish subsystem was
# broadly degrading (:8002 / :3000 wedged the same host-side way), so a restart
# couldn't fix those either.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAdaptiveGiveUp:
    @pytest.mark.asyncio
    async def test_failed_recovery_switches_to_alert_only_next_cycle(self):
        """HTTP entry whose external forward never recovers: cycle 1 restarts
        once (``recovery_failed``); the container is then in alert-only backoff
        so cycle 2 does NOT restart again. This is the core fix — we no longer
        burn the full restart cap on a wedge a restart can't clear.
        """
        pool = _make_pool(setting_values={
            pf.WATCH_LIST_KEY: json.dumps([
                {"container": "poindexter-grafana", "port": 3000, "path": "/"},
            ]),
            pf.MAX_FAILED_RECOVERIES_KEY: "1",
            pf.ALERT_ONLY_BACKOFF_MINUTES_KEY: "60",
        })

        def fake_http(url, _t):
            # internal always ok; external never recovers.
            return "host.docker.internal" not in url

        restart_calls: list[str] = []
        now = 1_000_000.0

        # Cycle 1 — stuck → one restart → recovery fails → backoff armed.
        s1 = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=lambda c: (restart_calls.append(c) or (True, "ok")),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: now,
        )
        assert s1["services"]["poindexter-grafana"]["status"] == "recovery_failed"
        assert restart_calls == ["poindexter-grafana"]

        # Cycle 2 — backoff active → alert-only, NO second restart.
        s2 = await pf.run_docker_port_forward_probe(
            pool,
            http_probe_fn=fake_http,
            container_exists_fn=lambda c: True,
            restart_fn=lambda c: (restart_calls.append(c) or (True, "ok")),
            sleep_fn=lambda s: None,
            notify_fn=lambda **k: None,
            now_fn=lambda: now + 60.0,  # 1 min later — within the 60-min backoff
        )
        svc = s2["services"]["poindexter-grafana"]
        assert svc["status"] == "alert_only", s2
        assert svc["reason"] == "restart_ineffective_backoff"
        # The cap was NOT consumed — still just the one restart.
        assert restart_calls == ["poindexter-grafana"]
        assert "docker_port_forward_restart_skipped" in _executed_alertnames(pool)

    @pytest.mark.asyncio
    async def test_alert_only_does_not_consume_restart_cap(self):
        """A container in alert-only (here a postgres DB) never records a
        restart, so the rolling restart cap stays untouched no matter how many
        cycles the wedge persists.
        """
        pool = _make_pool(setting_values={pf.WATCH_LIST_KEY: json.dumps([_PG_ENTRY])})

        def fake_pg(host, _p, _t):
            return host != "host.docker.internal"  # internal ok, external wedged

        for i in range(5):
            await pf.run_docker_port_forward_probe(
                pool,
                pg_probe_fn=fake_pg,
                container_exists_fn=lambda c: True,
                restart_fn=lambda c: (True, "ok"),
                sleep_fn=lambda s: None,
                notify_fn=lambda **k: None,
                now_fn=lambda: 1_000_000.0 + i,
            )
        # No restart timestamps recorded for the DB container.
        assert pf._restart_state.get("poindexter-postgres-local") in (None, [])

    @pytest.mark.asyncio
    async def test_backoff_expires_allows_restart_again(self):
        """After the alert-only backoff window elapses the probe is willing to
        try one more restart (in case the wedge became the recoverable
        per-container kind). The backoff bounds churn; it doesn't disable
        recovery forever.
        """
        pool = _make_pool(setting_values={
            pf.WATCH_LIST_KEY: json.dumps([
                {"container": "poindexter-grafana", "port": 3000, "path": "/"},
            ]),
            pf.MAX_FAILED_RECOVERIES_KEY: "1",
            pf.ALERT_ONLY_BACKOFF_MINUTES_KEY: "60",
        })

        def fake_http(url, _t):
            return "host.docker.internal" not in url  # never recovers

        restart_calls: list[str] = []

        def run_at(t):
            return pf.run_docker_port_forward_probe(
                pool,
                http_probe_fn=fake_http,
                container_exists_fn=lambda c: True,
                restart_fn=lambda c: (restart_calls.append(c) or (True, "ok")),
                sleep_fn=lambda s: None,
                notify_fn=lambda **k: None,
                now_fn=lambda: t,
            )

        base = 1_000_000.0
        await run_at(base)            # restart #1, backoff until base+3600
        await run_at(base + 60.0)     # within backoff → alert_only
        assert restart_calls == ["poindexter-grafana"]

        s3 = await run_at(base + 3601.0)  # backoff expired → restart allowed
        assert restart_calls == ["poindexter-grafana", "poindexter-grafana"]
        assert s3["services"]["poindexter-grafana"]["status"] == "recovery_failed"

    @pytest.mark.asyncio
    async def test_healthy_cycle_clears_backoff(self):
        """Once the operator clears the host proxy and the external probe passes
        again, the failure/backoff bookkeeping resets so a future wedge earns a
        fresh restart attempt rather than going straight to alert-only.
        """
        pool = _make_pool(setting_values={
            pf.WATCH_LIST_KEY: json.dumps([
                {"container": "poindexter-grafana", "port": 3000, "path": "/"},
            ]),
            pf.MAX_FAILED_RECOVERIES_KEY: "1",
        })

        external_ok = {"val": False}

        def fake_http(url, _t):
            if "host.docker.internal" not in url:
                return True  # internal always ok
            return external_ok["val"]

        restart_calls: list[str] = []

        def run_at(t):
            return pf.run_docker_port_forward_probe(
                pool,
                http_probe_fn=fake_http,
                container_exists_fn=lambda c: True,
                restart_fn=lambda c: (restart_calls.append(c) or (True, "ok")),
                sleep_fn=lambda s: None,
                notify_fn=lambda **k: None,
                now_fn=lambda: t,
            )

        # Cycle 1: wedged → restart #1 fails → backoff armed.
        await run_at(1_000_000.0)
        assert restart_calls == ["poindexter-grafana"]
        # Cycle 2: backoff → alert_only, no restart.
        await run_at(1_000_060.0)
        assert restart_calls == ["poindexter-grafana"]
        # Cycle 3: operator cleared the proxy — external healthy again → reset.
        external_ok["val"] = True
        s3 = await run_at(1_000_120.0)
        assert s3["services"]["poindexter-grafana"]["status"] == "ok"
        # Cycle 4: a fresh wedge → backoff was cleared, so we restart again.
        external_ok["val"] = False
        await run_at(1_000_180.0)
        assert restart_calls == ["poindexter-grafana", "poindexter-grafana"]
