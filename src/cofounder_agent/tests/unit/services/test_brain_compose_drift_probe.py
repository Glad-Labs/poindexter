"""Unit tests for brain/compose_drift_probe.py (GH#213).

Covers the four required scenarios from the issue:

1. no drift → probe returns success without firing notify_operator
2. drift detected, auto-recover off → notify_operator fires once
3. drift detected, auto-recover on → docker compose up runs (mocked) +
   re-check clears drift
4. dedupe — same drift across consecutive cycles → only one notification

Plus assorted edge cases (compose file unreadable, container missing,
recreate fails, drift persists after recreate). All external I/O
(asyncpg, subprocess, file read) is mocked — no real ``docker compose``
runs and no test sleeps for real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from brain import compose_drift_probe as cdp


def _make_pool(setting_values: dict[str, str] | None = None):
    """Async pool mock that returns app_settings values from a dict."""
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock()
    pool.execute = AsyncMock()

    setting_values = setting_values or {}

    async def _fetchval(_query, key):
        return setting_values.get(key)

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    return pool


def _compose_spec(services: dict | None = None) -> dict:
    """Build a minimal compose spec dict that the probe will accept."""
    return {
        "version": "3.8",
        "services": services or {
            "worker": {
                "container_name": "poindexter-worker",
                "image": "poindexter/worker:latest",
                "environment": {"DATABASE_URL": "postgres://x", "PORT": "8002"},
                "volumes": ["./src:/app:ro"],
                "ports": ["8002:8002"],
            }
        },
    }


def _matching_inspect(
    image: str = "poindexter/worker:latest",
    env_keys: tuple[str, ...] = ("DATABASE_URL", "PORT"),
    mount_targets: tuple[str, ...] = ("/app",),
    host_ports: tuple[str, ...] = ("8002",),
) -> dict:
    """Build a `docker inspect` JSON body that matches the spec above."""
    return {
        "Config": {
            "Image": image,
            "Env": [f"{k}=value" for k in env_keys],
        },
        "HostConfig": {
            "Binds": [f"/host/path:{t}:ro" for t in mount_targets],
            "PortBindings": {
                "8002/tcp": [{"HostIp": "", "HostPort": p} for p in host_ports]
            },
        },
        "Mounts": [{"Type": "bind", "Destination": t} for t in mount_targets],
    }


@pytest.fixture(autouse=True)
def _reset_module_state():
    cdp._last_notified_drifted = frozenset()
    yield
    cdp._last_notified_drifted = frozenset()


# ---------------------------------------------------------------------------
# Scenario 1 — no drift → success, no notify
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoDrift:
    @pytest.mark.asyncio
    async def test_zero_drift_returns_ok_no_notify(self):
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        notifies: list[dict] = []
        recreate_calls: list[tuple[str, list[str]]] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        def fake_recreate(path, services):
            recreate_calls.append((path, list(services)))
            return True, "should not be called"

        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=fake_notify,
            inspect_fn=lambda _name: _matching_inspect(),
            recreate_fn=fake_recreate,
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda _s: None,
        )

        assert summary["ok"] is True
        assert summary["status"] == "no_drift"
        assert summary["drifted_count"] == 0
        assert summary["inspected_count"] == 1
        assert notifies == []
        assert recreate_calls == []

        # Last execute call should be the no-drift audit row.
        audit_call = pool.execute.call_args_list[-1]
        assert "audit_log" in audit_call.args[0]
        assert audit_call.args[1] == "probe.compose_drift_ok"

    @pytest.mark.asyncio
    async def test_drift_clear_resets_notify_dedupe(self):
        # If a previous cycle notified about a drifted service, then a
        # later cycle finds drift cleared, the module-level dedupe
        # state must reset so a new drift event re-notifies.
        cdp._last_notified_drifted = frozenset({"worker"})

        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            inspect_fn=lambda _name: _matching_inspect(),
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda _s: None,
        )

        assert cdp._last_notified_drifted == frozenset()


# ---------------------------------------------------------------------------
# Scenario 2 — drift detected, auto-recover off → notify once
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDriftAutoRecoverDisabled:
    @pytest.mark.asyncio
    async def test_missing_mount_triggers_notify(self):
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        notifies: list[dict] = []
        recreate_calls: list[None] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        def fake_recreate(_p, _s):
            recreate_calls.append(None)
            return True, "should not be called"

        # YAML wants /app mount; live container has nothing mounted.
        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=fake_notify,
            inspect_fn=lambda _name: _matching_inspect(mount_targets=()),
            recreate_fn=fake_recreate,
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda _s: None,
        )

        assert summary["ok"] is False
        assert summary["status"] == "drift_detected_no_recover"
        assert summary["drifted_count"] == 1
        assert summary["drifted_services"] == ["worker"]
        assert summary["auto_recover_enabled"] is False
        assert recreate_calls == []
        assert len(notifies) == 1
        assert "Compose drift detected" in notifies[0]["title"]
        assert notifies[0]["severity"] == "warning"
        assert "DISABLED" in notifies[0]["detail"]
        # Mount target should appear in the detail summary.
        assert "/app" in notifies[0]["detail"]

    @pytest.mark.asyncio
    async def test_missing_env_key_triggers_notify(self):
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        notifies: list[dict] = []

        # YAML declares DATABASE_URL + PORT; container only has PORT.
        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: notifies.append(k),
            inspect_fn=lambda _name: _matching_inspect(env_keys=("PORT",)),
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda _s: None,
        )

        assert summary["status"] == "drift_detected_no_recover"
        assert len(notifies) == 1
        assert "DATABASE_URL" in notifies[0]["detail"]

    @pytest.mark.asyncio
    async def test_image_mismatch_triggers_notify(self):
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        notifies: list[dict] = []

        # YAML wants poindexter/worker:latest; live is on :v1.
        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: notifies.append(k),
            inspect_fn=lambda _name: _matching_inspect(
                image="poindexter/worker:v1"
            ),
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda _s: None,
        )

        assert summary["status"] == "drift_detected_no_recover"
        assert len(notifies) == 1
        assert "image mismatch" in notifies[0]["detail"]

    @pytest.mark.asyncio
    async def test_drift_writes_per_service_audit_event(self):
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            inspect_fn=lambda _name: _matching_inspect(mount_targets=()),
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda _s: None,
        )

        events = [
            call.args[1] for call in pool.execute.call_args_list
            if "audit_log" in call.args[0]
        ]
        assert "probe.compose_drift_detected" in events

    @pytest.mark.asyncio
    async def test_audit_payload_does_not_leak_env_values(self):
        # Critical: the audit row must list env *keys* only, never
        # values. Regression guard for the issue's "no secret values"
        # requirement.
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        secret_value = "super-secret-database-url-value"

        def yaml_with_secret(_path):
            return _compose_spec({
                "worker": {
                    "container_name": "poindexter-worker",
                    "image": "poindexter/worker:latest",
                    "environment": {
                        "DATABASE_URL": secret_value,
                        "PORT": "8002",
                    },
                }
            })

        await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            inspect_fn=lambda _name: _matching_inspect(env_keys=("PORT",)),
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=yaml_with_secret,
            sleep_fn=lambda _s: None,
        )

        for call in pool.execute.call_args_list:
            for arg in call.args:
                if isinstance(arg, str):
                    assert secret_value not in arg, (
                        "Audit payload leaked env value!"
                    )


# ---------------------------------------------------------------------------
# Scenario 3 — drift detected, auto-recover on → recreate + re-check
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDriftAutoRecoverEnabled:
    @pytest.mark.asyncio
    async def test_recover_clears_drift_no_escalation(self):
        # Drift detected, auto-recover ON, recreate succeeds, second
        # inspect call returns a matching container → recovered status.
        pool = _make_pool({"compose_drift_auto_recover_enabled": "true"})

        notifies: list[dict] = []
        recreate_calls: list[tuple[str, list[str]]] = []
        sleep_calls: list[float] = []

        def fake_notify(**kwargs):
            notifies.append(kwargs)

        def fake_recreate(path, services):
            recreate_calls.append((path, list(services)))
            return True, "Recreated"

        # First inspect: drift (no mounts). Subsequent inspects (post-
        # recreate re-probe): matching container.
        inspect_results = iter([
            _matching_inspect(mount_targets=()),  # initial drift
        ])

        def fake_inspect(_name):
            try:
                return next(inspect_results)
            except StopIteration:
                return _matching_inspect()  # recovered

        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=fake_notify,
            inspect_fn=fake_inspect,
            recreate_fn=fake_recreate,
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda s: sleep_calls.append(s),
        )

        assert summary["ok"] is True
        assert summary["status"] == "recovered"
        assert summary["drifted_count"] == 0
        assert summary["previous_drifted_services"] == ["worker"]
        assert len(recreate_calls) == 1
        assert recreate_calls[0][1] == ["worker"]
        # No escalation needed — recovery succeeded silently.
        assert notifies == []
        # We did sleep between recreate and re-probe.
        assert sleep_calls == [cdp.RECOVER_WAIT_SECONDS]

        events = [
            call.args[1] for call in pool.execute.call_args_list
            if "audit_log" in call.args[0]
        ]
        assert "probe.compose_drift_detected" in events
        assert "probe.compose_drift_recovered" in events

    @pytest.mark.asyncio
    async def test_recreate_fails_escalates(self):
        # docker compose up itself fails → critical notify, no re-probe.
        pool = _make_pool({"compose_drift_auto_recover_enabled": "true"})

        notifies: list[dict] = []
        sleep_calls: list[float] = []

        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: notifies.append(k),
            inspect_fn=lambda _name: _matching_inspect(mount_targets=()),
            recreate_fn=lambda _p, _s: (
                False, "docker socket permission denied"
            ),
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda s: sleep_calls.append(s),
        )

        assert summary["ok"] is False
        assert summary["status"] == "recover_recreate_failed"
        assert len(notifies) == 1
        assert notifies[0]["severity"] == "critical"
        assert "docker socket permission denied" in notifies[0]["detail"]
        # No sleep when recreate failed — we don't bother re-probing.
        assert sleep_calls == []

    @pytest.mark.asyncio
    async def test_drift_persists_after_recreate_escalates(self):
        # Recreate succeeded but drift is still there (e.g. compose
        # references a mount source that doesn't exist) → critical
        # notify.
        pool = _make_pool({"compose_drift_auto_recover_enabled": "true"})

        notifies: list[dict] = []

        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: notifies.append(k),
            # Always returns a drifted container — recreate doesn't
            # actually fix it.
            inspect_fn=lambda _name: _matching_inspect(mount_targets=()),
            recreate_fn=lambda _p, _s: (True, "Recreated"),
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda _s: None,
        )

        assert summary["ok"] is False
        assert summary["status"] == "recover_drift_persists"
        assert len(notifies) == 1
        assert notifies[0]["severity"] == "critical"
        assert "PERSISTS" in notifies[0]["title"]


# ---------------------------------------------------------------------------
# Scenario 4 — dedupe across cycles
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDedupe:
    @pytest.mark.asyncio
    async def test_repeat_cycle_with_unchanged_drift_does_not_renotify(self):
        # First cycle notifies; second cycle with same drifted-services
        # set must NOT re-notify (avoids stuck-loop Telegram blasts).
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        notifies: list[dict] = []

        async def cycle():
            await cdp.run_compose_drift_probe(
                pool,
                notify_fn=lambda **k: notifies.append(k),
                inspect_fn=lambda _name: _matching_inspect(mount_targets=()),
                recreate_fn=lambda _p, _s: (True, ""),
                yaml_loader=lambda _p: _compose_spec(),
                sleep_fn=lambda _s: None,
            )

        await cycle()
        assert len(notifies) == 1

        await cycle()
        assert len(notifies) == 1  # no new notify

    @pytest.mark.asyncio
    async def test_drift_set_change_re_notifies(self):
        # First cycle: only "worker" drifts. Second cycle: "worker" +
        # "api" drift. Set changed → re-notify.
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        notifies: list[dict] = []

        spec_one_service = _compose_spec({
            "worker": {
                "container_name": "poindexter-worker",
                "image": "poindexter/worker:latest",
                "volumes": ["./src:/app:ro"],
            },
        })
        spec_two_services = _compose_spec({
            "worker": {
                "container_name": "poindexter-worker",
                "image": "poindexter/worker:latest",
                "volumes": ["./src:/app:ro"],
            },
            "api": {
                "container_name": "poindexter-api",
                "image": "poindexter/api:latest",
                "volumes": ["./src:/app:ro"],
            },
        })

        await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: notifies.append(k),
            inspect_fn=lambda _name: _matching_inspect(mount_targets=()),
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: spec_one_service,
            sleep_fn=lambda _s: None,
        )
        await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: notifies.append(k),
            inspect_fn=lambda _name: _matching_inspect(mount_targets=()),
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: spec_two_services,
            sleep_fn=lambda _s: None,
        )

        assert len(notifies) == 2


# ---------------------------------------------------------------------------
# Edge cases — compose unreadable, container missing, skip list
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_compose_file_unreadable_returns_unknown(self):
        # YAML loader returns None (file missing or PyYAML absent) →
        # status=unknown, no escalation, no recreate.
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        notifies: list[dict] = []
        recreate_calls: list[None] = []

        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: notifies.append(k),
            inspect_fn=lambda _name: _matching_inspect(),
            recreate_fn=lambda _p, _s: (recreate_calls.append(None) or (True, "")),
            yaml_loader=lambda _p: None,
            sleep_fn=lambda _s: None,
        )

        assert summary["ok"] is True  # not OUR failure to surface
        assert summary["status"] == "unknown"
        assert notifies == []
        assert recreate_calls == []

    @pytest.mark.asyncio
    async def test_compose_missing_services_returns_unknown(self):
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            inspect_fn=lambda _name: _matching_inspect(),
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: {"version": "3"},  # no services:
            sleep_fn=lambda _s: None,
        )

        assert summary["status"] == "unknown"

    @pytest.mark.asyncio
    async def test_docker_unreachable_returns_unknown_no_per_service_spam(self):
        # Brain container's docker socket is bind-mounted but unreadable
        # (root-owned socket, brain runs as non-root). Without a pre-flight
        # check, the probe interprets every container as "missing" and
        # writes one audit_log row per service per cycle — Matt's prod
        # generated 2117 such rows in 6h before this guard. The fix:
        # detect docker-unreachable up front, emit ONE compose_drift_unknown
        # event, and return without iterating services.
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        # Spec with multiple services so the test would clearly fail if
        # the per-service loop runs.
        spec = _compose_spec({
            f"svc{i}": {
                "container_name": f"poindexter-svc{i}",
                "image": f"poindexter/svc{i}:latest",
            }
            for i in range(5)
        })

        notifies: list[dict] = []
        inspect_calls: list[str] = []

        def fake_inspect(name):
            inspect_calls.append(name)
            return None  # would normally be treated as drift

        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: notifies.append(k),
            inspect_fn=fake_inspect,
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: spec,
            sleep_fn=lambda _s: None,
            # Pre-flight reports docker unreachable.
            docker_reachable_fn=lambda: (
                False, "permission denied connecting to docker socket"
            ),
        )

        assert summary["status"] == "unknown"
        assert summary["ok"] is True  # not OUR failure to surface
        assert notifies == []
        # The pre-flight check short-circuits — per-service inspect must
        # not run, otherwise we'd write per-service audit rows.
        assert inspect_calls == []

        # Exactly one audit_log row should be written, with event
        # probe.compose_drift_unknown.
        audit_events = [
            call.args[1] for call in pool.execute.call_args_list
            if "audit_log" in call.args[0]
        ]
        assert audit_events == ["probe.compose_drift_unknown"]
        # Detail mentions docker-unreachable so an operator can diagnose.
        audit_detail = pool.execute.call_args_list[-1].args[3]
        assert "docker" in audit_detail.lower()
        assert "permission denied" in audit_detail.lower()

    @pytest.mark.asyncio
    async def test_docker_reachable_default_does_not_short_circuit(self):
        # Without an explicit docker_reachable_fn the probe should default
        # to "reachable" and run the normal flow — no behavior change for
        # callers that don't care about the new pre-flight.
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        inspect_calls: list[str] = []

        def fake_inspect(name):
            inspect_calls.append(name)
            return _matching_inspect()

        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            inspect_fn=fake_inspect,
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda _s: None,
        )

        assert summary["status"] == "no_drift"
        assert inspect_calls == ["poindexter-worker"]

    @pytest.mark.asyncio
    async def test_container_missing_counts_as_drift(self):
        # docker inspect returns None (container doesn't exist) → drift.
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        notifies: list[dict] = []

        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: notifies.append(k),
            inspect_fn=lambda _name: None,
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda _s: None,
        )

        assert summary["ok"] is False
        assert summary["status"] == "drift_detected_no_recover"
        assert len(notifies) == 1
        assert "container not running" in notifies[0]["detail"]

    @pytest.mark.asyncio
    async def test_skip_list_excludes_services(self):
        pool = _make_pool({
            "compose_drift_auto_recover_enabled": "false",
            "compose_drift_skip_services": "worker,api",
        })

        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            # If we got here we'd report drift, but the skip list
            # should remove the only service.
            inspect_fn=lambda _name: _matching_inspect(mount_targets=()),
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda _s: None,
        )

        assert summary["status"] == "no_drift"
        assert summary["inspected_count"] == 0

    @pytest.mark.asyncio
    async def test_service_without_container_name_is_skipped(self):
        # Services without container_name can't be looked up reliably
        # via `docker inspect` (would need to guess project prefix), so
        # we skip them.
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})

        spec = _compose_spec({
            "no_name": {
                "image": "foo:bar",
                "volumes": ["./x:/y:ro"],
            }
        })

        inspect_calls: list[str] = []

        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            inspect_fn=lambda name: (inspect_calls.append(name) or _matching_inspect()),
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: spec,
            sleep_fn=lambda _s: None,
        )

        assert inspect_calls == []  # never inspected
        assert summary["status"] == "no_drift"

    @pytest.mark.asyncio
    async def test_audit_write_failure_does_not_crash_probe(self):
        pool = _make_pool({"compose_drift_auto_recover_enabled": "false"})
        pool.execute = AsyncMock(side_effect=Exception("audit_log gone"))

        summary = await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: None,
            inspect_fn=lambda _name: _matching_inspect(),
            recreate_fn=lambda _p, _s: (True, ""),
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda _s: None,
        )

        assert summary["status"] == "no_drift"

    @pytest.mark.asyncio
    async def test_auto_recover_setting_missing_defaults_off(self):
        # Row absent → behave as if auto_recover=false.
        pool = _make_pool({})  # no settings

        notifies: list[dict] = []
        recreate_calls: list[None] = []

        await cdp.run_compose_drift_probe(
            pool,
            notify_fn=lambda **k: notifies.append(k),
            inspect_fn=lambda _name: _matching_inspect(mount_targets=()),
            recreate_fn=lambda _p, _s: (recreate_calls.append(None) or (True, "")),
            yaml_loader=lambda _p: _compose_spec(),
            sleep_fn=lambda _s: None,
        )

        assert recreate_calls == []
        assert len(notifies) == 1


# ---------------------------------------------------------------------------
# YAML helpers — direct unit coverage
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestYamlHelpers:
    def test_env_keys_dict_form(self):
        keys = cdp._yaml_env_keys({"FOO": "1", "BAR": "2"})
        assert keys == {"FOO", "BAR"}

    def test_env_keys_list_form_with_values(self):
        keys = cdp._yaml_env_keys(["FOO=1", "BAR=2"])
        assert keys == {"FOO", "BAR"}

    def test_env_keys_list_form_bare(self):
        keys = cdp._yaml_env_keys(["FOO", "BAR"])
        assert keys == {"FOO", "BAR"}

    def test_env_keys_empty(self):
        assert cdp._yaml_env_keys(None) == set()
        assert cdp._yaml_env_keys([]) == set()
        assert cdp._yaml_env_keys({}) == set()

    def test_volume_targets_short_form(self):
        targets = cdp._yaml_volume_targets([
            "./src:/app:ro",
            "/data:/var/data",
            "named-vol:/var/lib/named",
        ])
        assert targets == {"/app", "/var/data", "/var/lib/named"}

    def test_volume_targets_long_form(self):
        targets = cdp._yaml_volume_targets([
            {"type": "bind", "source": "./src", "target": "/app"},
        ])
        assert targets == {"/app"}

    def test_port_publishings_simple(self):
        ports = cdp._yaml_port_host_publishings(["8080:80", "9090:9090"])
        assert ports == {"8080", "9090"}

    def test_port_publishings_with_bind_addr(self):
        ports = cdp._yaml_port_host_publishings(["127.0.0.1:8080:80"])
        assert ports == {"8080"}

    def test_port_publishings_dict_form(self):
        ports = cdp._yaml_port_host_publishings([
            {"published": 8080, "target": 80}
        ])
        assert ports == {"8080"}


@pytest.mark.unit
class TestInspectHelpers:
    def test_live_env_keys_strips_values(self):
        keys = cdp._live_env_keys({
            "Config": {"Env": ["FOO=secret-value", "BAR=other"]}
        })
        assert keys == {"FOO", "BAR"}

    def test_live_mount_targets_from_binds(self):
        targets = cdp._live_mount_targets({
            "HostConfig": {"Binds": ["/host:/container:ro"]}
        })
        assert "/container" in targets

    def test_live_mount_targets_from_mounts(self):
        targets = cdp._live_mount_targets({
            "Mounts": [{"Type": "bind", "Destination": "/foo"}]
        })
        assert targets == {"/foo"}

    def test_live_port_publishings(self):
        ports = cdp._live_port_publishings({
            "HostConfig": {
                "PortBindings": {
                    "80/tcp": [{"HostIp": "", "HostPort": "8080"}],
                    "443/tcp": None,
                }
            }
        })
        assert ports == {"8080"}

    def test_live_image_tag(self):
        assert cdp._live_image_tag({"Config": {"Image": "foo:bar"}}) == "foo:bar"
        assert cdp._live_image_tag({"Config": {}}) is None
        assert cdp._live_image_tag({}) is None


@pytest.mark.unit
class TestDiffService:
    def test_no_drift(self):
        diff = cdp._diff_service(
            {
                "image": "foo:bar",
                "environment": {"FOO": "1"},
                "volumes": ["./x:/app:ro"],
                "ports": ["8080:80"],
            },
            _matching_inspect(
                image="foo:bar",
                env_keys=("FOO",),
                mount_targets=("/app",),
                host_ports=("8080",),
            ),
        )
        assert diff["drifted"] is False

    def test_container_missing(self):
        diff = cdp._diff_service({"image": "foo:bar"}, None)
        assert diff["drifted"] is True
        assert diff["container_missing"] is True

    def test_extras_in_container_dont_count_as_drift(self):
        # Container has extra env vars Docker injected (PATH, HOME) — that's
        # not drift, only the YAML-side missing-from-container case is.
        diff = cdp._diff_service(
            {"image": "foo:bar", "environment": {"FOO": "1"}},
            {
                "Config": {
                    "Image": "foo:bar",
                    "Env": ["FOO=val", "PATH=/bin", "HOME=/root"],
                },
                "HostConfig": {},
                "Mounts": [],
            },
        )
        assert diff["drifted"] is False


# ---------------------------------------------------------------------------
# Env-var expansion in volume/port specs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExpandComposeValue:
    """Compose YAML uses `${VAR:-default}` interpolation in mount paths and
    ports. PyYAML doesn't expand it; the running container has the resolved
    value in its inspect output. Without expansion the probe reports false
    drift on every service that uses env-var paths."""

    def test_simple_var_substitution(self, monkeypatch):
        monkeypatch.setenv("HOME", "/Users/matt")
        assert cdp._expand_compose_value("${HOME}/x") == "/Users/matt/x"

    def test_bare_var(self, monkeypatch):
        monkeypatch.setenv("HOME", "/Users/matt")
        assert cdp._expand_compose_value("$HOME/x") == "/Users/matt/x"

    def test_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("MISSING_VAR", raising=False)
        assert cdp._expand_compose_value("${MISSING_VAR:-/fallback}/x") == "/fallback/x"

    def test_default_ignored_when_set(self, monkeypatch):
        monkeypatch.setenv("HOME", "/Users/matt")
        assert cdp._expand_compose_value("${HOME:-/fallback}/x") == "/Users/matt/x"

    def test_default_when_empty(self, monkeypatch):
        monkeypatch.setenv("EMPTY_VAR", "")
        assert cdp._expand_compose_value("${EMPTY_VAR:-/fallback}/x") == "/fallback/x"

    def test_no_var_passthrough(self):
        assert cdp._expand_compose_value("/literal/path") == "/literal/path"

    def test_unset_no_default_empty(self, monkeypatch):
        monkeypatch.delenv("UNSET", raising=False)
        # Compose treats unset-without-default as warning + empty string.
        # We mirror that — an empty path won't match any container mount,
        # which is correct (operator config bug, not drift).
        assert cdp._expand_compose_value("${UNSET}/x") == "/x"


@pytest.mark.unit
class TestVolumeEnvVarExpansion:
    def test_volume_target_with_env_var_in_source(self, monkeypatch):
        # Real prod case: `${HOME:-.}/.poindexter/x:/inside/y`.
        # Pre-fix the parser splits on `:` and grabs the colon inside
        # `${HOME:-.}` as the separator, returning garbage like `-.}/.x`.
        monkeypatch.setenv("HOME", "/Users/matt")
        targets = cdp._yaml_volume_targets(
            ["${HOME:-.}/.poindexter/x:/inside/y"]
        )
        assert targets == {"/inside/y"}

    def test_volume_with_var_in_source_no_default(self, monkeypatch):
        monkeypatch.setenv("HOME", "/Users/matt")
        targets = cdp._yaml_volume_targets(
            ["${HOME}/.cache/huggingface:/cache:ro"]
        )
        assert targets == {"/cache"}

    def test_port_with_env_var_in_host_port(self, monkeypatch):
        monkeypatch.setenv("METRICS_PORT", "9090")
        ports = cdp._yaml_port_host_publishings(["${METRICS_PORT:-9090}:9090"])
        assert ports == {"9090"}

    def test_port_default_when_var_unset(self, monkeypatch):
        monkeypatch.delenv("METRICS_PORT", raising=False)
        ports = cdp._yaml_port_host_publishings(["${METRICS_PORT:-9090}:9090"])
        assert ports == {"9090"}
