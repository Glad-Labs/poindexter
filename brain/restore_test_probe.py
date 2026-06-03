"""Restore-test probe (Glad-Labs/poindexter#441).

Backups are created (#385) and freshness-monitored (#388 backup_watcher),
but nothing verifies a dump actually RESTORES. A corrupt-but-fresh dump is
worse than none — it gives false confidence. This probe closes that gap.

Once per ``restore_test_interval_hours`` (default 24h) it:

1. picks the newest dump under ``<backup_dir>/<tier>/`` (the brain's
   read-only /host-backups mount),
2. spins a throwaway ``pgvector/pgvector:pg16`` container (no published
   ports, attached to the worker's docker network),
3. ``docker cp``s the dump in and ``pg_restore``s it,
4. re-runs the production migration runner against it via
   ``docker exec poindexter-worker python .../migrations_smoke.py``,
5. asserts critical tables (posts, app_settings, audit_log) survived,
6. tears the throwaway down.

Verification failures (corrupt dump, empty tables, smoke failure) page the
operator at ``error`` (Telegram + Discord). Infra failures (docker
unreachable, no dump) are ``warning`` (Discord only) — a transient docker
hiccup that merely prevented the test must not train the operator to
ignore Telegram.

Design parity with brain/backup_watcher.py + brain/smart_monitor.py:
stdlib + asyncpg only; every tunable an app_settings row; subprocess calls
degrade gracefully (logged, never raised); CREATE_NO_WINDOW on win32.
Blocking docker calls go through ``asyncio.to_thread`` so the brain event
loop is never blocked.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified for tests
    from brain.operator_notifier import notify_operator

logger = logging.getLogger("brain.restore_test_probe")

# --- app_settings keys ------------------------------------------------------
ENABLED_KEY = "restore_test_enabled"
INTERVAL_HOURS_KEY = "restore_test_interval_hours"
BACKUP_DIR_KEY = "restore_test_backup_dir"
TIER_KEY = "restore_test_tier"
POSTGRES_IMAGE_KEY = "restore_test_postgres_image"
RUN_SMOKE_KEY = "restore_test_run_migrations_smoke"
CRITICAL_TABLES_KEY = "restore_test_critical_tables"
MIN_ROW_COUNT_KEY = "restore_test_min_row_count"
PG_READY_TIMEOUT_KEY = "restore_test_pg_ready_timeout_seconds"
RESTORE_TIMEOUT_KEY = "restore_test_restore_timeout_seconds"
SMOKE_TIMEOUT_KEY = "restore_test_smoke_timeout_seconds"

DEFAULT_ENABLED = True
DEFAULT_INTERVAL_HOURS = 24
DEFAULT_BACKUP_DIR = "/host-backups/auto"
DEFAULT_TIER = "daily"
DEFAULT_POSTGRES_IMAGE = "pgvector/pgvector:pg16"
DEFAULT_RUN_SMOKE = True
DEFAULT_CRITICAL_TABLES = ["posts", "app_settings", "audit_log"]
DEFAULT_MIN_ROW_COUNT = 1
DEFAULT_PG_READY_TIMEOUT = 60
DEFAULT_RESTORE_TIMEOUT = 300
DEFAULT_SMOKE_TIMEOUT = 180

# Constants (not settings — changing them needs coordinated edits elsewhere).
THROWAWAY_CONTAINER = "poindexter-restore-test"
WORKER_CONTAINER = "poindexter-worker"
SMOKE_SCRIPT_PATH = "/opt/scripts/ci/migrations_smoke.py"
WORKER_BACKEND_ROOT = "/app"
TARGET_DB = "poindexter_brain"
DUMP_PREFIX = "poindexter_brain_"
DUMP_SUFFIX = ".dump"
PROBE_INTERVAL_SECONDS = 300

# Identifier guard for operator-supplied table names before SQL interpolation.
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Generous subprocess cap for docker run / rm / inspect / cp / exec wrappers.
_DOCKER_CMD_TIMEOUT = 60

# Module-level last verdict so a recovery (fail -> pass) emits one notify.
# None = unknown (first run since boot). True = last run passed.
_last_passed: Optional[bool] = None


def _reset_module_state() -> None:
    """Test helper — wipe the recovery-notify latch."""
    global _last_passed
    _last_passed = None


# --- app_settings reads (brain is standalone; hit the DB directly) ----------
async def _read_setting(pool: Any, key: str, default: Any) -> Any:
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[RESTORE_TEST] Could not read %s: %s — default %r",
            key, exc, default,
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


def _parse_tables(val: Any) -> list[str]:
    """Split a comma-separated table list, keeping only valid identifiers."""
    if not val:
        return list(DEFAULT_CRITICAL_TABLES)
    out: list[str] = []
    for raw in str(val).split(","):
        name = raw.strip()
        if name and _TABLE_NAME_RE.match(name):
            out.append(name)
    return out or list(DEFAULT_CRITICAL_TABLES)


async def _read_config(pool: Any) -> dict[str, Any]:
    return {
        "enabled": _coerce_bool(
            await _read_setting(pool, ENABLED_KEY, "true"), DEFAULT_ENABLED),
        "interval_hours": _coerce_int(
            await _read_setting(pool, INTERVAL_HOURS_KEY, DEFAULT_INTERVAL_HOURS),
            DEFAULT_INTERVAL_HOURS),
        "backup_dir": str(await _read_setting(
            pool, BACKUP_DIR_KEY, DEFAULT_BACKUP_DIR)).strip() or DEFAULT_BACKUP_DIR,
        "tier": str(await _read_setting(
            pool, TIER_KEY, DEFAULT_TIER)).strip() or DEFAULT_TIER,
        "postgres_image": str(await _read_setting(
            pool, POSTGRES_IMAGE_KEY, DEFAULT_POSTGRES_IMAGE)).strip()
            or DEFAULT_POSTGRES_IMAGE,
        "run_smoke": _coerce_bool(
            await _read_setting(pool, RUN_SMOKE_KEY, "true"), DEFAULT_RUN_SMOKE),
        "critical_tables": _parse_tables(
            await _read_setting(pool, CRITICAL_TABLES_KEY, None)),
        "min_row_count": _coerce_int(
            await _read_setting(pool, MIN_ROW_COUNT_KEY, DEFAULT_MIN_ROW_COUNT),
            DEFAULT_MIN_ROW_COUNT),
        "pg_ready_timeout": _coerce_int(
            await _read_setting(pool, PG_READY_TIMEOUT_KEY, DEFAULT_PG_READY_TIMEOUT),
            DEFAULT_PG_READY_TIMEOUT),
        "restore_timeout": _coerce_int(
            await _read_setting(pool, RESTORE_TIMEOUT_KEY, DEFAULT_RESTORE_TIMEOUT),
            DEFAULT_RESTORE_TIMEOUT),
        "smoke_timeout": _coerce_int(
            await _read_setting(pool, SMOKE_TIMEOUT_KEY, DEFAULT_SMOKE_TIMEOUT),
            DEFAULT_SMOKE_TIMEOUT),
    }


# --- dump discovery + daily gate --------------------------------------------
def _find_latest_dump(backup_dir: str, tier: str) -> Optional[str]:
    """Newest ``poindexter_brain_*.dump`` under ``<backup_dir>/<tier>/``.

    Falls back to any ``*.dump`` if none match the prefix. Skips ``.tmp``
    partials. ``None`` when the directory is missing or empty.
    """
    tier_dir = Path(backup_dir) / tier
    if not tier_dir.is_dir():
        return None
    best_path: Optional[str] = None
    best_mtime = -1.0
    fallback_path: Optional[str] = None
    fallback_mtime = -1.0
    try:
        with os.scandir(tier_dir) as it:
            for entry in it:
                name = entry.name
                if not name.endswith(DUMP_SUFFIX):
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                if name.startswith(DUMP_PREFIX):
                    if mtime > best_mtime:
                        best_mtime, best_path = mtime, entry.path
                elif mtime > fallback_mtime:
                    fallback_mtime, fallback_path = mtime, entry.path
    except OSError as exc:
        logger.warning("[RESTORE_TEST] Could not scan %s: %s", tier_dir, exc)
        return None
    return best_path or fallback_path


async def _seconds_since_last_run(pool: Any) -> Optional[float]:
    """Seconds since the newest terminal restore-test audit event, or None.

    Gating lives in the DB (not module memory) so a brain restart doesn't
    re-trigger the heavy run. Both completed + failed terminal events
    advance the gate, so a persistent failure (or docker-down) doesn't
    hammer docker every 5-min cycle.
    """
    try:
        return await pool.fetchval(
            """
            SELECT EXTRACT(EPOCH FROM (NOW() - created_at))
              FROM audit_log
             WHERE event_type IN (
                 'probe.restore_test_completed', 'probe.restore_test_failed')
             ORDER BY id DESC
             LIMIT 1
            """
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RESTORE_TEST] gate lookup failed: %s", exc)
        return None


# --- docker seams (sync wrappers; never raise) ------------------------------
def _run_cmd(cmd: list[str], timeout: int) -> tuple[int, str, str]:
    """Run a command. Returns (rc, stdout, stderr). Never raises.

    rc=-1 signals the process could not run / timed out (docker missing,
    timeout) as distinct from a non-zero exit of a process that ran.
    """
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True, "text": True, "timeout": timeout,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        r = subprocess.run(cmd, **kwargs)
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except FileNotFoundError:
        return -1, "", "docker CLI not on PATH"
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return -1, "", f"{type(exc).__name__}: {str(exc)[:160]}"


def _discover_network(worker: str = WORKER_CONTAINER) -> Optional[str]:
    """First docker network attached to the worker, or None."""
    rc, out, _ = _run_cmd(
        ["docker", "inspect", "-f",
         "{{range $k,$v := .NetworkSettings.Networks}}{{$k}}\n{{end}}", worker],
        _DOCKER_CMD_TIMEOUT,
    )
    if rc != 0:
        return None
    for line in out.splitlines():
        net = line.strip()
        if net:
            return net
    return None


def _remove_container(name: str) -> None:
    """`docker rm -f` — idempotent, errors ignored (may not exist)."""
    _run_cmd(["docker", "rm", "-f", name], _DOCKER_CMD_TIMEOUT)


def _start_container(name: str, image: str, network: Optional[str],
                     password: str) -> tuple[bool, str]:
    cmd = ["docker", "run", "-d", "--name", name,
           "-e", f"POSTGRES_PASSWORD={password}",
           "-e", f"POSTGRES_DB={TARGET_DB}"]
    if network:
        cmd += ["--network", network]
    cmd.append(image)
    rc, _, err = _run_cmd(cmd, _DOCKER_CMD_TIMEOUT)
    return (rc == 0), (err.strip() or f"docker run rc={rc}")


def _wait_pg_ready(name: str, timeout: int) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        rc, _, _ = _run_cmd(
            ["docker", "exec", name, "pg_isready", "-U", "postgres", "-q"],
            _DOCKER_CMD_TIMEOUT)
        if rc == 0:
            return True
        time.sleep(2)
    return False


def _copy_dump(name: str, host_path: str) -> tuple[bool, str]:
    rc, _, err = _run_cmd(
        ["docker", "cp", host_path, f"{name}:/tmp/restore.dump"],
        _DOCKER_CMD_TIMEOUT)
    return (rc == 0), (err.strip() or f"docker cp rc={rc}")


def _restore(name: str, timeout: int) -> tuple[int, str]:
    """pg_restore the copied dump. Returns (rc, stderr). rc is informational
    — benign non-zero exits happen with --no-owner; the row counts + smoke
    are the authoritative signal (see _decide_verdict)."""
    rc, _, err = _run_cmd(
        ["docker", "exec", name, "pg_restore",
         "--no-owner", "--no-privileges", "-U", "postgres",
         "-d", TARGET_DB, "/tmp/restore.dump"],
        timeout)
    return rc, err.strip()


def _table_count(name: str, db: str, table: str) -> Optional[int]:
    """Row count for ``table`` via psql, or None on bad name / query error."""
    if not _TABLE_NAME_RE.match(table):
        logger.warning("[RESTORE_TEST] rejecting non-identifier table %r", table)
        return None
    rc, out, _ = _run_cmd(
        ["docker", "exec", name, "psql", "-U", "postgres", "-d", db,
         "-tAc", f"SELECT count(*) FROM {table}"],
        _DOCKER_CMD_TIMEOUT)
    if rc != 0:
        return None
    try:
        return int(out.strip())
    except (TypeError, ValueError):
        return None


def _run_smoke(throwaway: str, db: str, password: str, timeout: int) -> tuple[bool, str]:
    """Run migrations_smoke.py inside the worker against the throwaway DB."""
    dsn = f"postgresql://postgres:{password}@{throwaway}:5432/{db}"
    rc, out, err = _run_cmd(
        ["docker", "exec",
         "-e", f"DATABASE_URL={dsn}",
         "-e", f"POINDEXTER_BACKEND_ROOT={WORKER_BACKEND_ROOT}",
         WORKER_CONTAINER, "python", SMOKE_SCRIPT_PATH],
        timeout)
    tail = (err or out).strip()[-300:]
    return (rc == 0), tail


async def _emit_audit(pool: Any, event: str, detail: str,
                      *, severity: str = "info",
                      extra: Optional[dict[str, Any]] = None) -> None:
    payload: dict[str, Any] = {"detail": detail}
    if extra:
        payload.update(extra)
    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, details, severity)
            VALUES ($1, $2, $3::jsonb, $4)
            """,
            event, "brain.restore_test_probe", json.dumps(payload), severity)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[RESTORE_TEST] audit write failed (%s): %s", event, exc)


