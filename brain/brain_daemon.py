"""
Glad Labs Big Brain — standalone daemon.

Independent of FastAPI, OpenClaw, Ollama, and the worker.
Only dependency: Python + asyncpg + PostgreSQL.

Runs as its own process. If everything else dies, the brain survives.

Functions:
  1. Monitors all other services (FastAPI, worker, OpenClaw, Vercel)
  2. Processes its own reasoning queue (brain_queue)
  3. Self-maintains knowledge graph (expire stale, resolve contradictions)
  4. Generates proactive insights
  5. Sends alerts when services are down
  6. Can trigger restarts of other services

Usage:
    python brain/brain_daemon.py                # Run forever
    python brain/brain_daemon.py --once         # Run one cycle and exit
    pythonw brain/brain_daemon.py               # Run windowless (background)
"""

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import urllib.error
import urllib.request
import time
from datetime import datetime, timezone

# Standalone — no imports from the FastAPI codebase
import asyncpg

from health_probes import run_health_probes

LOG_DIR = os.path.join(os.path.expanduser("~"), os.getenv("APP_LOG_DIR", ".content-pipeline"))
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "brain.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("brain")

# Local brain DB — the daemon writes ALL data here (brain_knowledge, brain_decisions, etc.)
# Railway is only used for HTTP health checks, never for DB writes.
LOCAL_BRAIN_DB = os.getenv(
    "DATABASE_URL",
    "postgresql://gladlabs:gladlabs-brain-local@localhost:5433/gladlabs_brain",
)

# Telegram for alerts (direct bot API, no OpenClaw dependency)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Canonical env var; fallback matches services/telegram_config.py
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Detect if running on Railway (cloud) or locally
IS_RAILWAY = bool(os.getenv("RAILWAY_SERVICE_ID"))
# Detect Docker (set in docker-compose.local.yml)
IS_DOCKER = bool(os.getenv("IN_DOCKER"))

# Service URLs — loaded from DB at startup, these are just initial defaults
# Updated by _load_config_from_db() before the first monitoring cycle
_SITE_URL = "https://www.gladlabs.io"
_API_BASE_URL = "http://localhost:8002"

SERVICES = {
    "site": {"url": _SITE_URL, "type": "http", "critical": True},
    "api": {"url": _API_BASE_URL + "/api/health", "type": "json_status", "critical": True},
}


async def _load_config_from_db(pool):
    """Load identity config from app_settings (replaces env vars)."""
    global _SITE_URL, _API_BASE_URL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    try:
        rows = await pool.fetch(
            "SELECT key, value FROM app_settings WHERE key IN "
            "('site_url', 'api_base_url', 'telegram_bot_token', 'telegram_chat_id')"
        )
        config = {r["key"]: r["value"] for r in rows}
        if config.get("site_url"):
            _SITE_URL = config["site_url"]
            SERVICES["site"]["url"] = _SITE_URL
        if config.get("api_base_url"):
            _API_BASE_URL = config["api_base_url"]
            SERVICES["api"]["url"] = _API_BASE_URL + "/api/health"
        if config.get("telegram_bot_token"):
            TELEGRAM_BOT_TOKEN = config["telegram_bot_token"]
        if config.get("telegram_chat_id"):
            TELEGRAM_CHAT_ID = config["telegram_chat_id"]
        logger.info("[BRAIN] Loaded %d config values from DB", len(config))
    except Exception as e:
        logger.warning("[BRAIN] Could not load config from DB: %s (using defaults)", e, exc_info=True)

# Local-only services (only monitored when running on Matt's PC or in Docker)
if not IS_RAILWAY:
    # In Docker, other containers are on the Docker network; host services use host.docker.internal
    _local_host = "host.docker.internal" if IS_DOCKER else "localhost"
    # Worker is a sibling container in Docker — use its container name
    _worker_host = "gladlabs-worker" if IS_DOCKER else "localhost"
    SERVICES.update({
        "worker": {"url": f"http://{_worker_host}:8002/api/health", "type": "json_status", "critical": False},
        "openclaw": {"url": f"http://{_local_host}:18789/status", "type": "http", "critical": False},
        "nvidia_exporter": {"url": "http://gladlabs-prometheus:9090/-/healthy" if IS_DOCKER else f"http://{_local_host}:9835/metrics", "type": "http", "critical": False},
        "windows_exporter": {"url": f"http://{_local_host}:9182/metrics", "type": "http", "critical": False},
    })

