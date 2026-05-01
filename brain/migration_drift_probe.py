"""Migration drift probe + auto-restart workflow (Glad-Labs/poindexter#228).

The brain daemon already monitors *services*. This probe extends that to
*schema state*: it watches for the case where the worker container has
shipped a new migration file but the migration runner hasn't applied it
to the live DB. Today the operator finds out via Telegram only after
``poindexter_unapplied_migrations_count`` has been > 0 for 30 minutes
(Prometheus alert from #227), and even then the only fix is to manually
``docker restart poindexter-worker``. This probe automates that loop.

Probe behavior every 5-min cycle:

1. Compute drift by querying the worker's ``/api/health`` endpoint, which
   already exposes a ``migrations`` block (#270 / #313) with applied,
   pending, latest_applied, and drift fields. Going through the worker
   means the brain doesn't need its own copy of the migrations directory
   bind-mounted in.
2. If drift is 0 — emit a success result + audit event, return.
3. If drift > 0:
   a. Always emit a ``probe.migration_drift_detected`` audit event.
   b. If ``migration_drift_auto_recover_enabled = true`` (an
      app_setting that defaults to ``"false"`` for safety in case of bad
      migrations) — restart ``poindexter-worker`` via ``docker restart``
      using the Docker socket the brain container already mounts at
      ``/var/run/docker.sock`` (see docker-compose.local.yml). Then wait
      up to 60s for the worker to report healthy via /api/health and
      re-check drift. If it cleared, emit
      ``probe.migration_drift_recovered`` and stop. If it didn't, escalate
      via :func:`brain.operator_notifier.notify_operator`.
   c. If auto-recover is disabled — fire a single ``notify_operator()``
      so the operator knows there's drift, capped at one per cycle so a
      stuck restart-loop can't blast Telegram.

Standalone module — only depends on stdlib + asyncpg (already a brain
dep). Mirrors the patterns of ``brain/operator_url_probe.py`` and
``brain/health_probes.py`` so it slots into the existing probe registry
without new infrastructure.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

try:  # Flat import when brain/ is on sys.path (container runtime).
    from operator_notifier import notify_operator
except ImportError:  # pragma: no cover — package-qualified for tests
    from brain.operator_notifier import notify_operator

try:
    from docker_utils import localize_url
except ImportError:  # pragma: no cover — package-qualified for tests
    from brain.docker_utils import localize_url

logger = logging.getLogger("brain.migration_drift_probe")

# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# App_settings key controlling auto-recover behavior. Defaults to
# ``"false"`` in the seed migration so a bad migration can't trigger a
# restart loop on a fresh install. Operator flips it via
# ``poindexter set migration_drift_auto_recover_enabled true`` once
# they're confident the pipeline restart-on-drift behavior is right for
# their workload.
AUTO_RECOVER_SETTING_KEY = "migration_drift_auto_recover_enabled"

# Worker container name — the only thing we restart. Kept as a constant
# rather than a setting because it matches docker-compose.local.yml's
# container_name and changing it would break dozens of other things.
WORKER_CONTAINER = "poindexter-worker"

# How long to wait for the worker to report healthy after a restart
# before declaring the auto-recover attempt failed and escalating.
RESTART_WAIT_SECONDS = 60

# Poll interval while waiting for the worker to come back.
RESTART_POLL_INTERVAL_SECONDS = 3

# HTTP timeouts for /api/health queries — short so a hung worker doesn't
# stall the brain cycle.
HEALTH_TIMEOUT_SECONDS = 10

# Default worker URL inside the docker network. Brain container resolves
# ``poindexter-worker:8002`` via service discovery; on the host side we
# use ``localhost:8002`` (localize_url handles the rewrite).
_DEFAULT_WORKER_URL = "http://poindexter-worker:8002"

# Probe interval — the brain cycle is 5 minutes; we run on every cycle.
PROBE_INTERVAL_SECONDS = 300

# Module-level state — track last-cycle notification so we don't blast
# Telegram when auto-recover is disabled and drift persists. Reset to
# ``None`` when drift clears so a fresh drift event re-triggers a notify.
_last_notify_drift_count: int | None = None

# Expected ``pg_class.relkind`` per relation, post-migration. Migration
# 0125 (closes Glad-Labs/poindexter#329) unified ``content_tasks`` to be
# a VIEW in both dev and prod — relkind 'v'. Add new entries here as
# similar table-vs-view contracts get codified by future migrations. The
# probe checks each entry on every cycle and notifies when actual
# != expected so a stale environment can't silently drift back into the
# split state.
_EXPECTED_RELKINDS: dict[str, str] = {
    "content_tasks": "v",
}

# Module-level dedupe for relkind-mismatch notifications — same shape as
# the drift-count dedupe. A tuple of (relname, expected, actual) so a
# different relation's mismatch triggers a fresh notify even if the
# previous one is still outstanding.
_last_relkind_notify_key: tuple[str, str, str] | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_worker_health_url() -> str:
    """Return the URL of the worker's ``/api/health`` endpoint.

    Honors the ``WORKER_API_URL`` and ``API_URL`` env vars (set by
    docker-compose.local.yml on the brain container) and falls back to
    the docker-network service name. ``localize_url`` rewrites
    ``localhost`` to ``host.docker.internal`` when running inside a
    container, which keeps the same URL working from both host and
    container processes.
    """
    base = (
        os.getenv("WORKER_API_URL")
        or os.getenv("API_URL")
        or _DEFAULT_WORKER_URL
    )
    base = localize_url(base).rstrip("/")
    return f"{base}/api/health"


def _fetch_health(url: str | None = None, timeout: int = HEALTH_TIMEOUT_SECONDS) -> dict[str, Any]:
    """GET the worker's /api/health and return the parsed JSON body.

    Returns ``{"_error": str}`` on failure rather than raising, so the
    probe can degrade gracefully and keep emitting structured results.
    """
    target = url or _resolve_worker_health_url()
    try:
        req = urllib.request.Request(
            target,
            headers={"User-Agent": "Poindexter-MigrationDriftProbe/1.0"},
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        body = resp.read()
        return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        return {"_error": f"HTTP {exc.code}: {exc.reason}"}
    except Exception as exc:
        return {"_error": f"{type(exc).__name__}: {str(exc)[:160]}"}


def _drift_from_health(health: dict[str, Any]) -> dict[str, Any]:
    """Extract the migrations block from a /api/health response.

    Returns a normalized dict ``{ok, pending, applied, latest_applied,
    error}``. ``ok=False`` means the health endpoint didn't expose a
    usable migrations block (worker down, old build without #270/#313,
    etc.) — caller decides whether to escalate that as drift or as a
    separate "can't tell" condition.
    """
    if "_error" in health:
        return {
            "ok": False,
            "pending": None,
            "applied": None,
            "latest_applied": None,
            "error": health["_error"],
        }

    components = health.get("components") or {}
    migrations = components.get("migrations") or {}

    # The worker reports an "unknown" status when it can't read the
    # schema_migrations table — treat as can't-tell, not as drift.
    if isinstance(migrations, dict) and "error" in migrations:
        return {
            "ok": False,
            "pending": None,
            "applied": None,
            "latest_applied": None,
            "error": migrations.get("error", "migrations block error"),
        }

    pending = migrations.get("pending")
    applied = migrations.get("applied")
    latest = migrations.get("latest_applied")

    if pending is None:
        return {
            "ok": False,
            "pending": None,
            "applied": applied,
            "latest_applied": latest,
            "error": "migrations block missing 'pending' field",
        }

    return {
        "ok": True,
        "pending": int(pending),
        "applied": int(applied or 0),
        "latest_applied": latest,
        "error": None,
    }


async def _check_relkind_mismatches(pool) -> list[dict[str, str]]:
    """Compare actual ``pg_class.relkind`` against the expected shape for
    each relation in :data:`_EXPECTED_RELKINDS`.

    Returns a list of mismatches, each with ``relname``, ``expected``,
    and ``actual`` keys (``actual`` is the empty string when the
    relation is missing entirely). An empty list means every relation
    matches its expected shape — the contract is intact.

    Closes Glad-Labs/poindexter#329 follow-up: this is the early-warning
    signal that a future migration silently regressed the post-#329
    table/view contract. The probe doesn't auto-fix relkind mismatches
    (a wrong shape usually means data needs to be re-imported / converted
    by hand) — it just notifies so the operator can investigate.

    Best-effort — never raises. A query failure here just logs and
    returns ``[]`` so the rest of the drift probe keeps working.
    """
    if not _EXPECTED_RELKINDS:
        return []

    try:
        rows = await pool.fetch(
            """
            SELECT c.relname, c.relkind
              FROM pg_class c
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'public'
               AND c.relname = ANY($1::text[])
            """,
            list(_EXPECTED_RELKINDS.keys()),
        )
    except Exception as exc:
        logger.warning(
            "[MIGRATION_DRIFT] relkind probe query failed: %s", exc
        )
        return []

    # asyncpg returns relkind as bytes on some driver versions
    # (Postgres "char" type); normalise to str. Same coercion 0114 + 0125
    # do — keep this in sync.
    actual_by_name: dict[str, str] = {}
    for row in rows:
        rk = row["relkind"]
        if isinstance(rk, (bytes, bytearray)):
            rk = rk.decode("ascii")
        actual_by_name[row["relname"]] = rk or ""

    mismatches: list[dict[str, str]] = []
    for relname, expected in _EXPECTED_RELKINDS.items():
        actual = actual_by_name.get(relname, "")
        if actual != expected:
            mismatches.append({
                "relname": relname,
                "expected": expected,
                "actual": actual,
            })
    return mismatches


async def _read_auto_recover_enabled(pool) -> bool:
    """Read ``migration_drift_auto_recover_enabled`` from app_settings.

    Defaults to ``False`` if the row is missing or unparseable — the
    seed migration installs the row with ``"false"`` so this default
    only matters during the brief window between a fresh install and
    the first migration run.
    """
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1",
            AUTO_RECOVER_SETTING_KEY,
        )
    except Exception as exc:
        logger.warning(
            "[MIGRATION_DRIFT] Could not read %s from app_settings: %s",
            AUTO_RECOVER_SETTING_KEY, exc,
        )
        return False

    if val is None:
        return False
    return str(val).strip().lower() in ("true", "1", "yes", "on")


async def _emit_relkind_audit_and_notify(
    pool,
    mismatches: list[dict[str, str]],
    *,
    notify_fn,
) -> None:
    """Emit audit + notify_operator when ``_check_relkind_mismatches``
    finds anything. Capped via :data:`_last_relkind_notify_key` so a
    persistent mismatch doesn't blast Telegram every cycle.

    Best-effort — never raises so the caller's drift probe keeps
    running even if audit / notify is temporarily broken.
    """
    global _last_relkind_notify_key

    if not mismatches:
        # Reset the dedupe key so a fresh mismatch re-notifies later.
        if _last_relkind_notify_key is not None:
            logger.info(
                "[MIGRATION_DRIFT] Relkind contract restored (was %s)",
                _last_relkind_notify_key,
            )
            _last_relkind_notify_key = None
        return

    # Audit every mismatch every cycle — cheap and useful for the
    # post-incident timeline. Notification is the rate-limited part.
    for m in mismatches:
        await _emit_audit_event(
            pool,
            "probe.migration_relkind_mismatch",
            (
                f"Relation '{m['relname']}' has relkind="
                f"{m['actual']!r}, expected {m['expected']!r}"
            ),
            extra=m,
        )

    # Dedupe key is the SET of mismatches — any change re-notifies.
    key = tuple(
        (m["relname"], m["expected"], m["actual"])
        for m in mismatches
    )
    # Reduce to a stable tuple-of-tuples for comparison; we store just
    # the first one for the human-friendly comparison field below.
    first = mismatches[0]
    new_key = (first["relname"], first["expected"], first["actual"])

    if _last_relkind_notify_key == new_key and len(mismatches) == 1:
        # Same single mismatch as last cycle — already notified.
        logger.debug(
            "[MIGRATION_DRIFT] Relkind mismatch unchanged (%s) — "
            "skipping duplicate notification",
            key,
        )
        return

    summary_lines = [
        f"- {m['relname']}: actual={m['actual']!r}, expected={m['expected']!r}"
        for m in mismatches
    ]
    detail = (
        "Database schema drift detected — table/view contract regressed:\n"
        + "\n".join(summary_lines)
        + "\n\nUsually means a migration was hand-edited or skipped. "
        "Check `pg_class.relkind` in the live DB and re-apply the "
        "migration that originally established this contract "
        "(see migration 0125 for content_tasks)."
    )

    try:
        notify_fn(
            title=(
                f"Schema relkind mismatch on {len(mismatches)} relation(s)"
            ),
            detail=detail,
            source="brain.migration_drift_probe",
            severity="warning",
        )
        _last_relkind_notify_key = new_key
    except Exception as exc:
        logger.warning(
            "[MIGRATION_DRIFT] notify_fn failed for relkind mismatch: %s",
            exc,
        )


async def _emit_audit_event(
    pool,
    event: str,
    detail: str,
    *,
    pending: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write a row to ``audit_log`` for downstream observability.

    Mirrors the schema audit_log_bg uses (event_type, source, details,
    severity) so the brain's audit events show up alongside the
    pipeline's. Best-effort — never raises.
    """
    payload: dict[str, Any] = {"detail": detail}
    if pending is not None:
        payload["pending"] = pending
    if extra:
        payload.update(extra)

    try:
        await pool.execute(
            """
            INSERT INTO audit_log (event_type, source, details, severity)
            VALUES ($1, $2, $3::jsonb, $4)
            """,
            event,
            "brain.migration_drift_probe",
            json.dumps(payload),
            "warning" if "detected" in event else "info",
        )
    except Exception as exc:
        # audit_log table may not exist on a very fresh install — log
        # and carry on. The probe still does its job via notify_operator.
        logger.debug(
            "[MIGRATION_DRIFT] Could not write audit event %s: %s",
            event, exc,
        )


