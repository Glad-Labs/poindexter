"""Unit tests for brain/smart_monitor.py (Glad-Labs/poindexter#387).

Covers the eight acceptance scenarios from the issue:

1. smartctl absent → probe returns ``skipped``, no alert written, no crash
2. Drive enumeration succeeds, no warnings → probe returns ``ok``, no alerts
3. Reallocated sector > 0 → exactly one alert row, severity ``warning``
4. SMART self-test FAILED → exactly one alert row, severity ``critical``
5. Same warning observed twice within dedup window → second is suppressed
6. Same warning observed twice OUTSIDE dedup window → both fire
7. Attribute clears between two cycles → resolution row written
8. Per-drive exception isolation — one drive's smartctl failure
   doesn't skip the next drive

All external I/O (smartctl subprocess, the asyncpg pool) is mocked.
The pool is a ``MagicMock`` whose async methods are ``AsyncMock``s; we
seed app_settings reads via the ``setting_values`` dict passed to
``_make_pool``.
"""

from __future__ import annotations

from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# pythonpath in pyproject.toml includes "../.." so the brain package
# resolves the same way the backup_watcher tests import it.
from brain import smart_monitor as sm


# ---------------------------------------------------------------------------
# Helpers — pool builder + canned smartctl payloads
# ---------------------------------------------------------------------------


def _default_settings() -> dict[str, str]:
    """Match the migration's seed values."""
    return {
        sm.ENABLED_KEY: "true",
        sm.POLL_INTERVAL_HOURS_KEY: "6",
        sm.REALLOCATED_THRESHOLD_KEY: "0",
        sm.CURRENT_PENDING_THRESHOLD_KEY: "0",
        sm.WEAR_LEVELING_WARN_PERCENT_KEY: "90",
        sm.POWER_ON_HOURS_INFO_THRESHOLD_KEY: "50000",
        sm.ALERT_DEDUP_MINUTES_KEY: "360",
    }


def _make_pool(*, setting_values: Optional[dict[str, Optional[str]]] = None):
    """Build an asyncpg-style mock pool that:

    - returns ``setting_values[key]`` for ``SELECT value FROM app_settings``
      lookups (via ``fetchval``) — None values mean "row exists but value
      is NULL", absent keys fall through to the helper's default,
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


def _executed_alert_inserts(pool) -> list[dict[str, Any]]:
    """Pull every alert_events INSERT made by the probe, structured.

    Returns dicts with ``alertname``, ``severity``, ``status`` extracted
    from the positional args of each INSERT.
    """
    out: list[dict[str, Any]] = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO alert_events" not in sql:
            continue
        # Layout differs slightly between firing inserts (severity is a
        # bind param) and resolved inserts (severity is hardcoded
        # 'info'). We detect the resolved case by inspecting the SQL
        # body, then map args by position accordingly.
        if "'resolved'" in sql:
            # Args: (sql, alertname, labels_json, annotations_json, fingerprint)
            out.append({
                "alertname": call.args[1],
                "severity": "info",
                "status": "resolved",
            })
        else:
            # Args: (sql, alertname, severity, labels_json, annotations_json, fingerprint)
            out.append({
                "alertname": call.args[1],
                "severity": call.args[2],
                "status": "firing",
            })
    return out


def _executed_audit_events(pool) -> list[str]:
    """Pull every event_type written to audit_log by the probe."""
    out: list[str] = []
    for call in pool.execute.call_args_list:
        sql = call.args[0]
        if "INSERT INTO audit_log" in sql:
            out.append(call.args[1])
    return out


def _scan_payload(*device_names: str) -> dict[str, Any]:
    """Build a fake ``smartctl --scan-open --json`` response."""
    return {
        "devices": [
            {"name": name, "type": "sat", "model_name": "FAKE_MODEL"}
            for name in device_names
        ],
    }


def _healthy_drive_payload() -> dict[str, Any]:
    """A clean smartctl -a --json payload — every attribute below threshold."""
    return {
        "smart_status": {"passed": True},
        "ata_smart_attributes": {
            "table": [
                {"name": "Reallocated_Sector_Ct", "value": 100,
                 "raw": {"value": 0}},
                {"name": "Current_Pending_Sector", "value": 100,
                 "raw": {"value": 0}},
                {"name": "Power_On_Hours", "value": 99,
                 "raw": {"value": 1000}},
            ],
        },
    }


def _reallocated_payload(count: int = 5) -> dict[str, Any]:
    """smartctl payload with reallocated sectors > 0."""
    return {
        "smart_status": {"passed": True},
        "ata_smart_attributes": {
            "table": [
                {"name": "Reallocated_Sector_Ct", "value": 80,
                 "raw": {"value": count}},
                {"name": "Current_Pending_Sector", "value": 100,
                 "raw": {"value": 0}},
            ],
        },
    }


def _self_test_failed_payload() -> dict[str, Any]:
    """smartctl payload with overall SMART self-test FAILED."""
    return {
        "smart_status": {"passed": False},
        "ata_smart_attributes": {"table": []},
    }


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset dedup + firing-state maps between tests."""
    sm._reset_dedup_state()
    yield
    sm._reset_dedup_state()


