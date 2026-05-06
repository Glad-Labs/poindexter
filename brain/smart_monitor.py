"""SMART drive-health monitoring probe (Glad-Labs/poindexter#387).

Backups (#385) protect against data loss after the fact. SMART
monitoring catches the *upcoming* failure before the drive actually
dies — giving the operator time to copy data off and replace.

This stack runs on user PCs where hardware failure is the dominant
risk vs cloud, and most users never read SMART logs. The brain reads
them on a schedule and pages the operator via Telegram + Discord (the
same alert_dispatcher pipeline backup_watcher uses).

The probe runs ``smartctl --scan-open --json`` to enumerate drives,
then ``smartctl -a --json /dev/<drive>`` per drive. It flags:

================================  =============  =========
Attribute                         Threshold      Severity
================================  =============  =========
``Reallocated_Sector_Ct``         > 0            warning
``Current_Pending_Sector``        > 0            warning
``Wear_Leveling_Count``           > 90% used     warning
``Power_On_Hours``                > 50000        info
SMART self-test                   FAILED         critical
================================  =============  =========

Every threshold is a ``app_settings`` row so an operator can tune
without redeploying — see migration ``20260506_054712_*``.

Cross-platform: smartctl ships on Linux/macOS via package managers
and on Windows via the smartmontools MSI installer. If smartctl isn't
installed the probe degrades gracefully — one notify-on-startup
warning, then ``status='skipped'`` cycles forever (no crash, no
per-cycle noise).

Design parity with ``brain/backup_watcher.py``:

- DB-configurable through ``app_settings`` — every tunable is a row.
- Standalone module: only stdlib + asyncpg.
- Subprocess calls degrade gracefully (logged, not raised).
- Per-(drive, attribute) dedup state lives in a module-level dict so
  re-fires are suppressed within ``smart_monitor_alert_dedup_minutes``.
- Resolution detection — when an attribute clears between cycles, a
  ``status='resolved'`` ``alert_events`` row is written so the
  dispatcher pages "[RESOLVED]".
- Per-drive exception isolation — one drive's smartctl failure does
  not skip the next drive.
- Subprocess uses ``CREATE_NO_WINDOW`` on win32 (Matt's "no popup
  windows" rule).

Auto-resolve mechanism: writing a fresh ``alert_events`` row with
``status='resolved'`` and the same ``alertname`` is the contract the
existing dispatcher already speaks (Alertmanager status semantics;
the brain's ``_format_alert_message`` renders the header as
``[RESOLVED · ...]``).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import time
from typing import Any, Callable, Optional

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified for tests
    from brain.operator_notifier import notify_operator

logger = logging.getLogger("brain.smart_monitor")


# ---------------------------------------------------------------------------
# App_settings keys — every tunable lives in the DB so an operator can
# adjust without redeploying. See migration 20260506_054712_*.
# ---------------------------------------------------------------------------

ENABLED_KEY = "smart_monitor_enabled"
POLL_INTERVAL_HOURS_KEY = "smart_monitor_poll_interval_hours"
DRIVE_FILTER_KEY = "smart_monitor_drive_filter"
REALLOCATED_THRESHOLD_KEY = "smart_monitor_reallocated_sector_threshold"
CURRENT_PENDING_THRESHOLD_KEY = "smart_monitor_current_pending_threshold"
WEAR_LEVELING_WARN_PERCENT_KEY = "smart_monitor_wear_leveling_warn_percent"
POWER_ON_HOURS_INFO_THRESHOLD_KEY = "smart_monitor_power_on_hours_info_threshold"
SMARTCTL_PATH_KEY = "smart_monitor_smartctl_path"
ALERT_DEDUP_MINUTES_KEY = "smart_monitor_alert_dedup_minutes"

DEFAULT_ENABLED = True
DEFAULT_POLL_INTERVAL_HOURS = 6
DEFAULT_DRIVE_FILTER: Optional[str] = None
DEFAULT_REALLOCATED_THRESHOLD = 0
DEFAULT_CURRENT_PENDING_THRESHOLD = 0
DEFAULT_WEAR_LEVELING_WARN_PERCENT = 90
DEFAULT_POWER_ON_HOURS_INFO_THRESHOLD = 50000
DEFAULT_SMARTCTL_PATH: Optional[str] = None
DEFAULT_ALERT_DEDUP_MINUTES = 360

# Subprocess timeout for smartctl invocations. ``smartctl --scan-open``
# is fast (<1s); ``smartctl -a`` per drive can stall briefly on a sick
# drive but 30s is generous enough to never wedge a brain cycle.
SMARTCTL_TIMEOUT_SECONDS = 30

# How long the probe is willing to take in a single cycle. With the
# default ``poll_interval_hours=6`` the brain runs this once per ~72
# brain cycles (5-min cycles); the per-poll runtime is roughly
# 1s * len(drives), so a worst-case 8-drive box still finishes in <30s.
PROBE_INTERVAL_SECONDS = 6 * 3600  # 6 h, mirroring the default setting


# ---------------------------------------------------------------------------
# Module-level dedup bookkeeping — per-(drive_id, attribute_name) the
# timestamp of the last firing alert. Suppresses re-fires within the
# dedup window. Cleared when the attribute clears (resolution path).
# ---------------------------------------------------------------------------

_alert_dedup_state: dict[tuple[str, str], float] = {}

# Per-(drive_id, attribute_name) the last *firing* state — used to
# detect transitions back to healthy so we can write a resolved row.
# Value is the alertname so the resolution INSERT can mirror it.
_firing_state: dict[tuple[str, str], str] = {}

# One-shot flag — log "smartctl missing" exactly once per process so
# we don't spam notify_operator every cycle. notify_operator() is
# called once when the absence is first detected.
_smartctl_missing_notified: bool = False


def _reset_dedup_state() -> None:
    """Test helper — wipe the dedup + firing-state maps."""
    _alert_dedup_state.clear()
    _firing_state.clear()
    global _smartctl_missing_notified
    _smartctl_missing_notified = False


# ---------------------------------------------------------------------------
# app_settings reads — brain is standalone so we hit the DB directly.
# Each helper degrades to its default when the row is missing or the
# fetch raises, mirroring the pattern in brain/backup_watcher.py.
# ---------------------------------------------------------------------------


async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SMART_MONITOR] Could not read %s from app_settings: %s "
            "— using default %r", key, exc, default,
        )
        return default
    if val is None:
        return default
    return val


def _coerce_bool(val: Any, default: bool) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("true", "1", "yes", "on")


def _coerce_int(val: Any, default: int) -> int:
    if val is None:
        return default
    try:
        return int(str(val).strip())
    except (TypeError, ValueError):
        return default


async def _read_config(pool: Any) -> dict[str, Any]:
    """Pull every probe tunable in one helper.

    Returns a dict with all nine settings resolved + coerced. Cheap
    because the brain pool is local to the same Postgres instance.
    """
    enabled = _coerce_bool(
        await _read_setting(pool, ENABLED_KEY, "true"),
        DEFAULT_ENABLED,
    )
    poll_interval_hours = _coerce_int(
        await _read_setting(pool, POLL_INTERVAL_HOURS_KEY, DEFAULT_POLL_INTERVAL_HOURS),
        DEFAULT_POLL_INTERVAL_HOURS,
    )
    drive_filter_raw = await _read_setting(pool, DRIVE_FILTER_KEY, DEFAULT_DRIVE_FILTER)
    if drive_filter_raw is None or str(drive_filter_raw).strip() == "":
        drive_filter: Optional[list[str]] = None
    else:
        drive_filter = [
            piece.strip() for piece in str(drive_filter_raw).split(",")
            if piece.strip()
        ] or None
    reallocated_threshold = _coerce_int(
        await _read_setting(pool, REALLOCATED_THRESHOLD_KEY, DEFAULT_REALLOCATED_THRESHOLD),
        DEFAULT_REALLOCATED_THRESHOLD,
    )
    current_pending_threshold = _coerce_int(
        await _read_setting(pool, CURRENT_PENDING_THRESHOLD_KEY, DEFAULT_CURRENT_PENDING_THRESHOLD),
        DEFAULT_CURRENT_PENDING_THRESHOLD,
    )
    wear_leveling_warn_percent = _coerce_int(
        await _read_setting(pool, WEAR_LEVELING_WARN_PERCENT_KEY, DEFAULT_WEAR_LEVELING_WARN_PERCENT),
        DEFAULT_WEAR_LEVELING_WARN_PERCENT,
    )
    power_on_hours_threshold = _coerce_int(
        await _read_setting(pool, POWER_ON_HOURS_INFO_THRESHOLD_KEY, DEFAULT_POWER_ON_HOURS_INFO_THRESHOLD),
        DEFAULT_POWER_ON_HOURS_INFO_THRESHOLD,
    )
    smartctl_path_raw = await _read_setting(pool, SMARTCTL_PATH_KEY, DEFAULT_SMARTCTL_PATH)
    if smartctl_path_raw is None or str(smartctl_path_raw).strip() == "":
        smartctl_path: Optional[str] = None
    else:
        smartctl_path = str(smartctl_path_raw).strip()
    alert_dedup_minutes = _coerce_int(
        await _read_setting(pool, ALERT_DEDUP_MINUTES_KEY, DEFAULT_ALERT_DEDUP_MINUTES),
        DEFAULT_ALERT_DEDUP_MINUTES,
    )

    return {
        "enabled": enabled,
        "poll_interval_hours": poll_interval_hours,
        "drive_filter": drive_filter,
        "reallocated_threshold": reallocated_threshold,
        "current_pending_threshold": current_pending_threshold,
        "wear_leveling_warn_percent": wear_leveling_warn_percent,
        "power_on_hours_threshold": power_on_hours_threshold,
        "smartctl_path": smartctl_path,
        "alert_dedup_minutes": alert_dedup_minutes,
    }


# ---------------------------------------------------------------------------
# smartctl invocation — wraps subprocess.run with the platform-specific
# CREATE_NO_WINDOW flag so Windows hosts don't flash a console window.
# ---------------------------------------------------------------------------


def _resolve_smartctl_path(override: Optional[str]) -> Optional[str]:
    """Return the smartctl binary path or ``None`` if not installed.

    Honours the operator's ``smart_monitor_smartctl_path`` override
    first (useful when smartmontools is installed somewhere unusual),
    then falls back to ``shutil.which("smartctl")``.
    """
    if override:
        if os.path.isfile(override):
            return override
        logger.warning(
            "[SMART_MONITOR] smart_monitor_smartctl_path=%r does not exist; "
            "falling back to PATH lookup.", override,
        )
    return shutil.which("smartctl")


def _run_smartctl(
    binary: str,
    args: list[str],
) -> tuple[int, Optional[dict[str, Any]], str]:
    """Invoke ``smartctl`` and parse the JSON payload.

    smartctl returns non-zero on any concerning attribute (that's its
    whole job) but still emits a complete JSON document on stdout. We
    therefore *always* try to parse stdout when the JSON looks intact,
    and only treat the call as failed when JSON parsing also fails.

    Returns ``(exit_code, parsed_json_or_None, stderr_tail)``.
    """
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": SMARTCTL_TIMEOUT_SECONDS,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run([binary, *args], **kwargs)
        stdout = result.stdout or ""
        stderr_tail = (result.stderr or "").strip()[:200]
        parsed: Optional[dict[str, Any]] = None
        if stdout.strip():
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = None
        return result.returncode, parsed, stderr_tail
    except FileNotFoundError:
        return -1, None, "smartctl binary not found"
    except subprocess.TimeoutExpired:
        return -1, None, f"smartctl timed out after {SMARTCTL_TIMEOUT_SECONDS}s"
    except Exception as exc:  # noqa: BLE001
        return -1, None, f"smartctl error: {type(exc).__name__}: {str(exc)[:160]}"


def _scan_drives(
    binary: str,
    *,
    run_fn: Callable[[str, list[str]], tuple[int, Optional[dict[str, Any]], str]],
) -> list[dict[str, Any]]:
    """Enumerate drives via ``smartctl --scan-open --json``.

    Returns a list of ``{"name": "/dev/sda", "type": "sat", ...}``
    dicts as smartctl reports them. Empty list on any error (logged).
    """
    rc, parsed, stderr_tail = run_fn(binary, ["--scan-open", "--json"])
    if parsed is None:
        logger.warning(
            "[SMART_MONITOR] smartctl --scan-open failed (rc=%s): %s",
            rc, stderr_tail,
        )
        return []
    devices = parsed.get("devices") or []
    if not isinstance(devices, list):
        return []
    out: list[dict[str, Any]] = []
    for d in devices:
        if isinstance(d, dict) and d.get("name"):
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# Attribute extraction — pull the structured JSON smartctl emits and
# turn it into a small list of warning records the alert pipeline can
# act on. Pure function (no DB, no subprocess) — easy to unit test.
# ---------------------------------------------------------------------------


def _extract_warnings(
    payload: dict[str, Any],
    *,
    drive_id: str,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Walk a parsed ``smartctl -a --json`` payload and build a list
    of warning records.

    Each record is a self-contained dict with everything the
    ``alert_events`` writer needs: severity, attribute name, observed
    value, threshold, human-readable detail. Resolution detection
    happens at the caller; this function is stateless.
    """
    warnings: list[dict[str, Any]] = []

    # 1) SMART self-test result (critical).
    self_test = (
        payload.get("smart_status") or {}
    ).get("passed")
    # smartctl exposes the simple pass/fail at smart_status.passed
    # (true|false). When false, the drive is reporting overall SMART
    # failure — that's the upcoming-death signal we most care about.
    if self_test is False:
        warnings.append({
            "severity": "critical",
            "attribute_name": "smart_status_passed",
            "observed_value": False,
            "threshold": "true (passed)",
            "detail": (
                f"SMART self-test FAILED on drive {drive_id}. "
                f"Drive is reporting overall failure — replace as soon "
                f"as possible and copy critical data off first."
            ),
        })

    # 2) Per-attribute table — drives expose this as
    # ata_smart_attributes.table[]. Each entry has ``id``, ``name``,
    # ``raw.value`` (int) and ``value`` (normalized 0-255).
    table = (
        (payload.get("ata_smart_attributes") or {}).get("table") or []
    )
    realloc_threshold = int(config["reallocated_threshold"])
    pending_threshold = int(config["current_pending_threshold"])
    wear_warn_pct = int(config["wear_leveling_warn_percent"])
    power_on_threshold = int(config["power_on_hours_threshold"])

    for attr in table:
        if not isinstance(attr, dict):
            continue
        name = (attr.get("name") or "").strip()
        if not name:
            continue
        raw = attr.get("raw") or {}
        try:
            raw_value = int(raw.get("value", 0))
        except (TypeError, ValueError):
            raw_value = 0
        try:
            normalized = int(attr.get("value", 0))
        except (TypeError, ValueError):
            normalized = 0

        if name == "Reallocated_Sector_Ct" and raw_value > realloc_threshold:
            warnings.append({
                "severity": "warning",
                "attribute_name": name,
                "observed_value": raw_value,
                "threshold": realloc_threshold,
                "detail": (
                    f"Drive {drive_id} has {raw_value} reallocated "
                    f"sector(s) (threshold > {realloc_threshold}). "
                    f"Reallocations indicate physical media wear — "
                    f"begin replacement planning."
                ),
            })

        elif name == "Current_Pending_Sector" and raw_value > pending_threshold:
            warnings.append({
                "severity": "warning",
                "attribute_name": name,
                "observed_value": raw_value,
                "threshold": pending_threshold,
                "detail": (
                    f"Drive {drive_id} has {raw_value} sector(s) "
                    f"pending reallocation (threshold > {pending_threshold}). "
                    f"Pending sectors precede reallocations — the drive "
                    f"is failing slowly."
                ),
            })

        elif name == "Wear_Leveling_Count":
            # SSD lifetime: smartctl exposes the normalized value as
            # remaining-life on a 0-100 scale (some vendors 0-255 — we
            # only treat the documented 0-100 case as actionable).
            # Used-life % = 100 - normalized when 0<=normalized<=100.
            if 0 <= normalized <= 100:
                used_pct = 100 - normalized
                if used_pct > wear_warn_pct:
                    warnings.append({
                        "severity": "warning",
                        "attribute_name": name,
                        "observed_value": used_pct,
                        "threshold": wear_warn_pct,
                        "detail": (
                            f"SSD {drive_id} has used {used_pct}% of "
                            f"its rated write endurance (threshold > "
                            f"{wear_warn_pct}%). Plan a replacement."
                        ),
                    })

        elif name == "Power_On_Hours" and raw_value > power_on_threshold:
            warnings.append({
                "severity": "info",
                "attribute_name": name,
                "observed_value": raw_value,
                "threshold": power_on_threshold,
                "detail": (
                    f"Drive {drive_id} has accumulated {raw_value} "
                    f"power-on hours (threshold > {power_on_threshold}). "
                    f"FYI for replacement planning — not an emergency."
                ),
            })

    # 3) NVMe SMART log path — mirrors the ATA path but lives at
    # nvme_smart_health_information_log. We map the relevant fields
    # back onto the same warning records so the dispatcher renders
    # them uniformly.
    nvme = payload.get("nvme_smart_health_information_log")
    if isinstance(nvme, dict):
        # NVMe percentage_used is direct used-life in %.
        try:
            used_pct = int(nvme.get("percentage_used", 0))
        except (TypeError, ValueError):
            used_pct = 0
        if used_pct > wear_warn_pct:
            warnings.append({
                "severity": "warning",
                "attribute_name": "nvme_percentage_used",
                "observed_value": used_pct,
                "threshold": wear_warn_pct,
                "detail": (
                    f"NVMe {drive_id} has used {used_pct}% of its rated "
                    f"write endurance (threshold > {wear_warn_pct}%). "
                    f"Plan a replacement."
                ),
            })

        try:
            poh = int(nvme.get("power_on_hours", 0))
        except (TypeError, ValueError):
            poh = 0
        if poh > power_on_threshold:
            warnings.append({
                "severity": "info",
                "attribute_name": "nvme_power_on_hours",
                "observed_value": poh,
                "threshold": power_on_threshold,
                "detail": (
                    f"NVMe {drive_id} has accumulated {poh} power-on "
                    f"hours (threshold > {power_on_threshold})."
                ),
            })

    return warnings