# ---------------------------------------------------------------------------
# Worker restart
# ---------------------------------------------------------------------------


def _restart_worker_container() -> tuple[bool, str]:
    """Run ``docker restart poindexter-worker``. Returns (ok, message).

    The brain container has /var/run/docker.sock bind-mounted (see
    docker-compose.local.yml) and the docker CLI installed in its image
    (see brain/Dockerfile). Never raises — caller handles the bool.
    """
    try:
        kwargs: dict[str, Any] = {
            "capture_output": True,
            "text": True,
            "timeout": 30,
        }
        if os.name == "nt":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        result = subprocess.run(
            ["docker", "restart", WORKER_CONTAINER],
            **kwargs,
        )
        if result.returncode == 0:
            return True, f"Restarted {WORKER_CONTAINER}"
        return False, (
            f"docker restart {WORKER_CONTAINER} exit {result.returncode}: "
            f"{(result.stderr or '').strip()[:200]}"
        )
    except FileNotFoundError:
        return False, "docker CLI not on PATH (brain container missing docker binary?)"
    except subprocess.TimeoutExpired:
        return False, f"docker restart {WORKER_CONTAINER} timed out after 30s"
    except Exception as exc:
        return False, f"docker restart error: {type(exc).__name__}: {str(exc)[:160]}"


