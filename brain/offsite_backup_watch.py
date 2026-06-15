"""Offsite-backup auto-retry watch (Glad-Labs/poindexter#386).

Tier 2 (off-machine restic, #386) runs an in-stack ``backup-offsite``
container that ships the daily dumps to an S3-compatible repo and stamps an
``audit_log`` heartbeat (``offsite_backup_succeeded``) on each success. This
probe is the self-heal-before-paging layer for that tier — a sibling of
``brain/backup_watcher.py`` with one difference: its freshness source is the
audit_log heartbeat (a creds-free DB read), not a dump-dir stat. So it never
needs the restic password.

Per cycle:
1. Age of the newest ``offsite_backup_succeeded`` event. Fresh
   (<= ``offsite_backup_max_age_hours``) => happy path; auto-resolve any firing
   ``offsite_backup_stale`` alert.
2. Stale => ``docker restart poindexter-backup-offsite``, wait
   ``offsite_backup_watch_retry_delay_seconds``, re-read. Fresh => recovered.
3. After ``offsite_backup_watch_max_retries`` cumulative fail-then-retry
   cycles => escalate: emit a firing ``offsite_backup_stale`` alert_events row
   (``critical``) and stop kicking. Unlike backup_watcher (which leans on the
   runner's own failure alert + the Tier 1 healthcheck), the offsite tier has
   no other alert source for a dead runner, so this watch emits its own.

Design parity with backup_watcher: standalone (stdlib + asyncpg only),
DB-configurable through ``app_settings``, injectable ``age_fn`` / ``restart_fn``
/ ``sleep_fn`` / ``notify_fn`` seams for unit tests, and a Probe-Protocol
wrapper for the registry.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Awaitable, Callable
from typing import Any

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified path for tests
    from brain.operator_notifier import notify_operator

logger = logging.getLogger("brain.offsite_backup_watch")

ENABLED_KEY = "offsite_backup_watch_enabled"
MAX_AGE_HOURS_KEY = "offsite_backup_max_age_hours"
MAX_RETRIES_KEY = "offsite_backup_watch_max_retries"
RETRY_DELAY_KEY = "offsite_backup_watch_retry_delay_seconds"

DEFAULT_ENABLED = True
DEFAULT_MAX_AGE_HOURS = 26
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY_SECONDS = 120

_CONTAINER = "poindexter-backup-offsite"
_ALERTNAME = "offsite_backup_stale"
_HEARTBEAT_EVENT = "offsite_backup_succeeded"
_DOCKER_RESTART_TIMEOUT_SECONDS = 30
PROBE_INTERVAL_SECONDS = 300

# Module-level retry counter (single tier) — persists across cycles so
# escalation fires cumulatively, exactly like backup_watcher's _retry_state.
_retry_count = 0


def _reset_retry_state() -> None:
    """Test helper — wipe the cross-cycle retry counter."""
    global _retry_count
    _retry_count = 0


# --- app_settings reads (same pattern as backup_watcher.py) -----------------


async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[OFFSITE_WATCH] read %s failed: %s — default %r", key, exc, default
        )
        return default
    return default if val is None else val


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
    return {
        "enabled": _coerce_bool(
            await _read_setting(pool, ENABLED_KEY, "true"), DEFAULT_ENABLED
        ),
        "max_age_hours": _coerce_int(
            await _read_setting(pool, MAX_AGE_HOURS_KEY, DEFAULT_MAX_AGE_HOURS),
            DEFAULT_MAX_AGE_HOURS,
        ),
        "max_retries": _coerce_int(
            await _read_setting(pool, MAX_RETRIES_KEY, DEFAULT_MAX_RETRIES),
            DEFAULT_MAX_RETRIES,
        ),
        "retry_delay_seconds": _coerce_int(
            await _read_setting(pool, RETRY_DELAY_KEY, DEFAULT_RETRY_DELAY_SECONDS),
            DEFAULT_RETRY_DELAY_SECONDS,
        ),
    }


async def _seconds_since_heartbeat(pool: Any) -> float | None:
    """Age (seconds) of the newest offsite_backup_succeeded event, or None.

    Creds-free: a plain audit_log read — the brain never touches the restic
    password or the S3 keys. This is the whole reason the watch lives here and
    not in the worker.
    """
    try:
        val = await pool.fetchval(
            "SELECT EXTRACT(EPOCH FROM (now() - MAX(created_at)))"
            " FROM audit_log WHERE event_type = $1",
            _HEARTBEAT_EVENT,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OFFSITE_WATCH] heartbeat read failed: %s", exc)
        return None
    return None if val is None else float(val)


def _restart_offsite_container(container: str) -> tuple[bool, str]:
    """``docker restart <container>`` (shape mirrors backup_watcher)."""
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": _DOCKER_RESTART_TIMEOUT_SECONDS,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(["docker", "restart", container], **kwargs)
        if result.returncode == 0:
            return True, f"Restarted {container}"
        return False, (
            f"docker restart {container} exit {result.returncode}: "
            f"{(result.stderr or '').strip()[:200]}"
        )
    except FileNotFoundError:
        return False, "docker CLI not on PATH"
    except subprocess.TimeoutExpired:
        return False, f"docker restart {container} timed out"
    except Exception as exc:  # noqa: BLE001
        return False, f"docker restart error: {type(exc).__name__}: {str(exc)[:160]}"


async def _firing_alert_exists(pool: Any, alertname: str) -> bool:
    try:
        row = await pool.fetchrow(
            "SELECT status FROM alert_events WHERE alertname = $1"
            " ORDER BY id DESC LIMIT 1",
            alertname,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OFFSITE_WATCH] alert lookup failed: %s", exc)
        return False
    return bool(row) and (row["status"] or "").lower() == "firing"


async def _emit_alert(pool: Any, *, status: str, severity: str, detail: str) -> bool:
    """Insert an ``offsite_backup_stale`` alert_events row (firing or resolved).

    Unlike backup_watcher (which only ever writes ``resolved`` rows and leans on
    the runner's own failure alert), the offsite tier has no other alert source
    for a dead runner, so this watch emits the firing row itself on escalate.
    """
    labels = {
        "source": "brain.offsite_backup_watch",
        "category": "backup",
        "tier": "offsite",
    }
    annotations = {
        "summary": (
            "Offsite backup stale — runner not producing snapshots"
            if status == "firing"
            else "Offsite backup recovered after auto-retry"
        ),
        "description": detail,
    }
    fingerprint = f"offsite-backup-{status}-{int(time.time())}"
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                alertname, severity, status, labels, annotations,
                starts_at, fingerprint
            ) VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, NOW(), $6)
            """,
            _ALERTNAME,
            severity,
            status,
            json.dumps(labels),
            json.dumps(annotations),
            fingerprint,
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("[OFFSITE_WATCH] alert insert (%s) failed: %s", status, exc)
        return False


async def _emit_audit_event(
    pool: Any, event: str, detail: str, *, extra: dict[str, Any] | None = None
) -> None:
    payload: dict[str, Any] = {"detail": detail}
    if extra:
        payload.update(extra)
    try:
        await pool.execute(
            "INSERT INTO audit_log (event_type, source, details, severity)"
            " VALUES ($1,$2,$3::jsonb,$4)",
            event,
            "brain.offsite_backup_watch",
            json.dumps(payload),
            "warning" if "stale" in event or "escalate" in event else "info",
        )
    except Exception as exc:  # noqa: BLE001
        # silent-ok: the audit_log timeline write is best-effort (the table may
        # be absent on a very fresh install); the probe's real work — restart +
        # the firing alert_events row — already happened via their own paths.
        logger.debug("[OFFSITE_WATCH] audit write failed: %s", exc)


async def run_offsite_backup_watch_probe(
    pool: Any,
    *,
    age_fn: Callable[[], Awaitable[float | None]] | None = None,
    restart_fn: Callable[[str], tuple[bool, str]] | None = None,
    sleep_fn: Callable[[float], None] | None = None,
    notify_fn: Callable[..., None] | None = None,
) -> dict[str, Any]:
    """Single cycle of the offsite-backup watch.

    Args:
        pool: asyncpg pool for app_settings + audit_log + alert_events.
        age_fn: ``() -> age_seconds | None`` — defaults to the audit_log
            heartbeat read. Tests inject canned ages.
        restart_fn: ``(container) -> (ok, msg)`` — defaults to the real
            ``docker restart``.
        sleep_fn: ``(seconds) -> None`` — defaults to ``time.sleep``.
        notify_fn: operator notifier — defaults to
            :func:`brain.operator_notifier.notify_operator`, used only for the
            "docker is unreachable" surface.
    """
    global _retry_count
    age_fn = age_fn or (lambda: _seconds_since_heartbeat(pool))
    restart_fn = restart_fn or _restart_offsite_container
    sleep_fn = sleep_fn or time.sleep
    notify_fn = notify_fn or notify_operator

    config = await _read_config(pool)
    if not config["enabled"]:
        return {"ok": True, "status": "disabled", "detail": f"{ENABLED_KEY}=false"}

    max_age_seconds = float(config["max_age_hours"]) * 3600.0
    max_retries = int(config["max_retries"])
    retry_delay = float(config["retry_delay_seconds"])

    age = await age_fn()

    # 1) Fresh => happy path / auto-resolve.
    if age is not None and age <= max_age_seconds:
        prev = _retry_count
        _retry_count = 0
        status = "fresh"
        if await _firing_alert_exists(pool, _ALERTNAME):
            await _emit_alert(
                pool,
                status="resolved",
                severity="info",
                detail=(
                    f"Offsite backup fresh again (age={age:.0f}s <= "
                    f"{max_age_seconds:.0f}s) after {prev} watch retry attempt(s)."
                ),
            )
            await _emit_audit_event(
                pool,
                "probe.offsite_backup_resolved",
                f"fresh again (age={age:.0f}s)",
                extra={"age_seconds": age},
            )
            status = "auto_resolved"
        return {"ok": True, "status": status, "age_seconds": age, "retries_used": prev}

    # 2) Stale / missing — escalate if we've burned the retry budget.
    if _retry_count >= max_retries:
        detail = (
            f"Offsite backup stale after {_retry_count} retry attempt(s) "
            f"(age={age!r}s, threshold={max_age_seconds:.0f}s). Escalating."
        )
        logger.warning("[OFFSITE_WATCH] %s", detail)
        if not await _firing_alert_exists(pool, _ALERTNAME):
            await _emit_alert(pool, status="firing", severity="critical", detail=detail)
        await _emit_audit_event(
            pool,
            "probe.offsite_backup_escalate",
            detail,
            extra={"age_seconds": age, "retries_used": _retry_count},
        )
        return {
            "ok": False,
            "status": "escalated",
            "age_seconds": age,
            "retries_used": _retry_count,
        }

    # 2a) Retry via docker restart.
    _retry_count += 1
    logger.info(
        "[OFFSITE_WATCH] stale (age=%s) — restart %d/%d on %s",
        f"{age:.0f}s" if age is not None else "missing",
        _retry_count,
        max_retries,
        _CONTAINER,
    )
    ok, msg = restart_fn(_CONTAINER)
    if not ok:
        detail = (
            f"Offsite stale and docker restart failed: {msg} "
            f"(retry {_retry_count}/{max_retries})."
        )
        logger.warning("[OFFSITE_WATCH] %s", detail)
        await _emit_audit_event(
            pool,
            "probe.offsite_backup_restart_failed",
            detail,
            extra={"retries_used": _retry_count, "restart_error": msg},
        )
        if "docker CLI" in msg:
            try:
                notify_fn(
                    title="Offsite watch cannot restart container",
                    detail=detail,
                    source="brain.offsite_backup_watch",
                    severity="warning",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[OFFSITE_WATCH] notify failed: %s", exc)
        return {"ok": False, "status": "restart_failed", "retries_used": _retry_count}

    # 2b) Wait + re-read.
    sleep_fn(retry_delay)
    post_age = await age_fn()
    if post_age is not None and post_age <= max_age_seconds:
        _retry_count = 0
        if await _firing_alert_exists(pool, _ALERTNAME):
            await _emit_alert(
                pool,
                status="resolved",
                severity="info",
                detail=f"Offsite recovered after restart (age={post_age:.0f}s).",
            )
        await _emit_audit_event(
            pool,
            "probe.offsite_backup_recovered",
            f"fresh after restart (age={post_age:.0f}s)",
            extra={"age_seconds": post_age},
        )
        return {
            "ok": True,
            "status": "recovered",
            "age_seconds": post_age,
            "retries_used": _retry_count,
        }

    detail = (
        f"Offsite still stale after restart (age={post_age!r}s). "
        f"Used {_retry_count}/{max_retries}."
    )
    logger.warning("[OFFSITE_WATCH] %s", detail)
    await _emit_audit_event(
        pool,
        "probe.offsite_backup_retry_failed",
        detail,
        extra={"age_seconds": post_age, "retries_used": _retry_count},
    )
    return {
        "ok": False,
        "status": "retry_failed",
        "age_seconds": post_age,
        "retries_used": _retry_count,
    }


class OffsiteBackupWatchProbe:
    """Probe-Protocol wrapper (mirrors BackupWatcherProbe)."""

    name: str = "offsite_backup_watch"
    description: str = (
        "Watches the off-machine backup tier's audit_log heartbeat; `docker "
        "restart`s the wedged runner before paging, and emits "
        "offsite_backup_stale on escalate."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult
        summary = await run_offsite_backup_watch_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("status", ""),
            metrics={"status": summary.get("status")},
            severity="warning" if not summary.get("ok") else "info",
        )
