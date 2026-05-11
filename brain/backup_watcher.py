"""Backup-watcher probe + auto-retry workflow (Glad-Labs/poindexter#388).

Tier 1 of the in-stack backup story (#385) ships two compose services
(``backup-hourly``, ``backup-daily``) that ``pg_dump`` into a host
bind-mount and INSERT into ``alert_events`` on any non-zero exit. The
brain's ``alert_dispatcher`` then pages the operator via Telegram +
Discord. That works, but it pages on *every* transient hiccup —
postgres briefly down during a stack restart, disk full for the 60
seconds the operator takes to notice and free space, etc.

This probe sits between the failure and the page:

1. **Freshness check** — every cycle, stat the latest dump in each
   tier directory. Hourly should be < ``backup_watcher_hourly_max_age_minutes``
   (default 90 — matches the compose healthcheck), daily < 26h
   (default ``backup_watcher_daily_max_age_hours``).
2. **Auto-retry** — when stale, ``docker restart
   poindexter-backup-<tier>``. Sleep ``backup_watcher_retry_delay_seconds``
   (default 120). Re-stat. If a fresh dump appeared, *and* there's a
   firing ``backup_<tier>_failed`` alert outstanding, write a
   ``status='resolved'`` row so the dispatcher pages "RESOLVED".
3. **Escalate** — after ``backup_watcher_max_retries`` (default 2)
   consecutive fail-then-retry-fail cycles, stop kicking the container
   and leave the original alert firing. The operator is already paged;
   adding more noise won't help.

Design parity with the rest of the brain:

- DB-configurable through ``app_settings`` (every tunable above is a
  row, not a constant — see migration
  ``20260506_*_seed_backup_watcher_settings.py``).
- Standalone module: only stdlib + asyncpg. Subprocess calls degrade
  gracefully when the docker CLI isn't reachable (logged, not raised).
- Mirrors ``brain/migration_drift_probe.py`` lifecycle: a
  ``run_backup_watcher_probe(pool, *, ...)`` entry point with
  injectable ``stat_fn``/``restart_fn``/``sleep_fn``/``notify_fn``
  seams for unit tests; a ``BackupWatcherProbe`` Probe-Protocol
  wrapper for the registry.

Auto-resolve mechanism: writing a fresh ``alert_events`` row with
``status='resolved'`` and the same ``alertname`` is the contract the
existing dispatcher already speaks (Alertmanager status semantics; the
brain's ``_format_alert_message`` renders the header as
``[RESOLVED · ...]``). We don't UPDATE the original row — that would
silently rewrite history. The "fired then recovered" pair is the
audit trail.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified for tests
    from brain.operator_notifier import notify_operator

logger = logging.getLogger("brain.backup_watcher")


# ---------------------------------------------------------------------------
# App_settings keys — every tunable lives in the DB so an operator can
# adjust without redeploying the brain. Defaults below match the
# healthcheck thresholds in docker-compose.local.yml so the watcher
# fires at the same moment the container is marked unhealthy.
# ---------------------------------------------------------------------------

ENABLED_KEY = "backup_watcher_enabled"
POLL_INTERVAL_MINUTES_KEY = "backup_watcher_poll_interval_minutes"
HOURLY_MAX_AGE_MINUTES_KEY = "backup_watcher_hourly_max_age_minutes"
DAILY_MAX_AGE_HOURS_KEY = "backup_watcher_daily_max_age_hours"
MAX_RETRIES_KEY = "backup_watcher_max_retries"
RETRY_DELAY_SECONDS_KEY = "backup_watcher_retry_delay_seconds"
BACKUP_DIR_KEY = "backup_watcher_backup_dir"
# Glad-Labs/poindexter#444: dr-backup scripts (host-side, not the
# in-stack tiers above) drop ``dr-backup-*-failed.sentinel`` files
# under ``~/.poindexter/logs/`` when both the script failed AND the
# script's primary Telegram alert path failed. The brain bind-mounts
# that directory read-only at /host-backup-logs and surfaces any
# sentinels through alert_events on each cycle — the second line of
# defense behind the in-stack age-based check.
SENTINEL_DIR_KEY = "backup_watcher_sentinel_dir"

DEFAULT_ENABLED = True
DEFAULT_POLL_INTERVAL_MINUTES = 5
DEFAULT_HOURLY_MAX_AGE_MINUTES = 90
DEFAULT_DAILY_MAX_AGE_HOURS = 26
DEFAULT_MAX_RETRIES = 2
DEFAULT_RETRY_DELAY_SECONDS = 120
DEFAULT_BACKUP_DIR = str(Path.home() / ".poindexter" / "backups" / "auto")
DEFAULT_SENTINEL_DIR = "/host-backup-logs"

# Container names match docker-compose.local.yml. Kept as constants
# rather than settings — changing them would break dozens of other
# places (compose file, prometheus scrape config, healthcheck script).
_CONTAINERS_BY_TIER: dict[str, str] = {
    "hourly": "poindexter-backup-hourly",
    "daily": "poindexter-backup-daily",
}

# Subprocess timeout for ``docker restart``. Generous because a stack
# under load can take 10s+ to restart a container with an active
# pg_dump in flight.
DOCKER_RESTART_TIMEOUT_SECONDS = 30

# How long the probe is willing to take in a single cycle. With the
# default ``retry_delay_seconds=120`` and ``max_retries=2``, a worst-case
# cycle for one tier is ~4 minutes; both tiers ~8. Brain cycles at 5
# minutes by default, so back-to-back ``run_backup_watcher_probe`` calls
# would overlap. Mitigation: per-tier retry bookkeeping persists across
# cycles, so a long cycle's retries don't have to all happen in one
# call — see ``_retry_state`` below.
PROBE_INTERVAL_SECONDS = 300

# Probe-name → tier mapping; used to derive the alert_events alertname
# the backup runner inserts on failure (see scripts/backup/run.sh).
_ALERTNAME_BY_TIER: dict[str, str] = {
    "hourly": "backup_hourly_failed",
    "daily": "backup_daily_failed",
}

# Glad-Labs/poindexter#444 — dr-backup sentinel surfacing. These map
# the on-disk sentinel filenames the host-side scripts at
# ``~/.poindexter/scripts/dr-backup/*.sh`` drop when the primary
# Telegram alert path failed, to a distinct alertname so the operator
# can tell the host-side dr-backup failure apart from the in-stack
# ``backup_<tier>_failed`` page above. The "_path → tier → alertname"
# mapping is kept here (not in app_settings) because changing it would
# require a coordinated edit on the script side too — same rationale
# as the container-name constants above.
_SENTINEL_FILES_BY_TIER: dict[str, str] = {
    "hourly": "dr-backup-hourly-failed.sentinel",
    "daily": "dr-backup-failed.sentinel",
}
_SENTINEL_ALERTNAME_BY_TIER: dict[str, str] = {
    "hourly": "dr_backup_hourly_failed",
    "daily": "dr_backup_daily_failed",
}


# ---------------------------------------------------------------------------
# Module-level retry bookkeeping — per-tier consecutive-retry counter so
# escalation fires *across* cycles. Reset to 0 when a fresh dump appears.
# ---------------------------------------------------------------------------

_retry_state: dict[str, int] = {tier: 0 for tier in _CONTAINERS_BY_TIER}


def _reset_retry_state() -> None:
    """Test helper — wipe the per-tier retry counter."""
    for tier in _CONTAINERS_BY_TIER:
        _retry_state[tier] = 0


# ---------------------------------------------------------------------------
# app_settings reads — brain is standalone so we hit the DB directly.
# Each helper degrades to its default when the row is missing or the
# fetch raises, mirroring the pattern in brain/migration_drift_probe.py.
# ---------------------------------------------------------------------------


async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[BACKUP_WATCHER] Could not read %s from app_settings: %s "
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

    Returns a dict with all eight settings resolved + coerced. Cheap
    because the brain pool is local to the same Postgres instance.
    """
    enabled = _coerce_bool(
        await _read_setting(pool, ENABLED_KEY, "true"),
        DEFAULT_ENABLED,
    )
    poll_interval_minutes = _coerce_int(
        await _read_setting(pool, POLL_INTERVAL_MINUTES_KEY, DEFAULT_POLL_INTERVAL_MINUTES),
        DEFAULT_POLL_INTERVAL_MINUTES,
    )
    hourly_max_age_minutes = _coerce_int(
        await _read_setting(pool, HOURLY_MAX_AGE_MINUTES_KEY, DEFAULT_HOURLY_MAX_AGE_MINUTES),
        DEFAULT_HOURLY_MAX_AGE_MINUTES,
    )
    daily_max_age_hours = _coerce_int(
        await _read_setting(pool, DAILY_MAX_AGE_HOURS_KEY, DEFAULT_DAILY_MAX_AGE_HOURS),
        DEFAULT_DAILY_MAX_AGE_HOURS,
    )
    max_retries = _coerce_int(
        await _read_setting(pool, MAX_RETRIES_KEY, DEFAULT_MAX_RETRIES),
        DEFAULT_MAX_RETRIES,
    )
    retry_delay_seconds = _coerce_int(
        await _read_setting(pool, RETRY_DELAY_SECONDS_KEY, DEFAULT_RETRY_DELAY_SECONDS),
        DEFAULT_RETRY_DELAY_SECONDS,
    )
    backup_dir = await _read_setting(pool, BACKUP_DIR_KEY, DEFAULT_BACKUP_DIR)
    if backup_dir is None or str(backup_dir).strip() == "":
        backup_dir = DEFAULT_BACKUP_DIR
    backup_dir = os.path.expanduser(str(backup_dir).strip())
    sentinel_dir = await _read_setting(pool, SENTINEL_DIR_KEY, DEFAULT_SENTINEL_DIR)
    if sentinel_dir is None or str(sentinel_dir).strip() == "":
        sentinel_dir = DEFAULT_SENTINEL_DIR
    sentinel_dir = os.path.expanduser(str(sentinel_dir).strip())

    return {
        "enabled": enabled,
        "poll_interval_minutes": poll_interval_minutes,
        "hourly_max_age_minutes": hourly_max_age_minutes,
        "daily_max_age_hours": daily_max_age_hours,
        "max_retries": max_retries,
        "retry_delay_seconds": retry_delay_seconds,
        "backup_dir": backup_dir,
        "sentinel_dir": sentinel_dir,
    }