# ---------------------------------------------------------------------------
# alert_events writer — same shape as backup_watcher's resolved
# emitter so the dispatcher renders the warnings the same way.
# ---------------------------------------------------------------------------


def _alertname_for(drive_id: str, attribute_name: str) -> str:
    """Stable alertname per (drive, attribute). Used both for dedup
    and for the resolved-row contract — the dispatcher matches on
    alertname when deciding whether a [RESOLVED] page is owed.
    """
    # Sanitize: alert_events.alertname is a plain string; we want
    # something readable in Telegram + Discord. Replace path separators
    # so a drive id like "/dev/nvme0" becomes "dev_nvme0".
    safe_drive = drive_id.replace("/", "_").lstrip("_")
    safe_attr = attribute_name.replace(" ", "_")
    return f"smart_{safe_drive}_{safe_attr}"


async def _emit_warning_alert(
    pool: Any,
    *,
    drive_id: str,
    drive_meta: dict[str, Any],
    warning: dict[str, Any],
) -> bool:
    """Insert a firing ``alert_events`` row for a single warning.

    Returns True on success, False on failure (already logged).
    """
    alertname = _alertname_for(drive_id, warning["attribute_name"])
    severity = warning["severity"]
    labels = {
        "source": "brain.smart_monitor",
        "drive_id": drive_id,
        "drive_type": str(drive_meta.get("type") or ""),
        "drive_model": str(drive_meta.get("model_name") or ""),
        "attribute_name": warning["attribute_name"],
        "category": "smart",
    }
    annotations = {
        "summary": (
            f"SMART warning on {drive_id}: {warning['attribute_name']} "
            f"= {warning['observed_value']}"
        ),
        "description": warning["detail"],
        "observed_value": str(warning["observed_value"]),
        "threshold": str(warning["threshold"]),
    }
    fingerprint = f"smart-monitor-{alertname}-{int(time.time())}"
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                alertname, severity, status, labels, annotations,
                starts_at, fingerprint
            ) VALUES (
                $1, $2, 'firing', $3::jsonb, $4::jsonb, NOW(), $5
            )
            """,
            alertname,
            severity,
            json.dumps(labels),
            json.dumps(annotations),
            fingerprint,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SMART_MONITOR] Failed to write firing alert for %s: %s",
            alertname, exc,
        )
        return False


async def _emit_resolved_alert(
    pool: Any,
    *,
    drive_id: str,
    attribute_name: str,
    detail: str,
) -> bool:
    """Insert a ``status='resolved'`` row mirroring the firing schema.

    Returns True on success, False on failure (already logged). The
    dispatcher picks the row up on its next 30s poll and pages
    ``[RESOLVED · ...]`` via Telegram + Discord.
    """
    alertname = _alertname_for(drive_id, attribute_name)
    labels = {
        "source": "brain.smart_monitor",
        "drive_id": drive_id,
        "attribute_name": attribute_name,
        "category": "smart",
    }
    annotations = {
        "summary": (
            f"SMART warning on {drive_id} cleared: {attribute_name}"
        ),
        "description": detail,
    }
    fingerprint = f"smart-monitor-resolved-{alertname}-{int(time.time())}"
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                alertname, severity, status, labels, annotations,
                starts_at, fingerprint
            ) VALUES (
                $1, 'info', 'resolved', $2::jsonb, $3::jsonb, NOW(), $4
            )
            """,
            alertname,
            json.dumps(labels),
            json.dumps(annotations),
            fingerprint,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[SMART_MONITOR] Failed to write resolved alert for %s: %s",
            alertname, exc,
        )
        return False


