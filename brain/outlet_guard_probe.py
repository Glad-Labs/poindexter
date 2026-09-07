"""Outlet guard — restore the PC's own wall outlet when its smart plug opens.

Earned 2026-09-06. The Shelly plug that meters the PC's wall power
(``shelly_psu_url`` → ``psu_total_power_watts``) opened its relay at 03:04 EDT
with mains still present on its input. The UPS behind it carried the PC on
battery for 18 minutes, NUT then shut the host down cleanly, and it stayed off
for 12 hours because the plug's ``initial_state`` was ``off``. Every signal was
green the whole time: the brain reported 0 issues at 03:21 with the battery at
21%, because nothing looked at *whether our own outlet was switched on*.

The plug is a Gen2+ Shelly with a local RPC, and the brain was alive on
battery for those 18 minutes — it could have turned the outlet back on with
one call. This probe is that call, gated three ways so it can never fight a
human or a real outage:

1. **Mains must be present** on the plug's input (``voltage`` ≥
   ``outlet_guard_min_line_voltage_volts``). A relay that is open with 0 V on
   its input is a real outage; there is nothing to restore.
2. **The UPS must be on battery** (``network_ups_tools_ups_status{flag="OB"}``
   from the NUT exporter), unless ``outlet_guard_require_ups_on_battery`` is
   ``false``. On battery + our metered outlet open = *we are running on the
   UPS behind that outlet*. On line + outlet open = the plug is metering
   something that isn't our supply, so restoring it is not ours to decide —
   page instead. Unknown (exporter unreachable) is treated as **not
   confirmed**: page, don't act (fail-closed, per feedback_no_silent_defaults).
3. **Bounded** by ``outlet_guard_restore_cap_per_window`` per
   ``outlet_guard_restore_window_minutes`` so a relay that keeps dropping
   escalates to a page instead of a restore loop.

Before commanding the relay the probe snapshots the plug's ``source`` (who
issued the last switch change: ``button`` / ``cloud`` / ``matter`` / ``init``
…), ``errors`` (``overtemp`` / ``overpower`` …) and temperature, and carries
them in the alert. ``Switch.Set`` overwrites ``source``, so this snapshot is
the only forensic record of *why* the outlet opened — the 2026-09-06 event
lost exactly that field to the operator's button press.

Why the brain and not the worker: the worker rents the same UPS; the brain is
the standalone watchdog that only needs Python + asyncpg + httpx, and it is
already the thing that pages when the UPS transfers.

The plug's address rides in on ``SHELLY_PSU_URL`` — the same env var the
``gpu-exporter`` service reads, exported by ``start-stack.sh`` from the
``shelly_psu_url`` bootstrap key, so the value keeps a single home. Unset =
probe no-ops (an install without a smart plug has nothing to guard).

Design parity with brain/mcp_http_probe.py (fail-closed kill-switch, capped
recovery, alert_events writer).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable
from typing import Any

try:  # pragma: no cover — only fails when the dep is uninstalled
    import httpx
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

try:  # Flat import when brain/ is on sys.path (container runtime).
    from secret_reader import read_app_setting as _read_app_setting
except ImportError:  # pragma: no cover — package-qualified path
    from brain.secret_reader import read_app_setting as _read_app_setting

logger = logging.getLogger("brain.outlet_guard_probe")


# ---------------------------------------------------------------------------
# App_settings keys.
# ---------------------------------------------------------------------------

ENABLED_KEY = "outlet_guard_enabled"
MIN_LINE_VOLTAGE_KEY = "outlet_guard_min_line_voltage_volts"
REQUIRE_UPS_ON_BATTERY_KEY = "outlet_guard_require_ups_on_battery"
UPS_METRICS_URL_KEY = "outlet_guard_ups_metrics_url"
HTTP_TIMEOUT_SECONDS_KEY = "outlet_guard_http_timeout_seconds"
RESTORE_CAP_KEY = "outlet_guard_restore_cap_per_window"
RESTORE_WINDOW_MINUTES_KEY = "outlet_guard_restore_window_minutes"
SWITCH_ID_KEY = "outlet_guard_switch_id"
DEDUP_HOURS_KEY = "outlet_guard_dedup_hours"

DEFAULT_ENABLED = True
DEFAULT_MIN_LINE_VOLTAGE = 90.0
DEFAULT_REQUIRE_UPS_ON_BATTERY = True
# The NUT exporter (docker-compose ``nut-exporter``, profile ``ups``) serves the
# UPS variables on /ups_metrics?ups=<name>; /metrics is only its Go runtime.
# Same address + name the Prometheus ``nut`` job scrapes.
DEFAULT_UPS_METRICS_URL = "http://host.docker.internal:9199/ups_metrics?ups=cyberpower"
DEFAULT_HTTP_TIMEOUT_SECONDS = 3
DEFAULT_RESTORE_CAP = 3
DEFAULT_RESTORE_WINDOW_MINUTES = 60
DEFAULT_SWITCH_ID = 0
DEFAULT_DEDUP_HOURS = 6

SHELLY_URL_ENV = "SHELLY_PSU_URL"

ALERT_RESTORED = "outlet_guard_restored"
ALERT_RESTORE_FAILED = "outlet_guard_restore_failed"
ALERT_NOT_RESTORED = "outlet_guard_outlet_off_not_restored"


# ---------------------------------------------------------------------------
# Module state.
# ---------------------------------------------------------------------------

_restore_attempts: list[float] = []  # monotonic timestamps within the rolling window
_last_alert_at: dict[str, float] = {}  # alertname -> monotonic time of last write


def _reset_state() -> None:
    """Test hook — drop module-level cap + dedup state."""
    _restore_attempts.clear()
    _last_alert_at.clear()


# ---------------------------------------------------------------------------
# Tunable readers (same fail-closed contract as mcp_http_probe).
# ---------------------------------------------------------------------------

_UNSET = "\x00brain.outlet_guard_probe._UNSET\x00"


async def _read_bool(pool, key: str, default: bool, *, fail_closed: bool = False) -> bool:
    raw = await _read_app_setting(pool, key, _UNSET)
    if raw == _UNSET:
        if fail_closed:
            logger.warning(
                "[OUTLET_GUARD] %s unreadable (row missing or DB error); "
                "fail-closed → treating as disabled",
                key,
            )
            return False
        return default
    raw_norm = str(raw).strip().lower()
    if raw_norm in {"1", "true", "yes", "on"}:
        return True
    if raw_norm in {"0", "false", "no", "off"}:
        return False
    logger.warning(
        "[OUTLET_GUARD] %s has unparseable bool value %r; treating as %s",
        key, raw,
        "disabled (fail-closed)" if fail_closed else f"default ({default})",
    )
    return False if fail_closed else default


async def _read_int(pool, key: str, default: int) -> int:
    raw = await _read_app_setting(pool, key, "")
    if not raw:
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        logger.warning("[OUTLET_GUARD] %s is not an integer (%r); using %d", key, raw, default)
        return default


async def _read_float(pool, key: str, default: float) -> float:
    raw = await _read_app_setting(pool, key, "")
    if not raw:
        return default
    try:
        return float(str(raw).strip())
    except ValueError:
        logger.warning("[OUTLET_GUARD] %s is not a number (%r); using %s", key, raw, default)
        return default


def resolve_shelly_url() -> str:
    """Plug base URL from ``SHELLY_PSU_URL`` (compose passes the bootstrap key
    through, exactly as it does for the gpu-exporter). Empty = unconfigured."""
    return os.environ.get(SHELLY_URL_ENV, "").strip().rstrip("/")


# ---------------------------------------------------------------------------
# UPS status from the NUT exporter exposition.
# ---------------------------------------------------------------------------

_UPS_STATUS_RE = re.compile(
    r'^network_ups_tools_ups_status\{[^}]*flag="(?P<flag>[A-Z]+)"[^}]*\}\s+(?P<val>[0-9.eE+-]+)',
    re.MULTILINE,
)
_UPS_SCALAR_RE = re.compile(
    r"^network_ups_tools_(?P<name>battery_charge|battery_runtime|input_voltage)(?:\{[^}]*\})?\s+(?P<val>[0-9.eE+-]+)",
    re.MULTILINE,
)


def parse_ups_exposition(text: str) -> dict[str, Any]:
    """Pull the on-battery flag + a few battery scalars out of nut_exporter text.

    Returns ``{"on_battery": bool | None, ...scalars}``. ``on_battery`` is
    ``None`` when the exposition carries no ``ups_status`` series at all
    (exporter up but driver lost the UPS) — callers must treat that as
    *unknown*, never as "on line".
    """
    flags: dict[str, float] = {}
    for m in _UPS_STATUS_RE.finditer(text):
        try:
            flags[m.group("flag")] = float(m.group("val"))
        except ValueError:
            continue
    out: dict[str, Any] = {"on_battery": None}
    if flags:
        out["on_battery"] = flags.get("OB", 0.0) >= 1.0
        out["on_line"] = flags.get("OL", 0.0) >= 1.0
    for m in _UPS_SCALAR_RE.finditer(text):
        try:
            out[m.group("name")] = float(m.group("val"))
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# alert_events + audit_log writers.
# ---------------------------------------------------------------------------


async def _write_alert(
    pool,
    *,
    now: float,
    alertname: str,
    severity: str,
    title: str,
    body: str,
    details: dict[str, Any],
    dedup_s: float,
) -> bool:
    """Insert one ``alert_events`` row (AlertManager-shaped, same as the other
    brain probes) unless the same alertname fired inside the dedup window.

    Severity picks the channel in the dispatcher's matrix: ``critical`` pages
    Telegram + Discord (a restored outlet is a near-miss of a 12-hour outage
    and the operator must know), ``warning`` is Discord-only.
    """
    last = _last_alert_at.get(alertname)
    if last is not None and (now - last) < dedup_s:
        logger.warning("[OUTLET_GUARD] %s suppressed by dedup window: %s", alertname, body)
        return False
    labels = json.dumps({"probe": "outlet_guard_probe"})
    annotations = json.dumps({"summary": title, "description": body, **details}, default=str)
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                alertname, status, severity, category,
                labels, annotations, fingerprint
            ) VALUES (
                $1, 'firing', $2, 'infrastructure',
                $3::jsonb, $4::jsonb, $5
            )
            """,
            alertname,
            severity,
            labels,
            annotations,
            f"outlet_guard_probe:{alertname}",
        )
        _last_alert_at[alertname] = now
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OUTLET_GUARD] alert_events write failed: %s", exc)
        return False