def _make_run_fn(scan_payload, drive_payloads):
    """Build a run_fn stub that returns canned smartctl output.

    ``drive_payloads`` is a ``{drive_id: [payload1, payload2, ...]}`` map
    so tests can simulate transitions across cycles by popping the next
    payload each call.
    """
    def run_fn(_binary, args):
        if "--scan-open" in args:
            return 0, scan_payload, ""
        # The drive id is the last positional arg (we may have prefixed
        # with -d <type> -a --json).
        drive_id = args[-1]
        payloads = drive_payloads.get(drive_id) or []
        if not payloads:
            return 0, _healthy_drive_payload(), ""
        # Smartctl exits non-zero when warnings present; we still emit
        # the JSON. Mirror that behavior — the probe must accept both.
        return 4, payloads.pop(0), ""

    return run_fn


# ---------------------------------------------------------------------------
# 1) smartctl absent → skipped, no alert, no crash
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSmartctlAbsent:
    @pytest.mark.asyncio
    async def test_smartctl_missing_skips_gracefully_with_one_notify(self):
        pool = _make_pool()

        notify_calls: list[dict] = []

        def fake_notify(**kwargs):
            notify_calls.append(kwargs)

        # First cycle — smartctl absent. Should notify once.
        summary = await sm.run_smart_monitor_probe(
            pool,
            run_fn=lambda b, a: (-1, None, "should not run"),
            which_fn=lambda _name: None,
            notify_fn=fake_notify,
        )
        assert summary["status"] == "skipped"
        assert summary["ok"] is True
        assert len(notify_calls) == 1
        assert "smartctl" in notify_calls[0]["title"].lower()

        # No alert_events rows were written.
        assert _executed_alert_inserts(pool) == []

        # Second cycle — still absent. Must NOT notify again (one-shot).
        summary2 = await sm.run_smart_monitor_probe(
            pool,
            run_fn=lambda b, a: (-1, None, "should not run"),
            which_fn=lambda _name: None,
            notify_fn=fake_notify,
        )
        assert summary2["status"] == "skipped"
        assert len(notify_calls) == 1, notify_calls


# ---------------------------------------------------------------------------
# 2) Drive enumeration succeeds, no warnings → ok, no alerts
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoWarnings:
    @pytest.mark.asyncio
    async def test_healthy_drives_emit_no_alerts(self):
        pool = _make_pool()

        run_fn = _make_run_fn(
            scan_payload=_scan_payload("/dev/sda", "/dev/sdb"),
            drive_payloads={
                "/dev/sda": [_healthy_drive_payload()],
                "/dev/sdb": [_healthy_drive_payload()],
            },
        )

        summary = await sm.run_smart_monitor_probe(
            pool,
            run_fn=run_fn,
            which_fn=lambda _name: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None,
        )

        assert summary["ok"] is True
        assert summary["status"] == "ok"
        assert summary["drives"]["/dev/sda"]["status"] == "ok"
        assert summary["drives"]["/dev/sdb"]["status"] == "ok"
        assert _executed_alert_inserts(pool) == []


# ---------------------------------------------------------------------------
# 3) Reallocated sector > 0 → one warning alert
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReallocatedSector:
    @pytest.mark.asyncio
    async def test_reallocated_sectors_fire_warning_alert(self):
        pool = _make_pool()

        run_fn = _make_run_fn(
            scan_payload=_scan_payload("/dev/sda"),
            drive_payloads={
                "/dev/sda": [_reallocated_payload(count=7)],
            },
        )

        summary = await sm.run_smart_monitor_probe(
            pool,
            run_fn=run_fn,
            which_fn=lambda _name: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None,
        )

        inserts = _executed_alert_inserts(pool)
        assert len(inserts) == 1, inserts
        assert inserts[0]["severity"] == "warning"
        assert inserts[0]["status"] == "firing"
        assert "Reallocated_Sector_Ct" in inserts[0]["alertname"]
        assert "dev_sda" in inserts[0]["alertname"]

        # The probe summary captures the fired warning.
        assert summary["drives"]["/dev/sda"]["status"] == "warnings_fired"
        assert "Reallocated_Sector_Ct" in summary["drives"]["/dev/sda"]["warnings_fired"]