# External service status pages (always monitored from anywhere)
EXTERNAL_SERVICES = {
    "github": {
        "url": "https://www.githubstatus.com/api/v2/status.json",
        "type": "statuspage",  # Atlassian Statuspage format
    },
    "vercel": {
        "url": "https://www.vercel-status.com/api/v2/status.json",
        "type": "statuspage",
    },
    "railway": {
        "url": "https://railway.instatus.com/summary.json",
        "type": "instatus",  # Instatus format
    },
    # grafana_cloud removed — using local Grafana now (localhost:3000)
    # anthropic removed — no longer used in pipeline (session 55)
}

# Track previous external status to detect transitions
_prev_external_status = {}

CYCLE_SECONDS = 300  # 5 minutes between full cycles


def check_http(url: str, timeout: int = 10) -> tuple:
    """Check if an HTTP endpoint responds. Returns (ok, status_code, detail)."""
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return True, resp.status, "ok"
    except urllib.error.HTTPError as e:
        return False, e.code, str(e.reason)[:100]
    except Exception as e:
        return False, 0, str(e)[:100]


def check_statuspage(url: str, timeout: int = 10) -> tuple:
    """Check an Atlassian Statuspage API. Returns (ok, indicator, description)."""
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        data = json.loads(resp.read())
        indicator = data.get("status", {}).get("indicator", "unknown")
        description = data.get("status", {}).get("description", "unknown")
        ok = indicator == "none"  # "none" = all systems operational
        return ok, indicator, description
    except Exception as e:
        return False, "unreachable", str(e)[:100]


def check_instatus(url: str, timeout: int = 10) -> tuple:
    """Check an Instatus summary endpoint. Returns (ok, status, description)."""
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        data = json.loads(resp.read())
        status = data.get("page", {}).get("status", "UNKNOWN")
        ok = status in ("UP", "HASISSUES")  # UP = good, HASISSUES = degraded but alive
        return ok, status.lower(), status
    except Exception as e:
        return False, "unreachable", str(e)[:100]


def check_json_status(url: str, timeout: int = 10) -> tuple:
    """Check a JSON health endpoint. Returns (ok, status, detail)."""
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        data = json.loads(resp.read())
        status = data.get("status", "unknown")
        ok = status in ("healthy", "degraded", "ok")
        return ok, 200, status
    except Exception as e:
        return False, 0, str(e)[:100]


def send_telegram(message: str):
    """Send alert to Telegram — direct bot API, no dependencies."""
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("[BRAIN] No Telegram bot token — can't send alert")
        return
    try:
        payload = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": f"🧠 Brain: {message}"}).encode()
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.error("[BRAIN] Telegram send failed: %s", e, exc_info=True)


def restart_service(name: str):
    """Attempt to restart a local service. Only works on the local PC, not Railway."""
    if IS_RAILWAY:
        logger.info("[BRAIN] Cannot restart local service %s from Railway — alert sent instead", name)
        send_telegram(f"Service {name} is down. Brain cannot restart from cloud — check your PC.")
        return
    if IS_DOCKER:
        # In Docker, we can restart sibling containers via the Docker socket (if mounted)
        # or just alert. For now, alert — Matt can add Docker socket mount later.
        logger.info("[BRAIN] Cannot restart %s from Docker container — alerting instead", name)
        send_telegram(f"Service {name} is down. Brain is in Docker — restart manually or mount Docker socket.")
        return
    try:
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        if name == "worker":
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", r"C:\Users\mattm\glad-labs-website\scripts\start-worker.ps1"],
                **kwargs,
            )
            logger.info("[BRAIN] Restarted worker")
        elif name == "openclaw":
            subprocess.Popen(
                ["powershell", "-NoProfile", "-Command", "openclaw gateway restart"],
                **kwargs,
            )
            logger.info("[BRAIN] Restarted OpenClaw")
    except Exception as e:
        logger.error("[BRAIN] Failed to restart %s: %s", name, e, exc_info=True)