# --- verdict policy ---------------------------------------------------------
def _decide_verdict(*, restore_rc: int, restore_stderr: str,
                    row_counts: dict[str, Optional[int]],
                    schema_migrations_count: Optional[int], min_count: int,
                    smoke_enabled: bool, smoke_ok: bool,
                    smoke_detail: str) -> tuple[bool, str, str]:
    """Combine the three signals into (passed, severity, detail).

    Authoritative signal is the DATA (row counts + schema_migrations +
    smoke), NOT pg_restore's exit code: a custom-format restore into a
    fresh DB routinely exits non-zero on benign role/ownership notices
    with --no-owner. Treating exit-code as fatal would page on healthy
    backups. So restore_rc/stderr are surfaced in the detail but never
    flip the verdict on their own.
    """
    problems: list[str] = []
    for table, count in row_counts.items():
        if count is None:
            problems.append(f"{table}: query failed / table missing")
        elif count < min_count:
            problems.append(f"{table}: {count} rows (< {min_count})")
    if not schema_migrations_count:
        problems.append("schema_migrations: empty")
    if smoke_enabled and not smoke_ok:
        problems.append(f"migrations_smoke failed: {smoke_detail}")

    if problems:
        detail = "Restore verification FAILED — " + "; ".join(problems)
        if restore_rc != 0:
            detail += f" (pg_restore rc={restore_rc}: {restore_stderr[:160]})"
        return False, "error", detail

    counts = ", ".join(f"{t}={c}" for t, c in row_counts.items())
    detail = (f"Restore OK — {counts}, schema_migrations="
              f"{schema_migrations_count}"
              + (", smoke=pass" if smoke_enabled else ", smoke=skipped"))
    if restore_rc != 0:
        detail += f" (benign pg_restore rc={restore_rc})"
    return True, "info", detail