# ---------------------------------------------------------------------------
# 4) SMART self-test FAILED → one critical alert
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSelfTestFailed:
    @pytest.mark.asyncio
    async def test_self_test_failed_fires_critical_alert(self):
        pool = _make_pool()

        run_fn = _make_run_fn(
            scan_payload=_scan_payload("/dev/sda"),
            drive_payloads={
                "/dev/sda": [_self_test_failed_payload()],
            },
        )

        summary = await sm.run_smart_monitor_probe(
            pool,
            run_fn=run_fn,
            which_fn=lambda _name: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None,
        )

        inserts = _executed_alert_inserts(pool)
        assert len(inserts) == 1, inserts
        assert inserts[0]["severity"] == "critical"
        assert inserts[0]["status"] == "firing"
        assert "smart_status_passed" in inserts[0]["alertname"]
        assert summary["drives"]["/dev/sda"]["status"] == "warnings_fired"


# ---------------------------------------------------------------------------
# 5) Same warning twice within dedup window → second suppressed
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDedupSuppression:
    @pytest.mark.asyncio
    async def test_dedup_suppresses_second_fire_within_window(self):
        pool = _make_pool(
            setting_values={sm.ALERT_DEDUP_MINUTES_KEY: "360"},  # 6h window
        )

        # Two separate cycles, both stale, second cycle 1 hour later
        # — well within the 6h dedup window.
        cycle_times = iter([1_000_000.0, 1_000_000.0 + 3600.0])

        def now_fn():
            return next(cycle_times)

        run_fn = _make_run_fn(
            scan_payload=_scan_payload("/dev/sda"),
            drive_payloads={
                "/dev/sda": [
                    _reallocated_payload(count=5),
                    _reallocated_payload(count=5),
                ],
            },
        )

        # Cycle 1
        s1 = await sm.run_smart_monitor_probe(
            pool, run_fn=run_fn, which_fn=lambda _n: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None, now_fn=now_fn,
        )
        # Need fresh run_fn for cycle 2 because the stub pops payloads.
        run_fn2 = _make_run_fn(
            scan_payload=_scan_payload("/dev/sda"),
            drive_payloads={"/dev/sda": [_reallocated_payload(count=5)]},
        )
        s2 = await sm.run_smart_monitor_probe(
            pool, run_fn=run_fn2, which_fn=lambda _n: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None, now_fn=now_fn,
        )

        firing_inserts = [i for i in _executed_alert_inserts(pool) if i["status"] == "firing"]
        assert len(firing_inserts) == 1, (
            f"Dedup must suppress the second cycle's fire; got {firing_inserts!r}"
        )
        assert "Reallocated_Sector_Ct" in s2["drives"]["/dev/sda"]["warnings_suppressed"]
        # The suppressed cycle is still 'ok' from the brain's POV — no
        # *new* warning fired.
        assert s2["drives"]["/dev/sda"]["status"] == "ok"


# ---------------------------------------------------------------------------
# 6) Same warning twice OUTSIDE dedup window → both fire
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDedupExpiry:
    @pytest.mark.asyncio
    async def test_warning_outside_dedup_window_re_fires(self):
        pool = _make_pool(
            setting_values={sm.ALERT_DEDUP_MINUTES_KEY: "60"},  # 1h window
        )

        # Two cycles 2h apart — second is past the window.
        cycle_times = iter([1_000_000.0, 1_000_000.0 + 2 * 3600.0])

        def now_fn():
            return next(cycle_times)

        run_fn1 = _make_run_fn(
            scan_payload=_scan_payload("/dev/sda"),
            drive_payloads={"/dev/sda": [_reallocated_payload(count=5)]},
        )
        await sm.run_smart_monitor_probe(
            pool, run_fn=run_fn1, which_fn=lambda _n: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None, now_fn=now_fn,
        )

        run_fn2 = _make_run_fn(
            scan_payload=_scan_payload("/dev/sda"),
            drive_payloads={"/dev/sda": [_reallocated_payload(count=5)]},
        )
        s2 = await sm.run_smart_monitor_probe(
            pool, run_fn=run_fn2, which_fn=lambda _n: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None, now_fn=now_fn,
        )

        firing_inserts = [i for i in _executed_alert_inserts(pool) if i["status"] == "firing"]
        assert len(firing_inserts) == 2, (
            f"Outside the dedup window the warning must re-fire; got {firing_inserts!r}"
        )
        assert "Reallocated_Sector_Ct" in s2["drives"]["/dev/sda"]["warnings_fired"]


