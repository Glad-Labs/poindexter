"""Unit tests for brain/compose_drift_probe.py — on-demand suppression
(Glad-Labs/poindexter#425).

Covers the noise-suppression behavior added in #425:

1. A service flagged as on-demand whose container is missing → suppressed
   (no drift entry, no audit row, no notify call).
2. A service NOT flagged as on-demand whose container is missing → still
   reported as drifted (regression guard).
3. A service flagged as on-demand whose container IS running but has
   real spec drift (missing mount) → still reported as drifted (the
   suppression only covers `container_missing`, not genuine drift).
4. The default on-demand list seeds `wan-server` and `sdxl-server` so
   a fresh install gets the right behavior without an operator config
   step.

External I/O (subprocess `docker inspect`, notify_operator) is mocked.
The pool is a MagicMock with AsyncMock methods, seeded via the
`setting_values` dict on `_make_pool`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from brain import compose_drift_probe as cd

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool(*, setting_values: dict[str, str] | None = None):
    """asyncpg-style mock pool that returns canned settings + records writes."""
    pool = MagicMock()
    settings = dict(setting_values or {})

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    audit_rows: list[tuple[Any, ...]] = []

    async def _execute(query, *args):
        audit_rows.append((query, args))
        return None

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.execute = AsyncMock(side_effect=_execute)
    pool._audit_rows = audit_rows  # type: ignore[attr-defined]
    return pool


def _spec(services: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build a minimal compose spec dict for the yaml_loader stub."""
    return {"services": services}


def _audit_event_types(pool) -> list[str]:
    """Pull the event_type arg out of every recorded execute() call."""
    out: list[str] = []
    for _query, args in pool._audit_rows:
        if args and isinstance(args[0], str):
            out.append(args[0])
    return out


@pytest.fixture(autouse=True)
def _reset_compose_drift_module_state():
    """Isolate each test from compose_drift_probe's module-level globals — the
    notify dedup set/timestamp and the host-recover rolling cap all persist
    across calls in production, so they must be reset between tests."""
    cd._reset_host_recover_state()
    cd._last_notified_drifted = frozenset()
    cd._last_notified_at = 0.0
    yield
    cd._reset_host_recover_state()
    cd._last_notified_drifted = frozenset()
    cd._last_notified_at = 0.0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_demand_service_missing_is_suppressed():
    """wan-server/sdxl-server missing → no drift row, no notify."""
    cd._last_notified_drifted = frozenset()  # reset module-level state
    pool = _make_pool(
        setting_values={
            cd.ON_DEMAND_SERVICES_SETTING_KEY: "wan-server,sdxl-server",
        }
    )

    spec = _spec({
        "wan-server": {"container_name": "poindexter-wan-server"},
        "sdxl-server": {"container_name": "poindexter-sdxl-server"},
    })

    notify = MagicMock()
    summary = await cd.run_compose_drift_probe(
        pool,
        notify_fn=notify,
        inspect_fn=lambda _name: None,  # everything missing
        yaml_loader=lambda _path: spec,
        docker_reachable_fn=lambda: (True, ""),
    )

    assert summary["status"] == "no_drift"
    assert summary["drifted_count"] == 0
    notify.assert_not_called()
    # Only the "no drift" audit row should be written — no per-service
    # `compose_drift_detected` rows for the suppressed services.
    events = _audit_event_types(pool)
    assert "probe.compose_drift_detected" not in events
    assert "probe.compose_drift_ok" in events


@pytest.mark.asyncio
async def test_non_on_demand_missing_still_alerts():
    """A regular service going missing must still be reported (no regression)."""
    cd._last_notified_drifted = frozenset()
    pool = _make_pool(
        setting_values={
            cd.ON_DEMAND_SERVICES_SETTING_KEY: "wan-server",
        }
    )

    spec = _spec({
        "worker": {"container_name": "poindexter-worker"},
    })

    notify = MagicMock()
    summary = await cd.run_compose_drift_probe(
        pool,
        notify_fn=notify,
        inspect_fn=lambda _name: None,
        yaml_loader=lambda _path: spec,
        docker_reachable_fn=lambda: (True, ""),
    )

    assert summary["status"] == "drift_detected_no_recover"
    assert summary["drifted_count"] == 1
    assert "worker" in summary["drifted_services"]
    notify.assert_called_once()
    assert "probe.compose_drift_detected" in _audit_event_types(pool)