# --- orchestrator -----------------------------------------------------------
async def _notify(notify_fn: Callable[..., Any], *,
                  title: str, detail: str, severity: str) -> None:
    try:
        notify_fn(title=title, detail=detail,
                  source="brain.restore_test_probe", severity=severity)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RESTORE_TEST] notify_fn failed: %s", exc)


async def _infra_fail(pool: Any, notify_fn: Callable[..., Any],
                      detail: str) -> dict[str, Any]:
    """Infra-level failure: warning severity (Discord only), gate advances."""
    global _last_passed
    logger.warning("[RESTORE_TEST] %s", detail)
    await _emit_audit(pool, "probe.restore_test_failed", detail,
                      severity="warning", extra={"reason": "infra"})
    if _last_passed is not False:
        await _notify(notify_fn, title="Restore test could not run",
                      detail=detail, severity="warning")
    _last_passed = False
    return {"ok": False, "status": "infra_error", "detail": detail}


async def run_restore_test_probe(
    pool: Any, *,
    find_dump_fn: Optional[Callable[[str, str], Optional[str]]] = None,
    discover_network_fn: Optional[Callable[[], Optional[str]]] = None,
    start_fn: Optional[Callable[..., tuple[bool, str]]] = None,
    wait_ready_fn: Optional[Callable[[str, int], bool]] = None,
    copy_fn: Optional[Callable[[str, str], tuple[bool, str]]] = None,
    restore_fn: Optional[Callable[[str, int], tuple[int, str]]] = None,
    count_fn: Optional[Callable[[str, str, str], Optional[int]]] = None,
    smoke_fn: Optional[Callable[..., tuple[bool, str]]] = None,
    teardown_fn: Optional[Callable[[str], None]] = None,
    notify_fn: Optional[Callable[..., Any]] = None,
) -> dict[str, Any]:
    """One execution of the restore-test probe. Seams default to the real
    docker impls; tests inject stubs so no container ever runs. Blocking
    docker calls go through ``asyncio.to_thread`` so the brain event loop
    is never blocked.
    """
    global _last_passed
    find_dump_fn = find_dump_fn or _find_latest_dump
    discover_network_fn = discover_network_fn or _discover_network
    start_fn = start_fn or _start_container
    wait_ready_fn = wait_ready_fn or _wait_pg_ready
    copy_fn = copy_fn or _copy_dump
    restore_fn = restore_fn or _restore
    count_fn = count_fn or _table_count
    smoke_fn = smoke_fn or _run_smoke
    teardown_fn = teardown_fn or _remove_container
    notify_fn = notify_fn or notify_operator

    cfg = await _read_config(pool)
    if not cfg["enabled"]:
        return {"ok": True, "status": "disabled",
                "detail": f"Disabled (app_settings.{ENABLED_KEY}=false)"}

    since = await _seconds_since_last_run(pool)
    if since is not None and since < cfg["interval_hours"] * 3600:
        return {"ok": True, "status": "skipped",
                "detail": (f"Last run {since/3600:.1f}h ago "
                           f"(< {cfg['interval_hours']}h interval)")}

    dump = await asyncio.to_thread(find_dump_fn, cfg["backup_dir"], cfg["tier"])
    if not dump:
        detail = (f"No dump under {cfg['backup_dir']}/{cfg['tier']}/. "
                  f"If backups are running this should appear within a day.")
        await _emit_audit(pool, "probe.restore_test_failed", detail,
                          severity="warning", extra={"reason": "no_dump"})
        if _last_passed is not False:
            await _notify(notify_fn,
                          title="Restore test: no dump to verify",
                          detail=detail, severity="warning")
        _last_passed = False
        return {"ok": False, "status": "no_dump", "detail": detail}

    password = secrets.token_hex(16)
    network = await asyncio.to_thread(discover_network_fn)
    started = False
    try:
        # Stale-cleanup: a prior crashed run may have left the container,
        # which would make `docker run --name` fail. Idempotent.
        await asyncio.to_thread(teardown_fn, THROWAWAY_CONTAINER)
        ok, msg = await asyncio.to_thread(
            start_fn, THROWAWAY_CONTAINER, cfg["postgres_image"], network, password)
        if not ok:
            return await _infra_fail(
                pool, notify_fn, f"Could not start throwaway container: {msg}")
        started = True

        if not await asyncio.to_thread(
                wait_ready_fn, THROWAWAY_CONTAINER, cfg["pg_ready_timeout"]):
            return await _infra_fail(
                pool, notify_fn,
                f"Throwaway pg not ready within {cfg['pg_ready_timeout']}s")

        ok, msg = await asyncio.to_thread(copy_fn, THROWAWAY_CONTAINER, dump)
        if not ok:
            return await _infra_fail(pool, notify_fn, f"docker cp failed: {msg}")

        restore_rc, restore_err = await asyncio.to_thread(
            restore_fn, THROWAWAY_CONTAINER, cfg["restore_timeout"])

        row_counts: dict[str, Optional[int]] = {}
        for table in cfg["critical_tables"]:
            row_counts[table] = await asyncio.to_thread(
                count_fn, THROWAWAY_CONTAINER, TARGET_DB, table)
        schema_migrations = await asyncio.to_thread(
            count_fn, THROWAWAY_CONTAINER, TARGET_DB, "schema_migrations")

        smoke_ok, smoke_detail = True, "skipped"
        if cfg["run_smoke"]:
            if network is None:
                smoke_ok, smoke_detail = True, "skipped (no docker network)"
            else:
                smoke_ok, smoke_detail = await asyncio.to_thread(
                    smoke_fn, THROWAWAY_CONTAINER, TARGET_DB, password,
                    cfg["smoke_timeout"])

        passed, severity, detail = _decide_verdict(
            restore_rc=restore_rc, restore_stderr=restore_err,
            row_counts=row_counts, schema_migrations_count=schema_migrations,
            min_count=cfg["min_row_count"], smoke_enabled=cfg["run_smoke"],
            smoke_ok=smoke_ok, smoke_detail=smoke_detail)

        event = ("probe.restore_test_completed" if passed
                 else "probe.restore_test_failed")
        await _emit_audit(pool, event, detail,
                          severity="info" if passed else "warning",
                          extra={"dump": os.path.basename(dump),
                                 "restore_rc": restore_rc,
                                 "row_counts": row_counts})

        if not passed:
            await _notify(
                notify_fn,
                title="Restore test FAILED — latest backup may be corrupt",
                detail=detail + f"\n\nDump: {os.path.basename(dump)}",
                severity=severity)
        elif _last_passed is False:
            await _notify(notify_fn, title="Restore test recovered",
                          detail=detail, severity="info")
        _last_passed = passed
        return {"ok": passed,
                "status": "passed" if passed else "verification_failed",
                "detail": detail, "dump": os.path.basename(dump)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[RESTORE_TEST] probe raised: %s", exc, exc_info=True)
        detail = f"Restore test errored: {type(exc).__name__}: {str(exc)[:160]}"
        await _emit_audit(pool, "probe.restore_test_failed", detail,
                          severity="warning", extra={"reason": "exception"})
        _last_passed = False
        return {"ok": False, "status": "infra_error", "detail": detail}
    finally:
        if started:
            try:
                await asyncio.to_thread(teardown_fn, THROWAWAY_CONTAINER)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[RESTORE_TEST] teardown failed: %s", exc)


# --- Probe-Protocol wrapper -------------------------------------------------
class RestoreTestProbe:
    """Probe-Protocol wrapper around run_restore_test_probe."""

    name: str = "restore_test"
    description: str = (
        "Daily: pg_restore the latest dump into a throwaway pgvector "
        "container, re-run the migration runner, assert critical tables "
        "survived. Pages on real corruption."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult
        summary = await run_restore_test_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={"status": summary.get("status")},
            severity="warning" if not summary.get("ok") else "info")