# ---------------------------------------------------------------------------
# Filesystem freshness — brain reads the host-side bind mount directly
# (the same path the compose service writes to inside the container).
# ---------------------------------------------------------------------------


def _latest_dump_age_seconds(
    backup_dir: str,
    tier: str,
    *,
    now: Optional[float] = None,
) -> Optional[float]:
    """Return age (in seconds) of the newest ``poindexter_brain_*.dump``
    file under ``<backup_dir>/<tier>/``. ``None`` when the directory is
    missing or empty (treated as stale by the caller).

    Uses ``os.scandir`` instead of ``Path.glob`` so a directory with
    thousands of files doesn't pay the ``Path()`` allocation per entry.
    """
    tier_dir = Path(backup_dir) / tier
    if not tier_dir.is_dir():
        return None

    latest_mtime: float = 0.0
    try:
        with os.scandir(tier_dir) as it:
            for entry in it:
                name = entry.name
                # Mirror the runner's naming: poindexter_brain_<ts>.dump.
                # Skip the partial ``.tmp`` files atomic-rename leaves
                # behind on a mid-dump kill.
                if not name.startswith("poindexter_brain_"):
                    continue
                if not name.endswith(".dump"):
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if mtime > latest_mtime:
                    latest_mtime = mtime
    except OSError as exc:
        logger.warning(
            "[BACKUP_WATCHER] Could not scan %s: %s", tier_dir, exc
        )
        return None

    if latest_mtime == 0.0:
        return None
    return (now if now is not None else time.time()) - latest_mtime