def _wait_for_worker_healthy(
    deadline_seconds: int = RESTART_WAIT_SECONDS,
    poll_interval: float = RESTART_POLL_INTERVAL_SECONDS,
    sleep_fn=time.sleep,
    health_url: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Block until the worker reports healthy via /api/health, or timeout.

    Returns ``(ok, last_health_dict)``. ``ok=False`` on timeout — caller
    should treat that as a failed auto-recover and escalate.

    ``sleep_fn`` is injected so tests can substitute a no-op without
    making the test sleep for real.
    """
    deadline = time.time() + deadline_seconds
    last: dict[str, Any] = {}
    while time.time() < deadline:
        last = _fetch_health(url=health_url, timeout=5)
        status = last.get("status")
        if status in ("healthy", "degraded", "ok"):
            return True, last
        sleep_fn(poll_interval)
    return False, last


# ---------------------------------------------------------------------------
# Top-level probe entry point
# ---------------------------------------------------------------------------


async def run_migration_drift_probe(
    pool,
    *,
    notify_fn=None,
    restart_fn=None,
    wait_fn=None,
    health_fetcher=None,
) -> dict[str, Any]:
    """Single execution of the migration-drift probe.

    Args:
        pool: asyncpg pool for app_settings + audit_log writes.
        notify_fn: operator notifier callable. Defaults to
            :func:`brain.operator_notifier.notify_operator`. Tests inject
            a stub.
        restart_fn: docker-restart callable. Defaults to
            :func:`_restart_worker_container`. Tests inject a stub so
            no real ``docker restart`` runs.
        wait_fn: callable that waits for the worker to report healthy.
            Defaults to :func:`_wait_for_worker_healthy`. Tests inject a
            stub.
        health_fetcher: callable that returns a parsed /api/health dict.
            Defaults to :func:`_fetch_health`. Tests inject canned
            responses.

    Returns a summary dict suitable for storage in brain_knowledge or
    inclusion in the probe results map.
    """
    global _last_notify_drift_count

    notify_fn = notify_fn or notify_operator
    restart_fn = restart_fn or _restart_worker_container
    wait_fn = wait_fn or _wait_for_worker_healthy
    health_fetcher = health_fetcher or _fetch_health

    # ---- 1) Initial drift check via worker /api/health ----------------------
    health = health_fetcher()
    drift = _drift_from_health(health)

    if not drift["ok"]:
        # Can't tell — log + audit but don't escalate or restart. The
        # /api/health worker_error_rate probe in health_probes.py owns
        # the "worker is down" alert path; we don't want to double up.
        detail = (
            f"Could not read migrations block from worker: "
            f"{drift.get('error', 'unknown')}"
        )
        logger.info("[MIGRATION_DRIFT] %s", detail)
        await _emit_audit_event(
            pool, "probe.migration_drift_unknown", detail
        )
        return {
            "ok": True,  # treat as not-failing — separate concern from drift
            "status": "unknown",
            "detail": detail,
            "pending": None,
            "auto_recover_enabled": await _read_auto_recover_enabled(pool),
        }

    pending = drift["pending"]
    auto_recover_enabled = await _read_auto_recover_enabled(pool)

    # ---- 2) Happy path: no drift -------------------------------------------
    if pending == 0:
        # Reset the dedupe counter so the next drift re-notifies.
        if _last_notify_drift_count is not None:
            logger.info(
                "[MIGRATION_DRIFT] Drift cleared (was %d pending, now 0)",
                _last_notify_drift_count,
            )
            _last_notify_drift_count = None

        # Even with zero pending migrations, the post-migration shape
        # contract can drift (e.g. someone hand-edits prod via psql). The
        # relkind probe catches that early — closes the table-vs-view
        # gap that #329 exposed.
        relkind_mismatches = await _check_relkind_mismatches(pool)
        await _emit_relkind_audit_and_notify(
            pool, relkind_mismatches, notify_fn=notify_fn
        )

        await _emit_audit_event(
            pool,
            "probe.migration_drift_ok",
            f"No drift: applied={drift['applied']}, latest={drift['latest_applied']}",
            pending=0,
        )
        return {
            "ok": True,
            "status": "no_drift",
            "detail": (
                f"No drift: {drift['applied']} migration(s) applied, "
                f"latest={drift['latest_applied']}"
            ),
            "pending": 0,
            "applied": drift["applied"],
            "latest_applied": drift["latest_applied"],
            "auto_recover_enabled": auto_recover_enabled,
            "relkind_mismatches": relkind_mismatches,
        }

    # ---- 3) Drift > 0 — always audit it ------------------------------------
    detected_detail = (
        f"{pending} migration(s) pending — applied={drift['applied']}, "
        f"latest_applied={drift['latest_applied']}"
    )
    logger.warning("[MIGRATION_DRIFT] %s", detected_detail)
    await _emit_audit_event(
        pool,
        "probe.migration_drift_detected",
        detected_detail,
        pending=pending,
        extra={"auto_recover_enabled": auto_recover_enabled},
    )

    # ---- 4a) Auto-recover disabled — single notify, return -----------------
    if not auto_recover_enabled:
        # Cap at one notification per cycle, and only re-notify when the
        # drift count changes (so a stuck pending=2 doesn't spam every
        # 5 minutes — but a new migration arriving (pending 2 -> 3) does).
        if _last_notify_drift_count != pending:
            try:
                notify_fn(
                    title=f"Migration drift detected ({pending} pending)",
                    detail=(
                        f"{detected_detail}\n\n"
                        f"Auto-recover is DISABLED "
                        f"(app_settings.{AUTO_RECOVER_SETTING_KEY}=false).\n"
                        f"Recommended fix: restart the worker to apply "
                        f"pending migrations, or enable auto-recover via "
                        f"`poindexter set {AUTO_RECOVER_SETTING_KEY} true`."
                    ),
                    source="brain.migration_drift_probe",
                    severity="warning",
                )
                _last_notify_drift_count = pending
            except Exception as exc:
                logger.warning(
                    "[MIGRATION_DRIFT] notify_fn failed: %s", exc
                )
        else:
            logger.debug(
                "[MIGRATION_DRIFT] Drift unchanged at %d pending — "
                "skipping duplicate notification",
                pending,
            )
        return {
            "ok": False,
            "status": "drift_detected_no_recover",
            "detail": detected_detail,
            "pending": pending,
            "applied": drift["applied"],
            "latest_applied": drift["latest_applied"],
            "auto_recover_enabled": False,
            "notified": _last_notify_drift_count == pending,
        }

    # ---- 4b) Auto-recover enabled — restart worker, re-check ---------------
    logger.info(
        "[MIGRATION_DRIFT] Auto-recover enabled — restarting %s",
        WORKER_CONTAINER,
    )
    restart_ok, restart_msg = restart_fn()
    if not restart_ok:
        # Restart itself failed — escalate immediately. We don't want
        # to silently fall through and pretend recovery worked.
        try:
            notify_fn(
                title=f"Migration drift auto-recover FAILED to restart {WORKER_CONTAINER}",
                detail=(
                    f"{detected_detail}\n\n"
                    f"docker restart failed: {restart_msg}\n\n"
                    f"Recommended fix: manually restart {WORKER_CONTAINER} "
                    f"and investigate why the brain container can't reach "
                    f"the docker socket."
                ),
                source="brain.migration_drift_probe",
                severity="critical",
            )
        except Exception as exc:
            logger.warning("[MIGRATION_DRIFT] notify_fn failed: %s", exc)
        await _emit_audit_event(
            pool,
            "probe.migration_drift_recover_failed",
            f"docker restart failed: {restart_msg}",
            pending=pending,
        )
        _last_notify_drift_count = pending
        return {
            "ok": False,
            "status": "recover_restart_failed",
            "detail": f"{detected_detail} — restart failed: {restart_msg}",
            "pending": pending,
            "auto_recover_enabled": True,
        }

    # Wait for the worker to come back healthy.
    healthy, post_health = wait_fn()
    if not healthy:
        try:
            notify_fn(
                title=f"Migration drift auto-recover: {WORKER_CONTAINER} did not come back healthy",
                detail=(
                    f"{detected_detail}\n\n"
                    f"Restarted {WORKER_CONTAINER} but it did not report "
                    f"healthy within {RESTART_WAIT_SECONDS}s.\n"
                    f"Last health response: {post_health.get('_error') or post_health.get('status', 'unknown')}\n\n"
                    f"Recommended fix: check `docker logs {WORKER_CONTAINER}` "
                    f"for migration errors. Bad migrations are why "
                    f"{AUTO_RECOVER_SETTING_KEY} defaults to false — the "
                    f"worker may be crash-looping on a broken migration."
                ),
                source="brain.migration_drift_probe",
                severity="critical",
            )
        except Exception as exc:
            logger.warning("[MIGRATION_DRIFT] notify_fn failed: %s", exc)
        await _emit_audit_event(
            pool,
            "probe.migration_drift_recover_failed",
            f"Worker did not become healthy after restart within {RESTART_WAIT_SECONDS}s",
            pending=pending,
        )
        _last_notify_drift_count = pending
        return {
            "ok": False,
            "status": "recover_unhealthy",
            "detail": (
                f"{detected_detail} — restarted but worker not healthy "
                f"within {RESTART_WAIT_SECONDS}s"
            ),
            "pending": pending,
            "auto_recover_enabled": True,
        }

    # Worker is healthy — re-check drift.
    post_drift = _drift_from_health(post_health)
    post_pending = post_drift.get("pending")

    if post_drift["ok"] and post_pending == 0:
        recovered_detail = (
            f"Drift cleared after restart: was {pending}, now 0 "
            f"(applied={post_drift['applied']}, "
            f"latest={post_drift['latest_applied']})"
        )
        logger.info("[MIGRATION_DRIFT] %s", recovered_detail)
        await _emit_audit_event(
            pool,
            "probe.migration_drift_recovered",
            recovered_detail,
            pending=0,
            extra={"previous_pending": pending},
        )
        _last_notify_drift_count = None
        return {
            "ok": True,
            "status": "recovered",
            "detail": recovered_detail,
            "pending": 0,
            "previous_pending": pending,
            "applied": post_drift["applied"],
            "latest_applied": post_drift["latest_applied"],
            "auto_recover_enabled": True,
        }

    # Restart succeeded, worker is healthy, but drift is STILL there —
    # this is the "bad migration that the runner refused to apply"
    # case. Escalate hard so the operator looks at logs.
    persistent_detail = (
        f"Drift persists after restart: was {pending}, "
        f"now {post_pending if post_pending is not None else 'unknown'} "
        f"({post_drift.get('error', 'no error reported')})"
    )
    try:
        notify_fn(
            title=f"Migration drift PERSISTS after auto-restart of {WORKER_CONTAINER}",
            detail=(
                f"{persistent_detail}\n\n"
                f"The worker came back healthy but the migrations "
                f"runner did not clear the pending count. This usually "
                f"means a migration is failing — check "
                f"`docker logs {WORKER_CONTAINER}` for the error.\n\n"
                f"Auto-recover will not retry this cycle. Disable it "
                f"with `poindexter set {AUTO_RECOVER_SETTING_KEY} false` "
                f"if you don't want it to retry next cycle either."
            ),
            source="brain.migration_drift_probe",
            severity="critical",
        )
    except Exception as exc:
        logger.warning("[MIGRATION_DRIFT] notify_fn failed: %s", exc)
    await _emit_audit_event(
        pool,
        "probe.migration_drift_recover_failed",
        persistent_detail,
        pending=post_pending,
        extra={"previous_pending": pending},
    )
    _last_notify_drift_count = post_pending if post_pending is not None else pending
    return {
        "ok": False,
        "status": "recover_drift_persists",
        "detail": persistent_detail,
        "pending": post_pending,
        "previous_pending": pending,
        "auto_recover_enabled": True,
    }


# ---------------------------------------------------------------------------
# Probe Protocol adapter — for the registry-driven path used by future
# code. The brain daemon currently dispatches probes by direct call, so
# the immediate wiring path is to invoke ``run_migration_drift_probe``
# from brain_daemon.run_cycle. The class form below lets the registry
# pattern from probe_interface.py pick this up once the probe loop is
# fully driven by ``get_registered_probes()``.
# ---------------------------------------------------------------------------


class MigrationDriftProbe:
    """Probe-Protocol-compatible wrapper around ``run_migration_drift_probe``."""

    name: str = "migration_drift"
    description: str = (
        "Detects unapplied migrations on the worker and (when enabled) "
        "auto-restarts the worker container to apply them."
    )
    interval_seconds: int = PROBE_INTERVAL_SECONDS

    async def check(self, pool, config):  # type: ignore[override]
        # ProbeResult import is lazy so brain_daemon.py imports don't
        # require probe_interface.py to be on sys.path in every context.
        try:
            from probe_interface import ProbeResult
        except ImportError:  # pragma: no cover
            from brain.probe_interface import ProbeResult

        summary = await run_migration_drift_probe(pool)
        return ProbeResult(
            ok=bool(summary.get("ok", False)),
            detail=summary.get("detail", ""),
            metrics={
                k: summary[k]
                for k in ("pending", "applied", "latest_applied", "status")
                if k in summary
            },
            severity="warning" if not summary.get("ok") else "info",
        )