async def monitor_services(pool) -> list:
    """Check all services, log to knowledge graph, alert on failures."""
    issues = []
    for name, config in SERVICES.items():
        if config["type"] == "json_status":
            ok, code, detail = check_json_status(config["url"])
        else:
            ok, code, detail = check_http(config["url"])

        # Store in knowledge graph
        await pool.execute("""
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, tags)
            VALUES ($1, $2, $3, $4, 'brain_monitor', $5)
            ON CONFLICT (entity, attribute) DO UPDATE SET
                value = EXCLUDED.value, updated_at = NOW()
        """, f"service.{name}", "status", "up" if ok else "down", 1.0, ["monitoring"])

        if not ok:
            issues.append({"service": name, "code": code, "detail": detail, "critical": config["critical"]})
            logger.warning("[BRAIN] Service %s is DOWN: %s", name, detail)

            # Auto-restart local services
            if name in ("worker", "openclaw"):
                restart_service(name)
                logger.info("[BRAIN] Auto-restarted %s", name)

            # Auto-triage: check alert_actions table before escalating
            if config["critical"]:
                try:
                    pattern = f"{name}_down" if name != "api" else "api_down"
                    action = await pool.fetchrow(
                        "SELECT id, action_type, cooldown_minutes, last_triggered_at, consecutive_failures, escalate_after_failures "
                        "FROM alert_actions WHERE pattern = $1 AND enabled = true", pattern
                    )
                    if action:
                        # Check cooldown
                        in_cooldown = False
                        if action["last_triggered_at"]:
                            elapsed = (datetime.now(timezone.utc) - action["last_triggered_at"]).total_seconds() / 60
                            in_cooldown = elapsed < action["cooldown_minutes"]

                        if not in_cooldown:
                            await pool.execute(
                                "UPDATE alert_actions SET last_triggered_at = NOW(), total_triggers = total_triggers + 1, "
                                "consecutive_failures = consecutive_failures + 1 WHERE id = $1", action["id"]
                            )
                            await pool.execute(
                                "INSERT INTO alert_log (alert_action_id, pattern, trigger_detail, action_taken, result) "
                                "VALUES ($1, $2, $3, $4, 'logged')",
                                action["id"], pattern, f"{name}: {detail}"[:500], action["action_type"]
                            )
                            failures = (action["consecutive_failures"] or 0) + 1
                            if failures >= action["escalate_after_failures"] and action["escalate_after_failures"] > 0:
                                send_telegram(f"🚨 {name} DOWN ({failures}x): {detail}")
                            else:
                                logger.info("[BRAIN] Alert '%s' logged (failure %d/%d before escalation)",
                                            pattern, failures, action["escalate_after_failures"])
                        else:
                            logger.debug("[BRAIN] Alert '%s' in cooldown", pattern)
                    else:
                        send_telegram(f"ALERT: {name} is DOWN — {detail}")
                except Exception as alert_err:
                    logger.warning("[BRAIN] Alert triage failed: %s — falling back to Telegram", alert_err, exc_info=True)
                    send_telegram(f"ALERT: {name} is DOWN — {detail}")
        else:
            logger.debug("[BRAIN] Service %s: OK", name)

    return issues


async def monitor_external_services(pool) -> list:
    """Check external service status pages, log to knowledge graph, alert on changes."""
    global _prev_external_status
    issues = []

    for name, config in EXTERNAL_SERVICES.items():
        if config["type"] == "statuspage":
            ok, indicator, description = check_statuspage(config["url"])
        elif config["type"] == "instatus":
            ok, indicator, description = check_instatus(config["url"])
        else:
            continue

        # Store in knowledge graph
        await pool.execute("""
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, tags)
            VALUES ($1, $2, $3, $4, 'brain_monitor', $5)
            ON CONFLICT (entity, attribute) DO UPDATE SET
                value = EXCLUDED.value, updated_at = NOW()
        """, f"external.{name}", "status", f"{indicator}: {description}",
            1.0, ["monitoring", "external"])

        prev = _prev_external_status.get(name)
        _prev_external_status[name] = indicator

        if not ok:
            issues.append({"service": name, "indicator": indicator, "description": description})
            # Only Telegram alert on MAJOR outages (not minor/degraded — reduces spam)
            is_major = indicator in ("major", "critical", "major_outage")
            if prev != indicator:
                logger.warning("[BRAIN] External %s: %s — %s", name, indicator, description)
                if is_major:
                    send_telegram(f"🚨 {name.upper()} MAJOR OUTAGE: {description}")
        else:
            # Alert on recovery from major outage only
            if prev and prev in ("major", "critical", "major_outage") and prev != indicator:
                logger.info("[BRAIN] External %s recovered: %s", name, description)
                send_telegram(f"✅ {name.upper()} recovered: {description}")
            logger.debug("[BRAIN] External %s: OK", name)

    return issues


