"""Unit tests for brain/outlet_guard_probe.py (2026-09-06 outlet-relay incident)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from brain import outlet_guard_probe as og

PLUG = "http://10.0.0.5"

UPS_ON_BATTERY = """
network_ups_tools_ups_status{flag="OL",ups="cyberpower"} 0
network_ups_tools_ups_status{flag="OB",ups="cyberpower"} 1
network_ups_tools_ups_status{flag="LB",ups="cyberpower"} 0
network_ups_tools_battery_charge{ups="cyberpower"} 61
network_ups_tools_battery_runtime{ups="cyberpower"} 1185
network_ups_tools_input_voltage{ups="cyberpower"} 0
"""
UPS_ON_LINE = """
network_ups_tools_ups_status{flag="OL",ups="cyberpower"} 1
network_ups_tools_ups_status{flag="OB",ups="cyberpower"} 0
network_ups_tools_battery_charge{ups="cyberpower"} 100
"""
UPS_NO_STATUS = "promhttp_metric_handler_requests_total 12\n"


def _default_settings() -> dict[str, str]:
    return {
        og.ENABLED_KEY: "true",
        og.MIN_LINE_VOLTAGE_KEY: "90",
        og.REQUIRE_UPS_ON_BATTERY_KEY: "true",
        og.UPS_METRICS_URL_KEY: "http://nut:9199/ups_metrics?ups=cyberpower",
        og.HTTP_TIMEOUT_SECONDS_KEY: "3",
        og.RESTORE_CAP_KEY: "3",
        og.RESTORE_WINDOW_MINUTES_KEY: "60",
        og.SWITCH_ID_KEY: "0",
        og.DEDUP_HOURS_KEY: "6",
    }


def _make_pool(*, setting_values: dict[str, str] | None = None):
    settings = {**_default_settings(), **(setting_values or {})}
    pool = MagicMock()

    async def _fetchrow(query, *args):
        if "app_settings" in query and args:
            key = args[0]
            if key in settings:
                return {"value": settings[key], "is_secret": False}
            return None
        return None

    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.execute = AsyncMock()
    return pool


class FakeShelly:
    """Stateful fake of the plug + the NUT exporter behind one http client."""

    def __init__(self, *, output: bool, voltage: float = 121.2, source: str = "cloud",
                 ups_text: str | None = UPS_ON_BATTERY, ups_raises: Exception | None = None,
                 set_raises: Exception | None = None, relay_sticks_off: bool = False,
                 get_raises: Exception | None = None):
        self.output = output
        self.voltage = voltage
        self.source = source
        self.ups_text = ups_text
        self.ups_raises = ups_raises
        self.set_raises = set_raises
        self.relay_sticks_off = relay_sticks_off
        self.get_raises = get_raises
        self.set_calls: list[dict] = []
        self.errors: list[str] = []

    def _status(self):
        return {
            "id": 0, "output": self.output, "voltage": self.voltage,
            "apower": 0.0 if not self.output else 281.2, "source": self.source,
            "errors": self.errors or None, "temperature": {"tC": 45.4},
        }

    def factory(self):
        shelly = self

        def _resp(payload=None, text=""):
            r = MagicMock()
            r.status_code = 200
            r.raise_for_status = MagicMock()
            r.json = MagicMock(return_value=payload)
            r.text = text
            return r

        async def _get(url, params=None):
            if "ups_metrics" in url:
                if shelly.ups_raises:
                    raise shelly.ups_raises
                return _resp(text=shelly.ups_text or "")
            if url.endswith("/rpc/Switch.GetStatus"):
                if shelly.get_raises:
                    raise shelly.get_raises
                return _resp(shelly._status())
            raise AssertionError(f"unexpected GET {url}")

        async def _post(url, json=None):
            assert url.endswith("/rpc")
            shelly.set_calls.append(json)
            if shelly.set_raises:
                raise shelly.set_raises
            if not shelly.relay_sticks_off:
                shelly.output = True
                shelly.source = "WS_in"
            return _resp({"id": 1, "result": {"was_on": False}})

        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=None)
        client.get = AsyncMock(side_effect=_get)
        client.post = AsyncMock(side_effect=_post)
        return client


def _alert_rows(pool) -> list[tuple[str, str, dict]]:
    """(alertname, severity, annotations) for every alert_events insert."""
    out = []
    for call in pool.execute.call_args_list:
        sql = call.args[0] if call.args else ""
        if "INSERT INTO alert_events" in sql:
            out.append((call.args[1], call.args[2], json.loads(call.args[4])))
    return out


def _audit_rows(pool) -> list[dict]:
    out = []
    for call in pool.execute.call_args_list:
        sql = call.args[0] if call.args else ""
        if "INSERT INTO audit_log" in sql:
            out.append(json.loads(call.args[3]))
    return out


@pytest.fixture(autouse=True)
def _reset_probe_state():
    og._reset_state()
    yield
    og._reset_state()


async def _run(pool, shelly: FakeShelly, *, now: float = 1000.0, url: str = PLUG):
    return await og.run_outlet_guard_probe(
        pool, http_client_factory=shelly.factory, now_fn=lambda: now, shelly_url=url,
    )


class TestGates:
    @pytest.mark.asyncio
    async def test_disabled_short_circuits(self):
        pool = _make_pool(setting_values={og.ENABLED_KEY: "false"})
        shelly = FakeShelly(output=False)
        result = await _run(pool, shelly)
        assert result["status"] == "disabled"
        assert shelly.set_calls == []

    @pytest.mark.asyncio
    async def test_kill_switch_is_fail_closed_when_row_missing(self):
        pool = _make_pool()
        # Simulate a DB that has no row at all for the kill-switch.
        settings = _default_settings()
        del settings[og.ENABLED_KEY]

        async def _fetchrow(query, *args):
            if args and args[0] in settings:
                return {"value": settings[args[0]], "is_secret": False}
            return None

        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        shelly = FakeShelly(output=False)
        result = await _run(pool, shelly)
        assert result["status"] == "disabled"
        assert shelly.set_calls == []

    @pytest.mark.asyncio
    async def test_unconfigured_plug_is_noop(self, monkeypatch):
        monkeypatch.delenv(og.SHELLY_URL_ENV, raising=False)
        pool = _make_pool()
        result = await og.run_outlet_guard_probe(pool, http_client_factory=FakeShelly(output=False).factory)
        assert result["status"] == "unconfigured"
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_shelly_url_comes_from_env(self, monkeypatch):
        monkeypatch.setenv(og.SHELLY_URL_ENV, "http://192.168.1.180/")
        assert og.resolve_shelly_url() == "http://192.168.1.180"

    @pytest.mark.asyncio
    async def test_outlet_on_is_steady_state_with_no_rows(self):
        pool = _make_pool()
        shelly = FakeShelly(output=True)
        result = await _run(pool, shelly)
        assert result["status"] == "outlet_on"
        assert result["ok"] is True
        assert _alert_rows(pool) == []
        assert _audit_rows(pool) == []
        assert shelly.set_calls == []

    @pytest.mark.asyncio
    async def test_plug_unreachable_is_not_this_probes_alarm(self):
        pool = _make_pool()
        shelly = FakeShelly(output=False, get_raises=ConnectionError("timed out"))
        result = await _run(pool, shelly)
        assert result["status"] == "unreachable"
        assert result["ok"] is True
        assert _alert_rows(pool) == []

    @pytest.mark.asyncio
    async def test_real_outage_is_not_restored(self):
        # Relay open AND 0 V on the input: the feed is dead, nothing to switch.
        pool = _make_pool()
        shelly = FakeShelly(output=False, voltage=0.0)
        result = await _run(pool, shelly)
        assert result["status"] == "mains_absent"
        assert shelly.set_calls == []
        assert _alert_rows(pool) == []


class TestUpsCrossCheck:
    @pytest.mark.asyncio
    async def test_ups_on_line_means_not_our_supply(self):
        pool = _make_pool()
        shelly = FakeShelly(output=False, ups_text=UPS_ON_LINE)
        result = await _run(pool, shelly)
        assert result["status"] == "not_our_supply"
        assert shelly.set_calls == []
        rows = _alert_rows(pool)
        assert [(r[0], r[1]) for r in rows] == [(og.ALERT_NOT_RESTORED, "warning")]

    @pytest.mark.asyncio
    async def test_ups_unreachable_is_fail_closed(self):
        pool = _make_pool()
        shelly = FakeShelly(output=False, ups_raises=ConnectionError("refused"))
        result = await _run(pool, shelly)
        assert result["status"] == "ups_not_confirmed"
        assert result["ok"] is False
        assert shelly.set_calls == []
        rows = _alert_rows(pool)
        assert rows[0][0] == og.ALERT_NOT_RESTORED
        assert "ConnectionError" in rows[0][2]["description"]

    @pytest.mark.asyncio
    async def test_exporter_without_status_series_is_unknown_not_on_line(self):
        pool = _make_pool()
        shelly = FakeShelly(output=False, ups_text=UPS_NO_STATUS)
        result = await _run(pool, shelly)
        assert result["status"] == "ups_not_confirmed"
        assert shelly.set_calls == []

    @pytest.mark.asyncio
    async def test_ups_check_can_be_disabled_for_plug_only_installs(self):
        pool = _make_pool(setting_values={og.REQUIRE_UPS_ON_BATTERY_KEY: "false"})
        shelly = FakeShelly(output=False, ups_raises=ConnectionError("no nut here"))
        result = await _run(pool, shelly)
        assert result["status"] == "restored"
        assert len(shelly.set_calls) == 1

    def test_parse_ups_exposition(self):
        parsed = og.parse_ups_exposition(UPS_ON_BATTERY)
        assert parsed["on_battery"] is True
        assert parsed["on_line"] is False
        assert parsed["battery_charge"] == 61.0
        assert parsed["battery_runtime"] == 1185.0
        assert og.parse_ups_exposition(UPS_ON_LINE)["on_battery"] is False
        assert og.parse_ups_exposition(UPS_NO_STATUS)["on_battery"] is None


class TestRestore:
    @pytest.mark.asyncio
    async def test_restores_and_pages_critical_with_pre_restore_source(self):
        pool = _make_pool()
        shelly = FakeShelly(output=False, source="matter")
        result = await _run(pool, shelly)
        assert result["status"] == "restored"
        assert result["ok"] is True
        assert shelly.set_calls == [
            {"id": 1, "method": "Switch.Set", "params": {"id": 0, "on": True}}
        ]
        assert shelly.output is True
        rows = _alert_rows(pool)
        assert len(rows) == 1
        alertname, severity, ann = rows[0]
        assert alertname == og.ALERT_RESTORED
        assert severity == "critical"
        # The forensic snapshot is the PRE-restore one — Switch.Set overwrote
        # the plug's `source` to WS_in, the alert must still say "matter".
        assert ann["plug"]["source"] == "matter"
        assert ann["plug"]["output"] is False
        assert ann["plug"]["voltage"] == 121.2
        assert ann["ups"]["on_battery"] is True
        assert "61" in ann["description"]
        audit = _audit_rows(pool)
        assert audit and audit[0]["status"] == "restored"

    @pytest.mark.asyncio
    async def test_switch_id_setting_is_honoured(self):
        pool = _make_pool(setting_values={og.SWITCH_ID_KEY: "2"})
        shelly = FakeShelly(output=False)
        await _run(pool, shelly)
        assert shelly.set_calls[0]["params"]["id"] == 2

    @pytest.mark.asyncio
    async def test_set_failure_pages_critical(self):
        pool = _make_pool()
        shelly = FakeShelly(output=False, set_raises=OSError("connection reset"))
        result = await _run(pool, shelly)
        assert result["status"] == "restore_failed"
        assert result["ok"] is False
        rows = _alert_rows(pool)
        assert [(r[0], r[1]) for r in rows] == [(og.ALERT_RESTORE_FAILED, "critical")]
        assert "OSError" in rows[0][2]["description"]

    @pytest.mark.asyncio
    async def test_relay_locked_out_is_a_failed_restore(self):
        # Switch.Set is accepted but the plug's protection keeps the relay open.
        pool = _make_pool()
        shelly = FakeShelly(output=False, relay_sticks_off=True)
        shelly.errors = ["overtemp"]
        result = await _run(pool, shelly)
        assert result["status"] == "restore_failed"
        rows = _alert_rows(pool)
        assert rows[0][0] == og.ALERT_RESTORE_FAILED
        assert rows[0][2]["after"]["errors"] == ["overtemp"]

    @pytest.mark.asyncio
    async def test_restore_cap_stops_switching_and_pages(self):
        pool = _make_pool(setting_values={og.RESTORE_CAP_KEY: "2", og.DEDUP_HOURS_KEY: "0"})
        # Three drops inside the window: two restores, then the cap trips.
        for i, now in enumerate((0.0, 60.0)):
            shelly = FakeShelly(output=False)
            result = await _run(pool, shelly, now=now)
            assert result["status"] == "restored", i
            assert len(shelly.set_calls) == 1
        shelly = FakeShelly(output=False)
        result = await _run(pool, shelly, now=120.0)
        assert result["status"] == "cap_reached"
        assert shelly.set_calls == []
        names = [r[0] for r in _alert_rows(pool)]
        assert names[-1] == og.ALERT_RESTORE_FAILED
        # ...and the window slides: an hour later the guard acts again.
        shelly = FakeShelly(output=False)
        result = await _run(pool, shelly, now=120.0 + 3601.0)
        assert result["status"] == "restored"

    @pytest.mark.asyncio
    async def test_dedup_suppresses_repeat_alert_but_still_restores(self):
        pool = _make_pool()
        first = await _run(pool, FakeShelly(output=False), now=0.0)
        second = await _run(pool, FakeShelly(output=False), now=60.0)
        assert first["status"] == second["status"] == "restored"
        # One alert row (deduped), two audit rows (every action is recorded).
        assert len(_alert_rows(pool)) == 1
        assert len(_audit_rows(pool)) == 2


class TestDefaultsWiring:
    def test_every_key_has_a_default_and_a_self_healing_category(self):
        from services.settings_categories import resolve_category
        from services.settings_defaults import DEFAULTS, METADATA

        keys = [
            og.ENABLED_KEY, og.MIN_LINE_VOLTAGE_KEY, og.REQUIRE_UPS_ON_BATTERY_KEY,
            og.UPS_METRICS_URL_KEY, og.HTTP_TIMEOUT_SECONDS_KEY, og.RESTORE_CAP_KEY,
            og.RESTORE_WINDOW_MINUTES_KEY, og.SWITCH_ID_KEY, og.DEDUP_HOURS_KEY,
        ]
        for k in keys:
            assert k in DEFAULTS, k
            assert k in METADATA, k
            assert resolve_category(k) == "self_healing", k
        assert DEFAULTS[og.ENABLED_KEY] == "true"
        assert DEFAULTS[og.REQUIRE_UPS_ON_BATTERY_KEY] == "true"

    def test_brain_image_copies_the_module(self):
        from pathlib import Path

        repo = Path(__file__).resolve().parents[5]
        dockerfile = (repo / "src" / "cofounder_agent" / "poindexter" / "brain" / "Dockerfile").read_text()
        # The image COPYs the whole package directory (poindexter#1046 step 2), so
        # presence is "the file exists in the package dir the Dockerfile copies".
        assert "COPY poindexter/brain/ /app/poindexter/brain/" in dockerfile, (
            "brain image is baked, not bind-mounted — the package directory COPY is gone"
        )
        assert (repo / "src" / "cofounder_agent" / "poindexter" / "brain" / "outlet_guard_probe.py").is_file()