# ---------------------------------------------------------------------------
# Audit log — same shape as backup_watcher so the timeline shows
# SMART monitor activity beside the rest of the brain's probes.
# ---------------------------------------------------------------------------


async def _emit_audit_event(
    pool: Any,
    event: str,
    detail: str,
    *,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    payload: dict[str, Any] = {"detail": detail}
    if extra:
        payload.update(extra)
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, details, severity)
            VALUES ($1, $2, $3::jsonb, $4)
            """,
            event,
            "brain.smart_monitor",
            json.dumps(payload),
            "warning" if "warning" in event or "critical" in event else "info",
        )
    except Exception as exc:  # noqa: BLE001
        # audit_log table may not exist on a very fresh install — log
        # and carry on so the probe still does its job via the
        # dispatcher path.
        logger.debug(
            "[SMART_MONITOR] Could not write audit event %s: %s",
            event, exc,
        )


# ---------------------------------------------------------------------------
# Per-drive check — the heart of the probe. Called once per drive per
# cycle. Builds a structured per-drive summary and writes alerts.
# ---------------------------------------------------------------------------


async def _check_one_drive(
    pool: Any,
    *,
    drive_meta: dict[str, Any],
    binary: str,
    config: dict[str, Any],
    run_fn: Callable[[str, list[str]], tuple[int, Optional[dict[str, Any]], str]],
    now: float,
) -> dict[str, Any]:
    """Run smartctl -a against one drive and process the output.

    Returns a per-drive summary with ``drive_id``, ``ok``, ``status``,
    ``warnings_fired``, ``warnings_resolved``, ``warnings_suppressed``.
    """
    drive_id = str(drive_meta.get("name") or "")
    drive_type = drive_meta.get("type")
    args = ["-a", "--json", drive_id]
    if drive_type:
        args = ["-d", str(drive_type), *args]
    rc, parsed, stderr_tail = run_fn(binary, args)
    # smartctl exits non-zero on warnings (that's expected); we prefer
    # the JSON when valid even on non-zero exits.
    if parsed is None:
        return {
            "ok": False,
            "status": "smartctl_failed",
            "drive_id": drive_id,
            "drive_meta": drive_meta,
            "error": stderr_tail,
            "exit_code": rc,
        }

    warnings = _extract_warnings(parsed, drive_id=drive_id, config=config)
    dedup_window_seconds = float(config["alert_dedup_minutes"]) * 60.0

    fired: list[str] = []
    suppressed: list[str] = []
    resolved: list[str] = []

    # Track which (drive, attr) pairs we saw warnings for this cycle —
    # used below to detect attributes that *cleared* between cycles.
    observed_now: set[tuple[str, str]] = set()

    for w in warnings:
        attr = w["attribute_name"]
        key = (drive_id, attr)
        observed_now.add(key)
        last_fired = _alert_dedup_state.get(key)
        if last_fired is not None and (now - last_fired) < dedup_window_seconds:
            # Within the dedup window — suppress the re-fire but
            # remember we saw the warning so resolution detection
            # below treats it as "still firing".
            _firing_state[key] = _alertname_for(drive_id, attr)
            suppressed.append(attr)
            continue
        ok = await _emit_warning_alert(
            pool,
            drive_id=drive_id,
            drive_meta=drive_meta,
            warning=w,
        )
        if ok:
            _alert_dedup_state[key] = now
            _firing_state[key] = _alertname_for(drive_id, attr)
            fired.append(attr)
            await _emit_audit_event(
                pool,
                f"probe.smart_monitor_{w['severity']}",
                f"{drive_id}: {attr}={w['observed_value']} (threshold "
                f"{w['threshold']})",
                extra={
                    "drive_id": drive_id,
                    "attribute_name": attr,
                    "observed_value": w["observed_value"],
                    "threshold": w["threshold"],
                    "severity": w["severity"],
                },
            )

    # Resolution detection — any (drive_id, *) in _firing_state that
    # is NOT in observed_now this cycle has cleared. Emit a resolved
    # row and drop the dedup entry so a future re-fire would alert
    # immediately.
    cleared_keys: list[tuple[str, str]] = [
        key for key in list(_firing_state.keys())
        if key[0] == drive_id and key not in observed_now
    ]
    for key in cleared_keys:
        _, attr = key
        await _emit_resolved_alert(
            pool,
            drive_id=drive_id,
            attribute_name=attr,
            detail=(
                f"Drive {drive_id} attribute {attr} no longer above "
                f"threshold — clearing outstanding warning."
            ),
        )
        _firing_state.pop(key, None)
        _alert_dedup_state.pop(key, None)
        resolved.append(attr)
        await _emit_audit_event(
            pool,
            "probe.smart_monitor_resolved",
            f"{drive_id}: {attr} cleared.",
            extra={"drive_id": drive_id, "attribute_name": attr},
        )

    status = "ok"
    if fired:
        status = "warnings_fired"
    elif resolved:
        status = "warnings_resolved"

    return {
        "ok": not fired,
        "status": status,
        "drive_id": drive_id,
        "drive_meta": drive_meta,
        "warnings_fired": fired,
        "warnings_suppressed": suppressed,
        "warnings_resolved": resolved,
        "exit_code": rc,
    }


# ---------------------------------------------------------------------------
# Top-level probe entry point — called once per brain cycle.
# ---------------------------------------------------------------------------


async def run_smart_monitor_probe(
    pool: Any,
    *,
    run_fn: Optional[
        Callable[[str, list[str]], tuple[int, Optional[dict[str, Any]], str]]
    ] = None,
    which_fn: Optional[Callable[[str], Optional[str]]] = None,
    notify_fn: Optional[Callable[..., None]] = None,
    now_fn: Optional[Callable[[], float]] = None,
) -> dict[str, Any]:
    """Single execution of the SMART monitor probe.

    Args:
        pool: asyncpg pool for app_settings + alert_events + audit_log.
        run_fn: ``(binary, args) -> (exit_code, parsed_json, stderr_tail)``
            — defaults to the real subprocess wrapper. Tests inject a
            stub that returns canned smartctl output.
        which_fn: ``(name) -> path | None`` — defaults to
            ``shutil.which``. Tests can simulate "smartctl absent".
        notify_fn: operator notifier callable. Defaults to
            :func:`brain.operator_notifier.notify_operator`. Only used
            for the one-time "smartctl missing" warning; per-warning
            paging is left to the existing alert_events dispatcher.
        now_fn: ``() -> float`` — defaults to ``time.time``. Tests
            inject canned timestamps to exercise dedup windows.

    Returns a structured summary suitable for inclusion in
    ``brain_decisions`` / the cycle's ``probe_results`` map.
    """
    global _smartctl_missing_notified

    run_fn = run_fn or _run_smartctl
    which_fn = which_fn or shutil.which
    notify_fn = notify_fn or notify_operator
    now_fn = now_fn or time.time

    config = await _read_config(pool)
    if not config["enabled"]:
        return {
            "ok": True,
            "status": "disabled",
            "detail": (
                f"SMART monitor disabled "
                f"(app_settings.{ENABLED_KEY}=false)"
            ),
            "drives": {},
        }

    # Resolve smartctl. If absent, degrade gracefully — log + notify
    # exactly once so the operator hears about it, then return
    # status='skipped' on every subsequent cycle without further noise.
    binary = (
        config["smartctl_path"] if config["smartctl_path"] and os.path.isfile(config["smartctl_path"])
        else which_fn("smartctl")
    )
    if not binary:
        if not _smartctl_missing_notified:
            _smartctl_missing_notified = True
            detail = (
                "smartctl binary not found on PATH. SMART monitor "
                "probe will be skipped until smartmontools is "
                "installed. On Linux: `apt install smartmontools`. "
                "On macOS: `brew install smartmontools`. On Windows: "
                "install the smartmontools MSI from "
                "https://www.smartmontools.org/."
            )
            logger.warning("[SMART_MONITOR] %s", detail)
            try:
                notify_fn(
                    title="SMART monitor disabled — smartctl not installed",
                    detail=detail,
                    source="brain.smart_monitor",
                    severity="warning",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[SMART_MONITOR] notify_fn failed: %s", exc)
        return {
            "ok": True,  # not a brain failure — optional dep missing
            "status": "skipped",
            "detail": "smartctl not installed; SMART monitor skipped",
            "drives": {},
        }

    # Enumerate drives. If scan returns zero, log + return ok (the
    # operator may legitimately have no SMART-capable drives, e.g. a
    # VM with virtio block devices).
    drives = _scan_drives(binary, run_fn=run_fn)
    if config["drive_filter"]:
        wanted = set(config["drive_filter"])
        drives = [d for d in drives if d.get("name") in wanted]

    if not drives:
        return {
            "ok": True,
            "status": "no_drives",
            "detail": (
                "smartctl --scan-open returned no drives. If you "
                "expected SMART monitoring, check that the brain is "
                "running with sufficient privileges (root/admin)."
            ),
            "drives": {},
        }

    now = now_fn()
    drive_summaries: dict[str, dict[str, Any]] = {}
    for drive_meta in drives:
        drive_id = str(drive_meta.get("name") or "")
        if not drive_id:
            continue
        try:
            drive_summaries[drive_id] = await _check_one_drive(
                pool,
                drive_meta=drive_meta,
                binary=binary,
                config=config,
                run_fn=run_fn,
                now=now,
            )
        except Exception as exc:  # noqa: BLE001
            # One drive blowing up shouldn't take the others down —
            # log and carry on so at least the partial check runs.
            logger.warning(
                "[SMART_MONITOR] %s check raised: %s", drive_id, exc,
                exc_info=True,
            )
            drive_summaries[drive_id] = {
                "ok": False,
                "status": "exception",
                "drive_id": drive_id,
                "drive_meta": drive_meta,
                "error": str(exc)[:200],
            }

    overall_ok = all(d.get("ok", False) for d in drive_summaries.values())
    statuses = ", ".join(
        f"{drive_id}={d.get('status', 'unknown')}"
        for drive_id, d in drive_summaries.items()
    )
    return {
        "ok": overall_ok,
        "status": "ok" if overall_ok else "degraded",
        "detail": f"SMART monitor cycle: {statuses}",
        "drives": drive_summaries,
        "config": {k: v for k, v in config.items() if k != "enabled"},
    }


# ---------------------------------------------------------------------------
# Probe-Protocol adapter — for the registry-driven path. Mirrors
# BackupWatcherProbe so this slots into the same registry without new
# infrastructure.
# ---------------------------------------------------------------------------


class SmartMonitorProbe:
    """Probe-Protocol-compatible wrapper around
    :func:`run_smart_monitor_probe`.
    """

    name: str = "smart_monitor"
    description: str = (
        "Polls SMART attributes on every detected drive and pages on "
        "regression (reallocated/pending sectors, SSD wear, SMART "
        "self-test failure) before the drive actually dies."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult

        summary = await run_smart_monitor_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={
                "status": summary.get("status"),
                "drives": {
                    drive_id: d.get("status")
                    for drive_id, d in (summary.get("drives") or {}).items()
                },
            },
            severity="warning" if not summary.get("ok") else "info",
        )