async def process_queue(pool, max_items: int = 5):
    """Process pending items in the brain queue."""
    try:
        items = await pool.fetch("""
            SELECT id, item_type, content, context
            FROM brain_queue WHERE status = 'pending'
            ORDER BY priority ASC, created_at ASC LIMIT $1
        """, max_items)

        for item in items:
            try:
                # Simple processing — extract facts from observations
                # Log as processed
                await pool.execute(
                    "UPDATE brain_queue SET status = 'processed', processed_at = NOW(), result = $1 WHERE id = $2",
                    json.dumps({"processed_by": "brain_daemon"}), item["id"],
                )
            except Exception as e:
                await pool.execute(
                    "UPDATE brain_queue SET status = 'failed', result = $1, processed_at = NOW() WHERE id = $2",
                    str(e)[:500], item["id"],
                )

        if items:
            logger.info("[BRAIN] Processed %d queue items", len(items))
    except Exception as e:
        logger.error("[BRAIN] Queue processing failed: %s", e, exc_info=True)


async def self_maintain(pool):
    """Expire stale knowledge, clean old queue items."""
    try:
        # Expire old facts
        result = await pool.execute(
            "DELETE FROM brain_knowledge WHERE expires_at IS NOT NULL AND expires_at < NOW()"
        )
        expired = int(result.split()[-1]) if result else 0

        # Clean old processed queue items
        await pool.execute(
            "DELETE FROM brain_queue WHERE status != 'pending' AND created_at < NOW() - INTERVAL '7 days'"
        )

        if expired:
            logger.info("[BRAIN] Maintenance: expired %d facts", expired)
    except Exception as e:
        logger.error("[BRAIN] Maintenance failed: %s", e, exc_info=True)