async def _write_audit(pool, status: str, details: dict[str, Any], severity: str) -> None:
    """Durable footprint for anything the guard did or refused to do. Steady
    state (outlet on) leaves no row — that signal lives in the exporter's
    ``psu_outlet_output_on`` gauge instead."""
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity) "
            "VALUES ($1, $2, $3::jsonb, $4)",
            "outlet_guard",
            "brain.outlet_guard_probe",
            json.dumps({"status": status, **details}, default=str),
            severity,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OUTLET_GUARD] audit_log write failed (non-critical): %s", exc)


# ---------------------------------------------------------------------------
# Shelly RPC helpers.
# ---------------------------------------------------------------------------


def _snapshot(status: dict[str, Any]) -> dict[str, Any]:
    """The forensic fields worth carrying in an alert, captured BEFORE
    ``Switch.Set`` overwrites ``source``."""
    temp = status.get("temperature")
    return {
        "output": status.get("output"),
        "voltage": status.get("voltage"),
        "apower": status.get("apower"),
        "source": status.get("source"),
        "errors": status.get("errors"),
        "temperature_c": temp.get("tC") if isinstance(temp, dict) else None,
    }


async def _get_switch_status(client, base_url: str, switch_id: int) -> dict[str, Any]:
    resp = await client.get(f"{base_url}/rpc/Switch.GetStatus", params={"id": switch_id})
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise ValueError(f"Switch.GetStatus returned non-object: {type(data).__name__}")
    return data