# ---------------------------------------------------------------------------
# 7) Attribute clears between cycles → resolution row written
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResolutionDetection:
    @pytest.mark.asyncio
    async def test_attribute_clears_writes_resolved_row(self):
        pool = _make_pool()

        # Cycle 1: warning. Cycle 2: drive is healthy.
        cycle_times = iter([1_000_000.0, 1_000_010.0])

        def now_fn():
            return next(cycle_times)

        run_fn1 = _make_run_fn(
            scan_payload=_scan_payload("/dev/sda"),
            drive_payloads={"/dev/sda": [_reallocated_payload(count=5)]},
        )
        await sm.run_smart_monitor_probe(
            pool, run_fn=run_fn1, which_fn=lambda _n: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None, now_fn=now_fn,
        )

        run_fn2 = _make_run_fn(
            scan_payload=_scan_payload("/dev/sda"),
            drive_payloads={"/dev/sda": [_healthy_drive_payload()]},
        )
        s2 = await sm.run_smart_monitor_probe(
            pool, run_fn=run_fn2, which_fn=lambda _n: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None, now_fn=now_fn,
        )

        all_inserts = _executed_alert_inserts(pool)
        resolved = [i for i in all_inserts if i["status"] == "resolved"]
        assert len(resolved) == 1, (
            f"Clearing attribute must write a resolved row; got {all_inserts!r}"
        )
        assert "Reallocated_Sector_Ct" in resolved[0]["alertname"]
        assert s2["drives"]["/dev/sda"]["status"] == "warnings_resolved"
        assert "Reallocated_Sector_Ct" in s2["drives"]["/dev/sda"]["warnings_resolved"]

        # Audit log captured the resolution.
        events = _executed_audit_events(pool)
        assert "probe.smart_monitor_resolved" in events


# ---------------------------------------------------------------------------
# 8) Per-drive exception isolation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPerDriveExceptionIsolation:
    @pytest.mark.asyncio
    async def test_one_drive_exception_does_not_skip_the_next(self):
        pool = _make_pool()

        def run_fn(_binary, args):
            if "--scan-open" in args:
                return 0, _scan_payload("/dev/sda", "/dev/sdb"), ""
            drive_id = args[-1]
            if drive_id == "/dev/sda":
                raise RuntimeError("synthetic sda explosion")
            return 0, _healthy_drive_payload(), ""

        summary = await sm.run_smart_monitor_probe(
            pool,
            run_fn=run_fn,
            which_fn=lambda _name: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None,
        )

        # /dev/sda blew up but /dev/sdb still ran.
        assert summary["drives"]["/dev/sda"]["status"] == "exception"
        assert summary["drives"]["/dev/sdb"]["status"] == "ok"
        # Overall ok=False because sda failed; sdb summary preserved.
        assert summary["ok"] is False


# ---------------------------------------------------------------------------
# Edge cases — disabled + no drives + smartctl JSON parse failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEdgeCases:
    @pytest.mark.asyncio
    async def test_disabled_short_circuits_without_running_smartctl(self):
        pool = _make_pool(setting_values={sm.ENABLED_KEY: "false"})

        def run_fn(_binary, _args):
            raise AssertionError("smartctl must not be invoked when disabled")

        summary = await sm.run_smart_monitor_probe(
            pool,
            run_fn=run_fn,
            which_fn=lambda _name: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None,
        )
        assert summary["status"] == "disabled"
        assert _executed_alert_inserts(pool) == []

    @pytest.mark.asyncio
    async def test_no_drives_returns_no_drives_status(self):
        pool = _make_pool()

        run_fn = _make_run_fn(
            scan_payload={"devices": []},  # empty scan
            drive_payloads={},
        )

        summary = await sm.run_smart_monitor_probe(
            pool,
            run_fn=run_fn,
            which_fn=lambda _name: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None,
        )
        assert summary["status"] == "no_drives"
        assert summary["ok"] is True

    @pytest.mark.asyncio
    async def test_drive_filter_restricts_scan(self):
        pool = _make_pool(
            setting_values={sm.DRIVE_FILTER_KEY: "/dev/sdb"},
        )
        scanned: list[str] = []

        def run_fn(_binary, args):
            if "--scan-open" in args:
                return 0, _scan_payload("/dev/sda", "/dev/sdb"), ""
            drive_id = args[-1]
            scanned.append(drive_id)
            return 0, _healthy_drive_payload(), ""

        summary = await sm.run_smart_monitor_probe(
            pool,
            run_fn=run_fn,
            which_fn=lambda _name: "/usr/sbin/smartctl",
            notify_fn=lambda **k: None,
        )
        assert scanned == ["/dev/sdb"]
        assert "/dev/sda" not in summary["drives"]
        assert "/dev/sdb" in summary["drives"]