async def update_system_metrics(pool):
    """Pull current system metrics into knowledge graph."""
    try:
        # Post count
        row = await pool.fetchrow("SELECT COUNT(*) as c FROM posts WHERE status = 'published'")
        if row:
            await pool.execute("""
                INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
                VALUES ('system', 'posts_count', $1, 1.0, 'brain_daemon')
                ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, str(row["c"]))

        # Task counts
        rows = await pool.fetch("SELECT status, COUNT(*) as c FROM content_tasks GROUP BY status")
        for r in rows:
            await pool.execute("""
                INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
                VALUES ('pipeline', $1, $2, 1.0, 'brain_daemon')
                ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, f"tasks_{r['status']}", str(r["c"]))

        # Page views today
        row = await pool.fetchrow("SELECT COUNT(*) as c FROM page_views WHERE created_at >= date_trunc('day', NOW())")
        if row:
            await pool.execute("""
                INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
                VALUES ('traffic', 'views_today', $1, 1.0, 'brain_daemon')
                ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, str(row["c"]))

    except Exception as e:
        logger.debug("[BRAIN] Metrics update failed: %s", e)


async def log_electricity_cost(pool):
    """Log electricity cost for this 5-minute cycle based on real power data or estimates."""
    try:
        # Try to get real power data from nvidia-smi-exporter (local only)
        watts = None
        power_source = "default"
        if not IS_RAILWAY:
            exporter_url = "http://host.docker.internal:9835/metrics" if IS_DOCKER else "http://localhost:9835/metrics"
            try:
                resp = urllib.request.urlopen(exporter_url, timeout=3)
                body = resp.read().decode()
                psu_watts = None
                estimate_watts = None
                for line in body.split("\n"):
                    if line.startswith("psu_total_power_watts"):
                        psu_watts = float(line.split()[-1])
                    elif line.startswith("system_total_power_estimate_watts"):
                        estimate_watts = float(line.split()[-1])
                # HX1500i wall power is ground truth; fall back to software estimate
                if psu_watts:
                    watts = psu_watts
                    power_source = "hx1500i"
                elif estimate_watts:
                    watts = estimate_watts
                    power_source = "estimate"
            except Exception:
                pass  # Exporter not running, use estimate

        if watts is None:
            # Estimate: ~80W idle (Railway containers + local standby)
            watts = 80.0 if IS_RAILWAY else 150.0  # Local PC idles higher

        # Load electricity rate from DB, fallback to default
        rate_per_kwh = 0.29  # $/kWh default
        try:
            row = await pool.fetchrow(
                "SELECT value FROM app_settings WHERE key = 'electricity_rate_kwh'"
            )
            if row:
                rate_per_kwh = float(row["value"])
        except Exception:
            pass

        # Calculate cost for this 5-minute interval
        hours = CYCLE_SECONDS / 3600.0
        kwh = (watts / 1000.0) * hours
        cost_usd = kwh * rate_per_kwh

        # Determine if system is actively generating (check for in_progress tasks)
        active_row = await pool.fetchrow(
            "SELECT COUNT(*) as c FROM content_tasks WHERE status = 'in_progress'"
        )
        is_generating = (active_row["c"] or 0) > 0
        cost_type = "electricity_active" if is_generating else "electricity_idle"
        phase = "generation" if is_generating else "idle"

        await pool.execute("""
            INSERT INTO cost_logs (
                task_id, phase, model, provider, cost_usd,
                input_tokens, output_tokens, total_tokens,
                duration_ms, success, cost_type, created_at, updated_at
            ) VALUES (
                NULL, $1, 'system', 'electricity', $2,
                0, 0, 0, $3, true, $4, NOW(), NOW()
            )
        """, phase, cost_usd, int(CYCLE_SECONDS * 1000), cost_type)

        logger.debug("[BRAIN] Electricity: %.0fW (%s), %.4f kWh, $%.6f (%s)",
                     watts, power_source, kwh, cost_usd, cost_type)

    except Exception as e:
        logger.debug("[BRAIN] Electricity cost logging failed: %s", e)


async def run_cycle(pool):
    """One full brain cycle: monitor → process → maintain → update."""
    logger.info("[BRAIN] === Cycle start ===")

    issues = await monitor_services(pool)
    ext_issues = await monitor_external_services(pool)
    await process_queue(pool)
    await self_maintain(pool)
    await update_system_metrics(pool)
    await log_electricity_cost(pool)

    # Health probes — exercise services with real inputs (each on its own schedule)
    probe_results = await run_health_probes(pool, send_telegram_fn=send_telegram)
    probe_failures = [name for name, r in probe_results.items() if not r.get("ok")]

    all_issues = issues + ext_issues

    # Log cycle result
    await pool.execute("""
        INSERT INTO brain_decisions (decision, reasoning, context, confidence)
        VALUES ($1, $2, $3::jsonb, $4)
    """, f"Cycle complete: {len(all_issues)} issues ({len(issues)} internal, {len(ext_issues)} external), {len(probe_results)} probes ({len(probe_failures)} failed)",
        f"Monitored {len(SERVICES)} internal + {len(EXTERNAL_SERVICES)} external services, ran {len(probe_results)} probes, processed queue, updated metrics",
        json.dumps({"issues": issues, "external_issues": ext_issues, "probe_failures": probe_failures, "timestamp": datetime.now(timezone.utc).isoformat()}),
        1.0,
    )

    logger.info("[BRAIN] === Cycle end: %d issues (%d internal, %d external), %d probes (%d failed) ===",
                len(all_issues), len(issues), len(ext_issues), len(probe_results), len(probe_failures))


async def main():
    one_shot = "--once" in sys.argv

    db_url = LOCAL_BRAIN_DB
    if not db_url:
        logger.error("[BRAIN] No DATABASE_URL — cannot start")
        sys.exit(1)

    logger.info("[BRAIN] Connecting to local brain DB...")
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
    logger.info("[BRAIN] Connected. Starting brain daemon (once=%s)", one_shot)

    # Load config from DB (site URLs, Telegram tokens, etc.)
    await _load_config_from_db(pool)

    # Fallback: load Telegram token from OpenClaw .env if not in DB
    global TELEGRAM_BOT_TOKEN
    env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(env_path) and not TELEGRAM_BOT_TOKEN:
        with open(env_path) as f:
            for line in f:
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    TELEGRAM_BOT_TOKEN = line.split("=", 1)[1].strip()

    shutdown = asyncio.Event()

    def _signal_handler():
        logger.info("[BRAIN] Shutdown signal received")
        shutdown.set()

    try:
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)
    except (NotImplementedError, AttributeError):
        # Windows doesn't support add_signal_handler — use KeyboardInterrupt instead
        pass

    # Touch heartbeat file for Docker HEALTHCHECK
    _heartbeat_path = "/tmp/brain_heartbeat"

    while not shutdown.is_set():
        try:
            await run_cycle(pool)
            # Update heartbeat after successful cycle
            with open(_heartbeat_path, "w") as hb:
                hb.write(str(time.time()))
        except Exception as e:
            logger.error("[BRAIN] Cycle failed: %s", e, exc_info=True)

        if one_shot:
            break

        try:
            await asyncio.wait_for(shutdown.wait(), timeout=CYCLE_SECONDS)
        except asyncio.TimeoutError:
            pass  # Normal — timeout means no shutdown signal, continue loop

    logger.info("[BRAIN] Shutting down gracefully")
    await pool.close()
    logger.info("[BRAIN] Pool closed, exiting")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger("brain").info("[BRAIN] Interrupted, exiting")