async def _set_switch_on(client, base_url: str, switch_id: int) -> Any:
    # JSON-RPC POST: the GET form 500s on nested params on Gen4 firmware.
    resp = await client.post(
        f"{base_url}/rpc",
        json={"id": 1, "method": "Switch.Set", "params": {"id": switch_id, "on": True}},
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Probe entry point.
# ---------------------------------------------------------------------------


async def run_outlet_guard_probe(
    pool,
    *,
    now_fn: Callable[[], float] | None = None,
    http_client_factory: Callable[..., Any] | None = None,
    shelly_url: str | None = None,
) -> dict[str, Any]:
    """Single probe cycle. Returns a summary dict.

    ``status`` values: ``disabled`` / ``unconfigured`` / ``no_httpx`` /
    ``unreachable`` / ``outlet_on`` / ``mains_absent`` / ``ups_not_confirmed``
    / ``not_our_supply`` / ``cap_reached`` / ``restored`` / ``restore_failed``.
    """
    clock = now_fn or time.monotonic
    now = clock()

    enabled = await _read_bool(pool, ENABLED_KEY, DEFAULT_ENABLED, fail_closed=True)
    if not enabled:
        return {"ok": True, "status": "disabled", "detail": "outlet_guard disabled"}

    base_url = (shelly_url if shelly_url is not None else resolve_shelly_url()).rstrip("/")
    if not base_url:
        return {
            "ok": True,
            "status": "unconfigured",
            "detail": f"{SHELLY_URL_ENV} unset — no smart plug to guard",
        }

    if httpx is None and http_client_factory is None:
        logger.warning("[OUTLET_GUARD] httpx not installed — skipping")
        return {"ok": False, "status": "no_httpx", "detail": "httpx not available"}

    timeout_s = await _read_int(pool, HTTP_TIMEOUT_SECONDS_KEY, DEFAULT_HTTP_TIMEOUT_SECONDS)
    switch_id = await _read_int(pool, SWITCH_ID_KEY, DEFAULT_SWITCH_ID)
    _httpx: Any = httpx
    factory = http_client_factory or (lambda: _httpx.AsyncClient(timeout=timeout_s))

    # ---- 1. Read the plug. Unreachable is NOT this probe's alarm: the meter's
    # liveness is owned by the psu watchdog + data-freshness probe.
    try:
        async with factory() as client:
            status = await _get_switch_status(client, base_url, switch_id)
    except Exception as exc:  # noqa: BLE001
        detail = f"Switch.GetStatus failed: {type(exc).__name__}: {exc}"
        logger.warning("[OUTLET_GUARD] %s", detail)
        return {"ok": True, "status": "unreachable", "detail": detail}

    snap = _snapshot(status)
    if snap["output"] is True:
        return {"ok": True, "status": "outlet_on", "detail": "outlet on", **snap}

    # ---- 2. Outlet is OFF. Is mains even there?
    min_v = await _read_float(pool, MIN_LINE_VOLTAGE_KEY, DEFAULT_MIN_LINE_VOLTAGE)
    voltage = snap.get("voltage")
    try:
        voltage_f = float(voltage) if voltage is not None else 0.0
    except (TypeError, ValueError):
        voltage_f = 0.0
    if voltage_f < min_v:
        detail = f"outlet off with {voltage_f:.1f} V on input (< {min_v:g} V): real outage, nothing to restore"
        logger.warning("[OUTLET_GUARD] %s", detail)
        return {"ok": True, "status": "mains_absent", "detail": detail, **snap}

    dedup_s = max(1, await _read_int(pool, DEDUP_HOURS_KEY, DEFAULT_DEDUP_HOURS)) * 3600

    # ---- 3. Confirm we are the load behind it: UPS must be on battery.
    require_ups = await _read_bool(pool, REQUIRE_UPS_ON_BATTERY_KEY, DEFAULT_REQUIRE_UPS_ON_BATTERY)
    ups: dict[str, Any] = {"on_battery": None}
    if require_ups:
        ups_url = (await _read_app_setting(pool, UPS_METRICS_URL_KEY, "")).strip() or DEFAULT_UPS_METRICS_URL
        try:
            async with factory() as client:
                resp = await client.get(ups_url)
                resp.raise_for_status()
                ups = parse_ups_exposition(resp.text)
        except Exception as exc:  # noqa: BLE001
            ups = {"on_battery": None, "error": f"{type(exc).__name__}: {exc}"}

        if ups.get("on_battery") is None:
            detail = (
                f"outlet off with {voltage_f:.1f} V present, but UPS state is unknown "
                f"({ups.get('error') or 'no ups_status series'}) — not restoring (fail-closed)"
            )
            logger.error("[OUTLET_GUARD] %s", detail)
            await _write_alert(
                pool, now=now, alertname=ALERT_NOT_RESTORED, severity="warning",
                title="Metered outlet is OFF and the guard cannot confirm the UPS",
                body=detail, details={"plug": snap, "ups": ups}, dedup_s=dedup_s,
            )
            await _write_audit(pool, "ups_not_confirmed", {"plug": snap, "ups": ups}, "warning")
            return {"ok": False, "status": "ups_not_confirmed", "detail": detail, **snap, "ups": ups}

        if not ups["on_battery"]:
            detail = (
                f"outlet off with {voltage_f:.1f} V present, UPS on line — the plug is not "
                "metering our supply; not restoring"
            )
            logger.warning("[OUTLET_GUARD] %s", detail)
            await _write_alert(
                pool, now=now, alertname=ALERT_NOT_RESTORED, severity="warning",
                title="Metered outlet is OFF but the UPS is on line",
                body=detail, details={"plug": snap, "ups": ups}, dedup_s=dedup_s,
            )
            await _write_audit(pool, "not_our_supply", {"plug": snap, "ups": ups}, "warning")
            return {"ok": True, "status": "not_our_supply", "detail": detail, **snap, "ups": ups}

    # ---- 4. Restore, bounded.
    cap = max(1, await _read_int(pool, RESTORE_CAP_KEY, DEFAULT_RESTORE_CAP))
    window_min = max(1, await _read_int(pool, RESTORE_WINDOW_MINUTES_KEY, DEFAULT_RESTORE_WINDOW_MINUTES))
    cutoff = now - window_min * 60
    _restore_attempts[:] = [t for t in _restore_attempts if t >= cutoff]
    if len(_restore_attempts) >= cap:
        detail = (
            f"outlet off again but restore cap reached ({len(_restore_attempts)}/{cap} in "
            f"{window_min}m) — relay keeps dropping; operator needed"
        )
        logger.error("[OUTLET_GUARD] %s", detail)
        await _write_alert(
            pool, now=now, alertname=ALERT_RESTORE_FAILED, severity="critical",
            title="Metered outlet keeps switching off — restore cap reached",
            body=detail, details={"plug": snap, "ups": ups}, dedup_s=dedup_s,
        )
        await _write_audit(pool, "cap_reached", {"plug": snap, "ups": ups}, "critical")
        return {"ok": False, "status": "cap_reached", "detail": detail, **snap, "ups": ups}

    _restore_attempts.append(now)
    try:
        async with factory() as client:
            await _set_switch_on(client, base_url, switch_id)
            after = await _get_switch_status(client, base_url, switch_id)
    except Exception as exc:  # noqa: BLE001
        detail = f"Switch.Set on failed: {type(exc).__name__}: {exc}"
        logger.error("[OUTLET_GUARD] %s", detail)
        await _write_alert(
            pool, now=now, alertname=ALERT_RESTORE_FAILED, severity="critical",
            title="Metered outlet is OFF and the restore command failed",
            body=detail, details={"plug": snap, "ups": ups}, dedup_s=dedup_s,
        )
        await _write_audit(pool, "restore_failed", {"plug": snap, "ups": ups, "error": detail}, "critical")
        return {"ok": False, "status": "restore_failed", "detail": detail, **snap, "ups": ups}

    if after.get("output") is not True:
        detail = "Switch.Set accepted but the outlet still reads off — relay locked out (overtemp/overpower?)"
        logger.error("[OUTLET_GUARD] %s errors=%s", detail, after.get("errors"))
        await _write_alert(
            pool, now=now, alertname=ALERT_RESTORE_FAILED, severity="critical",
            title="Metered outlet would not come back on",
            body=detail, details={"plug": snap, "after": _snapshot(after), "ups": ups}, dedup_s=dedup_s,
        )
        await _write_audit(pool, "restore_failed", {"plug": snap, "after": _snapshot(after), "ups": ups}, "critical")
        return {"ok": False, "status": "restore_failed", "detail": detail, **snap, "ups": ups}

    detail = (
        f"outlet was OFF with {voltage_f:.1f} V present while the UPS was on battery "
        f"(charge={ups.get('battery_charge')}%, runtime={ups.get('battery_runtime')}s); "
        f"turned it back on. Last switch source before restore: {snap.get('source')!r}"
    )
    logger.error("[OUTLET_GUARD] %s", detail)
    await _write_alert(
        pool, now=now, alertname=ALERT_RESTORED, severity="critical",
        title="Metered outlet was OFF — restored by the brain",
        body=detail, details={"plug": snap, "ups": ups}, dedup_s=dedup_s,
    )
    await _write_audit(pool, "restored", {"plug": snap, "ups": ups}, "critical")
    return {"ok": True, "status": "restored", "detail": detail, **snap, "ups": ups}