# ---------------------------------------------------------------------------
# Helper coverage — _extract_warnings is the parser; test it directly
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractWarnings:
    def _config(self):
        return {
            "reallocated_threshold": 0,
            "current_pending_threshold": 0,
            "wear_leveling_warn_percent": 90,
            "power_on_hours_threshold": 50000,
        }

    def test_healthy_payload_yields_no_warnings(self):
        warnings = sm._extract_warnings(
            _healthy_drive_payload(), drive_id="/dev/sda", config=self._config(),
        )
        assert warnings == []

    def test_pending_sector_fires_warning(self):
        payload = {
            "smart_status": {"passed": True},
            "ata_smart_attributes": {
                "table": [
                    {"name": "Current_Pending_Sector", "value": 90,
                     "raw": {"value": 3}},
                ],
            },
        }
        warnings = sm._extract_warnings(
            payload, drive_id="/dev/sda", config=self._config(),
        )
        assert len(warnings) == 1
        assert warnings[0]["severity"] == "warning"
        assert warnings[0]["attribute_name"] == "Current_Pending_Sector"
        assert warnings[0]["observed_value"] == 3

    def test_wear_leveling_above_threshold_fires(self):
        # SSD with normalized=5 → used 95% of endurance → above 90% threshold.
        payload = {
            "smart_status": {"passed": True},
            "ata_smart_attributes": {
                "table": [
                    {"name": "Wear_Leveling_Count", "value": 5,
                     "raw": {"value": 0}},
                ],
            },
        }
        warnings = sm._extract_warnings(
            payload, drive_id="/dev/sda", config=self._config(),
        )
        assert len(warnings) == 1
        assert warnings[0]["attribute_name"] == "Wear_Leveling_Count"
        assert warnings[0]["observed_value"] == 95
        assert warnings[0]["severity"] == "warning"

    def test_power_on_hours_above_threshold_is_info(self):
        payload = {
            "smart_status": {"passed": True},
            "ata_smart_attributes": {
                "table": [
                    {"name": "Power_On_Hours", "value": 50,
                     "raw": {"value": 60000}},
                ],
            },
        }
        warnings = sm._extract_warnings(
            payload, drive_id="/dev/sda", config=self._config(),
        )
        assert len(warnings) == 1
        assert warnings[0]["severity"] == "info"
        assert warnings[0]["attribute_name"] == "Power_On_Hours"

    def test_nvme_payload_emits_warnings(self):
        payload = {
            "smart_status": {"passed": True},
            "nvme_smart_health_information_log": {
                "percentage_used": 95,
                "power_on_hours": 60000,
            },
        }
        warnings = sm._extract_warnings(
            payload, drive_id="/dev/nvme0", config=self._config(),
        )
        attr_names = {w["attribute_name"] for w in warnings}
        assert "nvme_percentage_used" in attr_names
        assert "nvme_power_on_hours" in attr_names


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
                "drives": {
                    "/dev/sda": {"status": "ok"},
                },
            }

        import brain.smart_monitor as _sm_mod
        original = _sm_mod.run_smart_monitor_probe
        _sm_mod.run_smart_monitor_probe = fake_probe  # type: ignore[assignment]
        try:
            probe = sm.SmartMonitorProbe()
            result = await probe.check(pool, {})
        finally:
            _sm_mod.run_smart_monitor_probe = original  # type: ignore[assignment]

        assert result.ok is True
        assert result.detail == "fake"
        assert result.metrics["status"] == "ok"
        assert result.metrics["drives"]["/dev/sda"] == "ok"