@pytest.mark.asyncio
async def test_on_demand_running_with_real_drift_still_alerts():
    """If an on-demand container IS running but the spec drifted (missing
    mount), the probe still reports it. Suppression is scoped to
    `container_missing` only — genuine drift on these services must
    still be visible to the operator."""
    cd._last_notified_drifted = frozenset()
    pool = _make_pool(
        setting_values={
            cd.ON_DEMAND_SERVICES_SETTING_KEY: "wan-server",
        }
    )

    spec = _spec({
        "wan-server": {
            "container_name": "poindexter-wan-server",
            "volumes": ["/host/models:/models"],
        },
    })

    # Running container, but the /models mount is missing — real drift.
    inspect_payload = {
        "Config": {"Env": [], "Image": ""},
        "HostConfig": {"Binds": [], "PortBindings": {}},
        "Mounts": [],
    }

    notify = MagicMock()
    summary = await cd.run_compose_drift_probe(
        pool,
        notify_fn=notify,
        inspect_fn=lambda _name: inspect_payload,
        yaml_loader=lambda _path: spec,
        docker_reachable_fn=lambda: (True, ""),
    )

    assert summary["status"] == "drift_detected_no_recover"
    assert summary["drifted_count"] == 1
    assert "wan-server" in summary["drifted_services"]
    notify.assert_called_once()


@pytest.mark.asyncio
async def test_default_on_demand_list_covers_wan_and_sdxl():
    """No operator config → wan-server and sdxl-server are suppressed by
    default. Guards the issue #425 acceptance: out-of-the-box the noise
    is gone without requiring `poindexter settings set` first."""
    cd._last_notified_drifted = frozenset()
    pool = _make_pool(setting_values={})  # no on-demand setting set

    spec = _spec({
        "wan-server": {"container_name": "poindexter-wan-server"},
        "sdxl-server": {"container_name": "poindexter-sdxl-server"},
        "worker": {"container_name": "poindexter-worker"},
    })

    notify = MagicMock()

    def _inspect(name: str):
        # Worker is up; the on-demand pair is down.
        if name == "poindexter-worker":
            return {
                "Config": {"Env": [], "Image": ""},
                "HostConfig": {"Binds": [], "PortBindings": {}},
                "Mounts": [],
            }
        return None

    summary = await cd.run_compose_drift_probe(
        pool,
        notify_fn=notify,
        inspect_fn=_inspect,
        yaml_loader=lambda _path: spec,
        docker_reachable_fn=lambda: (True, ""),
    )

    # worker has no drift, wan/sdxl missing-but-suppressed → overall ok.
    assert summary["status"] == "no_drift"
    notify.assert_not_called()


@pytest.mark.asyncio
async def test_read_on_demand_services_uses_default_when_unset():
    """Direct test of the setting reader — defaults match the constant."""
    pool = _make_pool(setting_values={})
    out = await cd._read_on_demand_services(pool)
    assert out == {"wan-server", "sdxl-server"}


@pytest.mark.asyncio
async def test_read_on_demand_services_empty_string_disables_suppression():
    """Operator can opt out of the default by setting an empty string —
    they get the old behavior back (every missing container alerts)."""
    pool = _make_pool(
        setting_values={cd.ON_DEMAND_SERVICES_SETTING_KEY: ""}
    )
    out = await cd._read_on_demand_services(pool)
    assert out == set()


# ---------------------------------------------------------------------------
# Host-routed recovery (PR3). On drift, a containerised brain delegates the
# `docker compose up` it can't run itself to the host Recovery Agent. These
# cover the new 5-host branch: dispatch, disabled/unconfigured fall-through,
# the rolling cap escalation, and a failed POST. The 5a/5b paths above are
# left untouched (the 6 tests above still pass).
# ---------------------------------------------------------------------------

_RECOVERY_URL = "http://host.docker.internal:9841/recover"


def _seed_recovery(monkeypatch, *, url=_RECOVERY_URL, token="tok"):
    """Make _read_secret_setting return a configured agent URL + token."""
    async def _fake_secret(_pool, key, default=""):
        return {cd.HOST_RECOVER_URL_KEY: url, cd.HOST_RECOVER_TOKEN_KEY: token}.get(key, default)
    monkeypatch.setattr(cd, "_read_secret_setting", _fake_secret)