# ---------------------------------------------------------------------------
# Container restart — same pattern as migration_drift_probe._restart_worker.
# ---------------------------------------------------------------------------


def _restart_backup_container(container: str) -> tuple[bool, str]:
    """Run ``docker restart <container>``. Returns (ok, message).

    Brain container has /var/run/docker.sock bind-mounted (see
    docker-compose.local.yml) and the docker CLI installed
    (brain/Dockerfile). Never raises — caller handles the bool. On
    Windows the Docker CLI is invoked from the host via the same
    PATH-discovered binary; the CREATE_NO_WINDOW flag suppresses the
    flash-and-vanish console window per Matt's "no popups" rule.
    """
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": DOCKER_RESTART_TIMEOUT_SECONDS,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(
            ["docker", "restart", container],
            **kwargs,
        )
        if result.returncode == 0:
            return True, f"Restarted {container}"
        return False, (
            f"docker restart {container} exit {result.returncode}: "
            f"{(result.stderr or '').strip()[:200]}"
        )
    except FileNotFoundError:
        return False, (
            "docker CLI not on PATH (brain image missing docker binary?)"
        )
    except subprocess.TimeoutExpired:
        return False, (
            f"docker restart {container} timed out after "
            f"{DOCKER_RESTART_TIMEOUT_SECONDS}s"
        )
    except Exception as exc:  # noqa: BLE001
        return False, (
            f"docker restart error: {type(exc).__name__}: {str(exc)[:160]}"
        )


# ---------------------------------------------------------------------------
# Alert auto-resolve — write a fresh ``status='resolved'`` row matching
# the ``backup_<tier>_failed`` alertname. The dispatcher renders that
# as ``[RESOLVED · ...]`` so the operator's phone shows the recovery
# right after the fail page.
# ---------------------------------------------------------------------------


async def _firing_alert_exists(pool: Any, alertname: str) -> bool:
    """True iff the most recent row for ``alertname`` is firing.

    We don't care about historical noise — only whether the *latest*
    state is firing. If the runner has already inserted multiple firing
    rows (one per failed cycle), the most recent one stands in for the
    open incident.
    """
    try:
        row = await pool.fetchrow(
            """
            SELECT status FROM alert_events
            WHERE alertname = $1
            ORDER BY id DESC LIMIT 1
            """,
            alertname,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[BACKUP_WATCHER] alert_events lookup failed for %s: %s",
            alertname, exc,
        )
        return False
    if row is None:
        return False
    return (row["status"] or "").lower() == "firing"