async def _run_drift(pool, *, host_recover_fn, notify_fn=None):
    """Run the probe against a single drifting (container-missing) service."""
    spec = _spec({"worker": {"container_name": "poindexter-worker", "image": "x:1"}})
    return await cd.run_compose_drift_probe(
        pool,
        notify_fn=notify_fn or MagicMock(),
        inspect_fn=lambda name: None,  # container missing → drift (worker not on-demand)
        yaml_loader=lambda path: spec,
        host_recover_fn=host_recover_fn,
        sleep_fn=lambda s: None,
    )


@pytest.mark.asyncio
async def test_host_recover_dispatches_on_drift_without_paging(monkeypatch):
    cd._reset_host_recover_state()
    _seed_recovery(monkeypatch)
    pool = _make_pool()  # host_recover_enabled defaults true
    calls: list[tuple[str, str]] = []

    async def fake_recover(url, token):
        calls.append((url, token))
        return True, "dispatched"

    notify = MagicMock()
    summary = await _run_drift(pool, host_recover_fn=fake_recover, notify_fn=notify)

    assert summary["status"] == "host_recover_dispatched" and summary["ok"] is True
    assert calls == [(_RECOVERY_URL, "tok")]
    # Self-heal before paging: a successful dispatch leaves an audit trail but
    # does NOT page the operator.
    notify.assert_not_called()
    assert "probe.compose_drift_host_recover_dispatched" in _audit_event_types(pool)


@pytest.mark.asyncio
async def test_host_recover_disabled_falls_through_to_notify(monkeypatch):
    cd._reset_host_recover_state()
    _seed_recovery(monkeypatch)  # url/token present, but feature disabled
    pool = _make_pool(setting_values={cd.HOST_RECOVER_ENABLED_KEY: "false"})
    recover = AsyncMock()
    notify = MagicMock()

    summary = await _run_drift(pool, host_recover_fn=recover, notify_fn=notify)

    recover.assert_not_called()
    assert summary["status"] == "drift_detected_no_recover"  # existing 5a path
    notify.assert_called_once()


@pytest.mark.asyncio
async def test_host_recover_enabled_but_unconfigured_falls_through(monkeypatch):
    cd._reset_host_recover_state()
    # enabled by default, but no agent url/token configured
    monkeypatch.setattr(cd, "_read_secret_setting", AsyncMock(return_value=""))
    pool = _make_pool()
    recover = AsyncMock()
    notify = MagicMock()

    summary = await _run_drift(pool, host_recover_fn=recover, notify_fn=notify)

    recover.assert_not_called()
    assert summary["status"] == "drift_detected_no_recover"  # fell through to 5a
    notify.assert_called_once()


@pytest.mark.asyncio
async def test_host_recover_cap_reached_escalates_critical(monkeypatch):
    cd._reset_host_recover_state()
    _seed_recovery(monkeypatch)
    pool = _make_pool(setting_values={cd.HOST_RECOVER_CAP_KEY: "2"})
    dispatched = {"n": 0}

    async def fake_recover(url, token):
        dispatched["n"] += 1
        return True, "dispatched"

    notify = MagicMock()

    async def run():
        return await _run_drift(pool, host_recover_fn=fake_recover, notify_fn=notify)

    await run()  # attempt 1 (0 < 2 → dispatch)
    await run()  # attempt 2 (1 < 2 → dispatch)
    summary = await run()  # attempt 3 (2 >= 2 → cap reached, no dispatch)

    assert dispatched["n"] == 2  # only 2 dispatches; 3rd is capped
    assert summary["status"] == "host_recover_cap_reached"
    # Cap escalates to a critical page (drift the auto-heal can't clear).
    notify.assert_called_once()
    assert notify.call_args.kwargs.get("severity") == "critical"
    assert "probe.compose_drift_host_recover_cap_reached" in _audit_event_types(pool)


@pytest.mark.asyncio
async def test_host_recover_post_failure_pages_warning(monkeypatch):
    cd._reset_host_recover_state()
    _seed_recovery(monkeypatch)
    pool = _make_pool()

    async def fake_recover(url, token):
        return False, "connection refused"

    notify = MagicMock()
    summary = await _run_drift(pool, host_recover_fn=fake_recover, notify_fn=notify)

    assert summary["status"] == "host_recover_post_failed" and summary["ok"] is False
    # A broken recovery path can't self-heal → page (warning → Discord).
    notify.assert_called_once()
    assert notify.call_args.kwargs.get("severity") == "warning"
    assert "probe.compose_drift_host_recover_failed" in _audit_event_types(pool)