async def _emit_resolved_alert(
    pool: Any,
    *,
    tier: str,
    alertname: str,
    detail: str,
) -> bool:
    """Insert a ``status='resolved'`` row mirroring the runner's schema.

    Returns True on success, False on failure (already logged). The
    brain's alert_dispatcher picks the row up on its next 30s poll and
    fires "[RESOLVED · ...]" via the same Telegram + Discord transport
    the original page used.
    """
    labels = {
        "source": "brain.backup_watcher",
        "tier": tier,
        "category": "backup",
    }
    annotations = {
        "summary": f"Backup tier={tier} recovered after auto-retry",
        "description": detail,
    }
    fingerprint = f"backup-watcher-resolved-{tier}-{int(time.time())}"
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
            "[BACKUP_WATCHER] Failed to write resolved alert for %s: %s",
            alertname, exc,
        )
        return False


# ---------------------------------------------------------------------------
# Audit log — same shape as the rest of the brain's probes so the
# "what happened, when" timeline shows backup-watcher activity beside
# everything else.
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
            "brain.backup_watcher",
            json.dumps(payload),
            "warning" if "stale" in event or "escalate" in event else "info",
        )
    except Exception as exc:  # noqa: BLE001
        # audit_log table may not exist on a very fresh install — log
        # and carry on so the probe still does its job via the
        # dispatcher path.
        logger.debug(
            "[BACKUP_WATCHER] Could not write audit event %s: %s",
            event, exc,
        )


# ---------------------------------------------------------------------------
# dr-backup sentinel surfacing (Glad-Labs/poindexter#444).
#
# The host-side dr-backup scripts at ``~/.poindexter/scripts/dr-backup/``
# write a sentinel file under ``~/.poindexter/logs/`` when the script
# itself failed AND its primary Telegram-alert path also failed
# (creds missing, postgres down, network broken). brain bind-mounts
# that log dir read-only at /host-backup-logs so this probe can scan
# for the sentinels and surface them through the existing alert_events
# pipeline. Sentinel cleanup is owned by the script side — the script
# rms its own sentinel on the next successful run, so we never delete
# files we don't own.
# ---------------------------------------------------------------------------


def _parse_sentinel_file(path: Path) -> dict[str, str]:
    """Parse a dr-backup sentinel file into a dict of key→value strings.

    Sentinel format (see ~/.poindexter/scripts/dr-backup/run-*.sh)::

        rc=<exit_code>
        ts=<utc-iso>
        host=<hostname>          # daily only
        log=<log_path>
        tail<<EOF
        <multi-line tail>
        EOF

    The parser is deliberately tolerant — the script format is owned
    elsewhere and may evolve. Unknown keys pass through; missing keys
    are simply absent from the returned dict. The parsed ``tail`` (if
    any) is rejoined with ``\\n``.
    """
    parsed: dict[str, str] = {"_path": str(path)}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning(
            "[BACKUP_WATCHER] Could not read sentinel %s: %s", path, exc
        )
        return parsed

    in_tail = False
    tail_lines: list[str] = []
    for raw in text.splitlines():
        if in_tail:
            if raw.strip() == "EOF":
                in_tail = False
                continue
            tail_lines.append(raw)
            continue
        if raw == "tail<<EOF":
            in_tail = True
            continue
        if "=" not in raw:
            continue
        key, _, value = raw.partition("=")
        parsed[key.strip()] = value.strip()
    if tail_lines:
        parsed["tail"] = "\n".join(tail_lines)
    return parsed


def _scan_sentinel_dir(
    sentinel_dir: str,
) -> list[tuple[str, Path, dict[str, str]]]:
    """Find every dr-backup sentinel under ``sentinel_dir``.

    Returns ``(tier, path, parsed)`` triples for each existing sentinel.
    Missing directory returns an empty list — the dir-not-mounted case
    is logged separately by the caller so a missing /host-backup-logs
    mount becomes a single visible warning rather than a per-cycle
    silent skip.
    """
    out: list[tuple[str, Path, dict[str, str]]] = []
    base = Path(sentinel_dir)
    if not base.is_dir():
        return out
    for tier, filename in _SENTINEL_FILES_BY_TIER.items():
        candidate = base / filename
        if candidate.is_file():
            out.append((tier, candidate, _parse_sentinel_file(candidate)))
    return out


async def _alert_with_fingerprint_exists(pool: Any, fingerprint: str) -> bool:
    """True if ``alert_events`` already has a row with this fingerprint.

    ``alert_events.fingerprint`` has no UNIQUE constraint, so this
    check-before-insert is what dedups sentinel pages: the sentinel
    file persists until the next successful run, but the operator
    only wants one page per failure incident — not one per probe cycle.
    """
    try:
        row = await pool.fetchrow(
            "SELECT 1 FROM alert_events WHERE fingerprint = $1 LIMIT 1",
            fingerprint,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[BACKUP_WATCHER] alert_events fingerprint lookup failed "
            "for %s: %s", fingerprint, exc,
        )
        return False
    return row is not None


async def _emit_sentinel_alert(
    pool: Any,
    *,
    tier: str,
    sentinel_path: Path,
    parsed: dict[str, str],
) -> bool:
    """Insert a firing alert_events row for an unhandled dr-backup sentinel.

    Returns True if a new row was written, False if a row with the same
    fingerprint already exists (we already paged on this incident) or
    the insert failed (already logged). Severity is ``warning`` to
    match the surrounding ``notify_fn`` calls in this module.
    """
    alertname = _SENTINEL_ALERTNAME_BY_TIER[tier]
    ts_seed = parsed.get("ts")
    if not ts_seed:
        try:
            ts_seed = str(int(sentinel_path.stat().st_mtime))
        except OSError:
            ts_seed = str(int(time.time()))
    fingerprint = f"dr-backup-sentinel-{tier}-{ts_seed}"

    if await _alert_with_fingerprint_exists(pool, fingerprint):
        return False

    labels = {
        "source": "brain.backup_watcher.sentinel",
        "tier": tier,
        "category": "backup",
        "host": parsed.get("host", "unknown"),
    }
    annotations = {
        "summary": (
            f"dr-backup tier={tier} script failed AND its primary "
            f"alert path was unreachable — sentinel surfaced by brain."
        ),
        "description": (
            f"Sentinel {sentinel_path.name} present.\n"
            f"rc={parsed.get('rc', '?')} ts={parsed.get('ts', '?')} "
            f"host={parsed.get('host', '?')} log={parsed.get('log', '?')}\n"
            f"\nTail:\n{parsed.get('tail', '(no tail captured)')}"
        ),
    }
    try:
        await pool.execute(
            """
            INSERT INTO alert_events (
                alertname, severity, status, labels, annotations,
                starts_at, fingerprint
            ) VALUES (
                $1, 'warning', 'firing', $2::jsonb, $3::jsonb, NOW(), $4
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
            "[BACKUP_WATCHER] Failed to write sentinel alert for %s: %s",
            alertname, exc,
        )
        return False


async def _check_sentinels(
    pool: Any,
    *,
    sentinel_dir: str,
    scan_fn: Callable[[str], list[tuple[str, Path, dict[str, str]]]],
    notify_fn: Callable[..., None],
) -> dict[str, Any]:
    """Scan ``sentinel_dir`` for dr-backup sentinels and emit alerts.

    Mirrors the per-tier-summary shape returned elsewhere in this
    module so the top-level probe summary stays homogeneous. The
    missing-dir branch fires a single warning notify (not per-cycle
    spam — the dispatcher dedups by fingerprint, but notify_operator
    has no such dedup) so an operator who forgot the bind mount
    notices, then the probe goes back to clean status.
    """
    if not Path(sentinel_dir).is_dir():
        detail = (
            f"Sentinel dir {sentinel_dir!r} not present in container. "
            f"Add the bind mount to docker-compose.local.yml "
            f"(brain-daemon → volumes) or set "
            f"app_settings.{SENTINEL_DIR_KEY} to a path that is mounted."
        )
        logger.info("[BACKUP_WATCHER] %s", detail)
        return {
            "ok": True,
            "status": "dir_missing",
            "detail": detail,
            "sentinels": [],
        }

    found = scan_fn(sentinel_dir)
    if not found:
        return {
            "ok": True,
            "status": "clean",
            "detail": "No dr-backup sentinels present.",
            "sentinels": [],
        }

    summaries: list[dict[str, Any]] = []
    for tier, path, parsed in found:
        emitted = await _emit_sentinel_alert(
            pool,
            tier=tier,
            sentinel_path=path,
            parsed=parsed,
        )
        if emitted:
            await _emit_audit_event(
                pool,
                "probe.backup_watcher_sentinel_alert",
                f"Surfaced dr-backup {tier} sentinel via alert_events.",
                extra={
                    "tier": tier,
                    "sentinel": str(path),
                    "rc": parsed.get("rc"),
                    "ts": parsed.get("ts"),
                    "host": parsed.get("host"),
                },
            )
            try:
                # Belt-and-suspenders: the alert_dispatcher routing
                # picks up the alert_events row on its 30s poll, but
                # if dispatcher routing is itself broken (the exact
                # scenario where the sentinel exists in the first
                # place), notify_operator's fallback chain (Telegram →
                # Discord → alerts.log → stderr) is the last resort.
                notify_fn(
                    title=(
                        f"dr-backup {tier} failed (sentinel surfaced)"
                    ),
                    detail=(
                        f"rc={parsed.get('rc', '?')} "
                        f"ts={parsed.get('ts', '?')} "
                        f"host={parsed.get('host', '?')}\n"
                        f"See {path}"
                    ),
                    source="brain.backup_watcher",
                    severity="warning",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[BACKUP_WATCHER] notify_fn failed on sentinel: %s", exc
                )
        summaries.append({
            "tier": tier,
            "path": str(path),
            "rc": parsed.get("rc"),
            "ts": parsed.get("ts"),
            "alert_emitted": emitted,
        })

    new_count = sum(1 for s in summaries if s["alert_emitted"])
    return {
        "ok": False,
        "status": "sentinels_found",
        "detail": (
            f"Surfaced {new_count} new dr-backup sentinel alert(s); "
            f"{len(summaries)} sentinel(s) currently present."
        ),
        "sentinels": summaries,
    }


# ---------------------------------------------------------------------------
# Per-tier check — the heart of the probe. Called once per tier per
# cycle. Returns a structured per-tier summary dict the top-level
# probe stitches together.
# ---------------------------------------------------------------------------


async def _check_one_tier(
    pool: Any,
    *,
    tier: str,
    config: dict[str, Any],
    stat_fn: Callable[[str, str], Optional[float]],
    restart_fn: Callable[[str], tuple[bool, str]],
    sleep_fn: Callable[[float], None],
    notify_fn: Callable[..., None],
) -> dict[str, Any]:
    """Run the freshness → retry → escalate flow for one backup tier.

    Returns a per-tier summary with ``ok``, ``status``, ``age_seconds``,
    ``retries_used``, and ``container``.
    """
    backup_dir = config["backup_dir"]
    container = _CONTAINERS_BY_TIER[tier]
    alertname = _ALERTNAME_BY_TIER[tier]
    if tier == "hourly":
        max_age_seconds = float(config["hourly_max_age_minutes"]) * 60.0
    else:
        max_age_seconds = float(config["daily_max_age_hours"]) * 3600.0
    max_retries = int(config["max_retries"])
    retry_delay = float(config["retry_delay_seconds"])

    # 1) Initial freshness check.
    age = stat_fn(backup_dir, tier)
    if age is not None and age <= max_age_seconds:
        # Fresh — happy path. If we previously retried, that means
        # the dump self-healed without operator intervention; reset
        # the per-tier counter and (if the dispatcher hasn't already)
        # write a resolved row so the operator's phone gets the
        # recovery page.
        prev_retries = _retry_state[tier]
        if prev_retries > 0:
            _retry_state[tier] = 0
        if await _firing_alert_exists(pool, alertname):
            await _emit_resolved_alert(
                pool,
                tier=tier,
                alertname=alertname,
                detail=(
                    f"Backup tier={tier} recovered: latest dump is "
                    f"{age:.0f}s old (threshold {max_age_seconds:.0f}s). "
                    f"Resolving outstanding {alertname} after "
                    f"{prev_retries} retry attempt(s) by the watcher."
                ),
            )
            await _emit_audit_event(
                pool,
                "probe.backup_watcher_resolved",
                f"{tier}: dump fresh again ({age:.0f}s); auto-resolved alert.",
                extra={"tier": tier, "age_seconds": age, "container": container},
            )
            status = "auto_resolved"
        else:
            status = "fresh"
        return {
            "ok": True,
            "status": status,
            "tier": tier,
            "container": container,
            "age_seconds": age,
            "max_age_seconds": max_age_seconds,
            "retries_used": prev_retries,
        }

    # 2) Stale (or missing). Decide between retry and escalate.
    used = _retry_state[tier]
    if used >= max_retries:
        # We already burned through the allowed retries on previous
        # cycles. Stop kicking the container — leave the dispatcher
        # alert firing so the operator handles it manually.
        detail = (
            f"Backup tier={tier} stale after {used} retry attempt(s) "
            f"(latest dump age={age!r}s, threshold={max_age_seconds:.0f}s). "
            f"Escalating — operator must investigate."
        )
        logger.warning("[BACKUP_WATCHER] %s", detail)
        await _emit_audit_event(
            pool,
            "probe.backup_watcher_escalate",
            detail,
            extra={
                "tier": tier,
                "container": container,
                "age_seconds": age,
                "retries_used": used,
                "max_retries": max_retries,
            },
        )
        return {
            "ok": False,
            "status": "escalated",
            "tier": tier,
            "container": container,
            "age_seconds": age,
            "max_age_seconds": max_age_seconds,
            "retries_used": used,
        }

    # 2a) Try a docker restart. Counts toward the retry budget whether
    # docker accepts the command or not — a missing docker socket means
    # we can't auto-recover, period.
    _retry_state[tier] = used + 1
    logger.info(
        "[BACKUP_WATCHER] %s stale (age=%s, threshold=%.0fs) — restart "
        "attempt %d/%d on %s",
        tier, f"{age:.0f}s" if age is not None else "missing",
        max_age_seconds, _retry_state[tier], max_retries, container,
    )
    restart_ok, restart_msg = restart_fn(container)
    if not restart_ok:
        detail = (
            f"Backup tier={tier} stale and docker restart failed: "
            f"{restart_msg}. Retry attempt {_retry_state[tier]}/{max_retries}."
        )
        logger.warning("[BACKUP_WATCHER] %s", detail)
        await _emit_audit_event(
            pool,
            "probe.backup_watcher_restart_failed",
            detail,
            extra={
                "tier": tier,
                "container": container,
                "age_seconds": age,
                "retries_used": _retry_state[tier],
                "restart_error": restart_msg,
            },
        )
        # If docker isn't available we'll never recover. Surface that
        # specifically so the operator knows the brain is degraded.
        if "docker CLI" in restart_msg or "docker socket" in restart_msg.lower():
            try:
                notify_fn(
                    title="Backup watcher cannot restart container",
                    detail=(
                        f"{detail}\n\n"
                        f"Recommended fix: ensure /var/run/docker.sock "
                        f"is bind-mounted into the brain container and "
                        f"the docker CLI is installed in the brain image."
                    ),
                    source="brain.backup_watcher",
                    severity="warning",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[BACKUP_WATCHER] notify_fn failed: %s", exc
                )
        return {
            "ok": False,
            "status": "restart_failed",
            "tier": tier,
            "container": container,
            "age_seconds": age,
            "retries_used": _retry_state[tier],
            "restart_error": restart_msg,
        }

    # 2b) Restart accepted — give the container time to spin up and
    # produce a fresh dump, then re-stat.
    sleep_fn(retry_delay)
    post_age = stat_fn(backup_dir, tier)
    if post_age is not None and post_age <= max_age_seconds:
        # Recovery! Write the resolved row (if a firing alert is open)
        # and reset the retry counter. The next cycle will short-circuit
        # at the freshness check.
        _retry_state[tier] = 0
        if await _firing_alert_exists(pool, alertname):
            await _emit_resolved_alert(
                pool,
                tier=tier,
                alertname=alertname,
                detail=(
                    f"Backup tier={tier} recovered after watcher restart: "
                    f"fresh dump appeared (age={post_age:.0f}s, "
                    f"threshold={max_age_seconds:.0f}s)."
                ),
            )
        await _emit_audit_event(
            pool,
            "probe.backup_watcher_recovered",
            (
                f"{tier}: fresh dump appeared after restart "
                f"(age={post_age:.0f}s)."
            ),
            extra={
                "tier": tier,
                "container": container,
                "age_seconds": post_age,
                "retries_used": used + 1,
            },
        )
        return {
            "ok": True,
            "status": "recovered",
            "tier": tier,
            "container": container,
            "age_seconds": post_age,
            "retries_used": used + 1,
        }

    # 2c) Still stale after the restart + delay. Audit the failed
    # attempt and let the next cycle decide whether to retry again or
    # escalate (depending on whether we've hit ``max_retries``).
    detail = (
        f"Backup tier={tier} still stale after restart "
        f"(age={post_age!r}s, threshold={max_age_seconds:.0f}s). "
        f"Used {_retry_state[tier]}/{max_retries} retries."
    )
    logger.warning("[BACKUP_WATCHER] %s", detail)
    await _emit_audit_event(
        pool,
        "probe.backup_watcher_retry_failed",
        detail,
        extra={
            "tier": tier,
            "container": container,
            "age_seconds": post_age,
            "retries_used": _retry_state[tier],
            "max_retries": max_retries,
        },
    )
    return {
        "ok": False,
        "status": "retry_failed",
        "tier": tier,
        "container": container,
        "age_seconds": post_age,
        "retries_used": _retry_state[tier],
    }


# ---------------------------------------------------------------------------
# Top-level probe entry point — called once per brain cycle.
# ---------------------------------------------------------------------------


async def run_backup_watcher_probe(
    pool: Any,
    *,
    stat_fn: Optional[Callable[[str, str], Optional[float]]] = None,
    restart_fn: Optional[Callable[[str], tuple[bool, str]]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    notify_fn: Optional[Callable[..., None]] = None,
    scan_sentinels_fn: Optional[
        Callable[[str], list[tuple[str, Path, dict[str, str]]]]
    ] = None,
) -> dict[str, Any]:
    """Single execution of the backup-watcher probe.

    Args:
        pool: asyncpg pool for app_settings + alert_events + audit_log.
        stat_fn: ``(backup_dir, tier) -> age_seconds | None`` — defaults
            to filesystem stat. Tests inject canned ages.
        restart_fn: ``(container) -> (ok, msg)`` — defaults to the
            real ``docker restart``. Tests inject a stub.
        sleep_fn: ``(seconds) -> None`` — defaults to ``time.sleep``.
            Tests inject a no-op so they don't wait two minutes.
        notify_fn: operator notifier callable. Defaults to
            :func:`brain.operator_notifier.notify_operator`. Used for
            the "docker is broken" surface AND the sentinel-found
            fallback path; per-tier escalation is left to the existing
            alert_events dispatcher pipeline.
        scan_sentinels_fn: ``(sentinel_dir) -> [(tier, path, parsed)]``
            — defaults to the real filesystem scan. Tests inject a
            canned list (Glad-Labs/poindexter#444).

    Returns a structured summary suitable for inclusion in
    ``brain_decisions`` / the cycle's ``probe_results`` map.
    """
    stat_fn = stat_fn or _latest_dump_age_seconds
    restart_fn = restart_fn or _restart_backup_container
    sleep_fn = sleep_fn or time.sleep
    notify_fn = notify_fn or notify_operator
    scan_sentinels_fn = scan_sentinels_fn or _scan_sentinel_dir

    config = await _read_config(pool)
    if not config["enabled"]:
        return {
            "ok": True,
            "status": "disabled",
            "detail": (
                f"Backup watcher disabled "
                f"(app_settings.{ENABLED_KEY}=false)"
            ),
            "tiers": {},
        }

    if not Path(config["backup_dir"]).is_dir():
        # Fail-loud: the dump directory the watcher can't see is the
        # exact failure mode this probe is supposed to catch. Surface
        # via notify_operator so the operator hears about it; the
        # probe itself returns a degraded summary so the brain cycle
        # keeps running.
        detail = (
            f"backup_watcher_backup_dir does not exist: "
            f"{config['backup_dir']!r}. Set "
            f"app_settings.{BACKUP_DIR_KEY} to the host path that "
            f"docker-compose.local.yml bind-mounts into the backup "
            f"containers (default: ~/.poindexter/backups/auto)."
        )
        logger.warning("[BACKUP_WATCHER] %s", detail)
        try:
            notify_fn(
                title="Backup watcher cannot read backup directory",
                detail=detail,
                source="brain.backup_watcher",
                severity="warning",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[BACKUP_WATCHER] notify_fn failed: %s", exc)
        await _emit_audit_event(
            pool,
            "probe.backup_watcher_dir_missing",
            detail,
            extra={"backup_dir": config["backup_dir"]},
        )
        return {
            "ok": False,
            "status": "dir_missing",
            "detail": detail,
            "tiers": {},
            "config": {k: v for k, v in config.items() if k != "enabled"},
        }

    # Run both tiers each cycle. They share retry bookkeeping but are
    # otherwise independent — a stuck daily doesn't gate the hourly
    # check.
    tier_summaries: dict[str, dict[str, Any]] = {}
    for tier in ("hourly", "daily"):
        try:
            tier_summaries[tier] = await _check_one_tier(
                pool,
                tier=tier,
                config=config,
                stat_fn=stat_fn,
                restart_fn=restart_fn,
                sleep_fn=sleep_fn,
                notify_fn=notify_fn,
            )
        except Exception as exc:  # noqa: BLE001
            # One tier blowing up shouldn't take the other down — log
            # and carry on so at least the partial check runs. The
            # brain cycle's outer try/except would catch this anyway,
            # but a tier-level catch keeps the summary structured.
            logger.warning(
                "[BACKUP_WATCHER] %s tier check raised: %s", tier, exc,
                exc_info=True,
            )
            tier_summaries[tier] = {
                "ok": False,
                "status": "exception",
                "tier": tier,
                "container": _CONTAINERS_BY_TIER[tier],
                "error": str(exc)[:200],
            }

    # dr-backup sentinel surfacing (Glad-Labs/poindexter#444). Runs
    # AFTER the in-stack age check so a degraded sentinel scan never
    # masks a stale-dump escalation. Wrapped in its own try/except for
    # the same reason ``_check_one_tier`` is — a sentinel-parsing bug
    # shouldn't take the whole probe down.
    try:
        sentinel_summary = await _check_sentinels(
            pool,
            sentinel_dir=config["sentinel_dir"],
            scan_fn=scan_sentinels_fn,
            notify_fn=notify_fn,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[BACKUP_WATCHER] sentinel scan raised: %s", exc, exc_info=True,
        )
        sentinel_summary = {
            "ok": False,
            "status": "exception",
            "detail": f"Sentinel scan raised: {type(exc).__name__}",
            "sentinels": [],
        }

    overall_ok = all(t.get("ok", False) for t in tier_summaries.values()) and bool(
        sentinel_summary.get("ok", False)
    )
    statuses = ", ".join(
        f"{tier}={t.get('status', 'unknown')}"
        for tier, t in tier_summaries.items()
    )
    return {
        "ok": overall_ok,
        "status": "ok" if overall_ok else "degraded",
        "detail": (
            f"Backup watcher cycle: {statuses}; "
            f"sentinels={sentinel_summary.get('status', 'unknown')}"
        ),
        "tiers": tier_summaries,
        "sentinels": sentinel_summary,
        "config": {k: v for k, v in config.items() if k != "enabled"},
    }


# ---------------------------------------------------------------------------
# Probe-Protocol adapter — for the registry-driven path. Mirrors
# ComposeDriftProbe's wrapper so this slots into the same registry
# without new infrastructure.
# ---------------------------------------------------------------------------


class BackupWatcherProbe:
    """Probe-Protocol-compatible wrapper around
    :func:`run_backup_watcher_probe`.
    """

    name: str = "backup_watcher"
    description: str = (
        "Watches the in-stack backup tiers (hourly, daily) for stale "
        "dumps and auto-retries via `docker restart` before letting "
        "the dispatcher page the operator."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult

        summary = await run_backup_watcher_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={
                "status": summary.get("status"),
                "tiers": {
                    tier: t.get("status")
                    for tier, t in (summary.get("tiers") or {}).items()
                },
                "sentinels": (summary.get("sentinels") or {}).get("status"),
            },
            severity="warning" if not summary.get("ok") else "info",
        )
