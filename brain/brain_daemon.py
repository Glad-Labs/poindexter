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
from seed_loader import seed_app_settings

try:
    from business_probes import run_business_probes
    _HAS_BUSINESS_PROBES = True
except ImportError:
    _HAS_BUSINESS_PROBES = False

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
# #198: resolve via bootstrap helper so ~/.poindexter/bootstrap.toml or any
# of DATABASE_URL / LOCAL_DATABASE_URL / POINDEXTER_MEMORY_DSN works. If
# none of those yield a value, require_database_url() notifies the operator
# and exits cleanly.
from brain.bootstrap import require_database_url

LOCAL_BRAIN_DB = require_database_url(source="brain_daemon")

# Telegram for alerts (direct bot API, no OpenClaw dependency)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
# Canonical env var; fallback matches services/telegram_config.py
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Detect Docker (set in docker-compose.local.yml)
IS_DOCKER = bool(os.getenv("IN_DOCKER"))

# Service URLs — loaded from DB at startup via _load_config_from_db().
# Initial values come from env only; no hardcoded localhost fallback (#198).
# The daemon's first monitoring cycle replaces these with app_settings values.
_SITE_URL = os.getenv("SITE_URL", "")
_API_BASE_URL = os.getenv("API_BASE_URL", "")

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

async def _setting_int(pool, key: str, default: int) -> int:
    """Read an integer app_settings value. Brain daemon is standalone
    (no site_config) so it hits the DB directly. Falls back to default
    if the row is missing or unparseable. (#198)
    """
    try:
        val = await pool.fetchval(
            "SELECT value FROM app_settings WHERE key = $1", key
        )
        if val is None:
            return default
        return int(val)
    except (ValueError, TypeError, Exception):
        return default


# Local services always monitored (Poindexter runs on the operator's own machine).
# In Docker, other containers are on the Docker network; host services use host.docker.internal.
_local_host = "host.docker.internal" if IS_DOCKER else "localhost"
# Worker is a sibling container in Docker — use its container name.
_worker_host = "poindexter-worker" if IS_DOCKER else "localhost"
SERVICES.update({
    "worker": {"url": f"http://{_worker_host}:8002/api/health", "type": "json_status", "critical": False},
    "openclaw": {"url": f"http://{_local_host}:18789/status", "type": "http", "critical": False},
    "nvidia_exporter": {"url": "http://poindexter-prometheus:9090/-/healthy" if IS_DOCKER else f"http://{_local_host}:9835/metrics", "type": "http", "critical": False},
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


def send_discord(message: str, webhook_url: str | None = None):
    """Send message to Discord via webhook — no dependencies."""
    url = webhook_url or os.getenv("DISCORD_LAB_LOGS_WEBHOOK_URL", "")
    if not url:
        logger.debug("[BRAIN] No Discord webhook URL — skipping")
        return
    try:
        payload = json.dumps({"content": message}).encode()
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "PoinDexterBrain/1.0",
            },
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        logger.error("[BRAIN] Discord send failed: %s", e)


def notify(message: str):
    """Send to both Telegram (urgent) and Discord #ops (ops log).

    Telegram = alarm bell (phone push notification).
    Discord #ops = system's voice (scrollable ops history).
    Discord #lab-logs = public-facing (daily digest only).
    """
    send_telegram(message)
    # Operational messages go to #ops, not #lab-logs
    ops_webhook = os.getenv("DISCORD_OPS_WEBHOOK_URL", "")
    if ops_webhook:
        send_discord(message, webhook_url=ops_webhook)
    else:
        send_discord(message)  # fallback to lab-logs if ops not configured


def restart_service(name: str):
    """Attempt to restart a local service on the operator's PC."""
    if IS_DOCKER:
        # Docker socket is mounted — restart sibling containers directly.
        _container_map = {
            "worker": "poindexter-worker",
            "api": "poindexter-worker",
            "site": "poindexter-worker",
            "sdxl": "poindexter-sdxl-server",
            "sdxl-server": "poindexter-sdxl-server",
        }
        container = _container_map.get(name)
        if container:
            try:
                result = subprocess.run(
                    ["docker", "restart", container],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    logger.info("[BRAIN] Docker-restarted container %s", container)
                    notify(f"Auto-restarted {container}")
                else:
                    logger.warning("[BRAIN] Docker restart failed for %s: %s", container, result.stderr[:100])
                    notify(f"Failed to restart {container}: {result.stderr[:100]}")
            except FileNotFoundError:
                logger.warning("[BRAIN] Docker CLI not available in container — install docker-cli or mount the binary")
                notify(f"Service {name} is down. Docker CLI not found in brain container.")
            except Exception as e:
                logger.warning("[BRAIN] Docker restart error for %s: %s", name, e)
                notify(f"Service {name} is down. Restart failed: {e}")
        else:
            notify(f"Service {name} is down — no container mapping for auto-restart.")
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


_last_openclaw_doctor = 0.0  # Track last doctor run to avoid running every cycle
_openclaw_cli_missing = False  # Latch once we know the CLI isn't installed


def _run_openclaw_doctor():
    """Run 'openclaw doctor --fix' to heal degraded channels (Telegram 409, WhatsApp disconnect).

    The daemon may run in a container where the openclaw CLI isn't installed
    (it's a host-side tool). If we've already discovered the CLI is missing
    on this process, skip silently instead of retrying and logging every
    cycle.
    """
    global _last_openclaw_doctor, _openclaw_cli_missing
    if _openclaw_cli_missing:
        return
    try:
        kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        result = subprocess.run(
            ["openclaw", "doctor", "--fix"],
            capture_output=True, text=True, timeout=30, **kwargs,
        )
        _last_openclaw_doctor = time.time()
        if "error" in result.stdout.lower() or result.returncode != 0:
            logger.warning("[BRAIN] openclaw doctor reported issues: %s", result.stdout[-200:])
        else:
            logger.info("[BRAIN] openclaw doctor --fix ran OK")
    except FileNotFoundError:
        # CLI isn't installed here — log once, then quiet down.
        _openclaw_cli_missing = True
        logger.info("[BRAIN] openclaw CLI not on PATH — skipping periodic doctor runs")
    except Exception as e:
        logger.warning("[BRAIN] openclaw doctor failed: %s", e)


async def monitor_services(pool) -> list:
    """Check all services, log to knowledge graph, alert on failures."""
    global _last_openclaw_doctor
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
                                notify(f"🚨 {name} DOWN ({failures}x): {detail}")
                            else:
                                logger.info("[BRAIN] Alert '%s' logged (failure %d/%d before escalation)",
                                            pattern, failures, action["escalate_after_failures"])
                        else:
                            logger.debug("[BRAIN] Alert '%s' in cooldown", pattern)
                    else:
                        notify(f"ALERT: {name} is DOWN — {detail}")
                except Exception as alert_err:
                    logger.warning("[BRAIN] Alert triage failed: %s — falling back to Telegram", alert_err, exc_info=True)
                    notify(f"ALERT: {name} is DOWN — {detail}")
        else:
            logger.debug("[BRAIN] Service %s: OK", name)

    # Run openclaw doctor every 15 minutes to heal degraded channels
    # (Telegram 409 conflicts, WhatsApp disconnects) that appear "up" to HTTP checks
    if time.time() - _last_openclaw_doctor > 900:
        _run_openclaw_doctor()

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
                    notify(f"🚨 {name.upper()} MAJOR OUTAGE: {description}")
        else:
            # Alert on recovery from major outage only
            if prev and prev in ("major", "critical", "major_outage") and prev != indicator:
                logger.info("[BRAIN] External %s recovered: %s", name, description)
                notify(f"✅ {name.upper()} recovered: {description}")
            logger.debug("[BRAIN] External %s: OK", name)

    return issues


async def enqueue_brain_item(pool, item_type: str, content: str, context: dict = None, priority: int = 5):
    """Put an item into the brain queue. Callable by any service with a pool handle."""
    await pool.execute("""
        INSERT INTO brain_queue (item_type, content, context, priority, status)
        VALUES ($1, $2, $3::jsonb, $4, 'pending')
    """, item_type, content, json.dumps(context or {}), priority)
    logger.info("[BRAIN] Enqueued %s item (priority %d): %s", item_type, priority, content[:80])


# Brand pillars for topic relevance checks
BRAND_PILLARS = {"ai", "ml", "machine learning", "artificial intelligence", "hardware",
                 "gpu", "cpu", "gaming", "pc", "build", "benchmark", "linux", "llm",
                 "deep learning", "neural", "tech", "automation", "pipeline", "content"}


def _is_brand_relevant(text: str) -> bool:
    """Quick keyword check — does the topic touch at least one brand pillar?"""
    lower = text.lower()
    return any(kw in lower for kw in BRAND_PILLARS)


async def _handle_topic_suggestion(pool, item):
    """Validate a suggested topic and queue as content task if on-brand."""
    topic = item["content"]
    ctx = json.loads(item["context"]) if isinstance(item["context"], str) else (item["context"] or {})

    if not _is_brand_relevant(topic):
        logger.info("[BRAIN] Topic rejected (off-brand): %s", topic[:80])
        return {"action": "rejected", "reason": "off-brand"}

    # Check for duplicate topics already in the pipeline
    existing = await pool.fetchval(
        "SELECT COUNT(*) FROM pipeline_tasks_view WHERE topic ILIKE $1 AND status NOT IN ('failed', 'rejected')",
        f"%{topic[:60]}%",
    )
    if existing:
        logger.info("[BRAIN] Topic rejected (duplicate): %s", topic[:80])
        return {"action": "rejected", "reason": "duplicate_topic"}

    # Queue as a content task
    metadata = json.dumps({"source": ctx.get("source", "brain_queue"), "suggested_by": ctx.get("suggested_by", "unknown")})
    await pool.execute("""
        INSERT INTO pipeline_tasks (task_id, task_type, topic, status)
        VALUES (gen_random_uuid()::text, 'blog_post', $1::text, 'pending')
    """, topic, metadata)
    logger.info("[BRAIN] Topic accepted and queued: %s", topic[:80])
    return {"action": "queued_as_content_task"}


async def _handle_alert(pool, item):
    """Forward alert content to Telegram."""
    ctx = json.loads(item["context"]) if isinstance(item["context"], str) else (item["context"] or {})
    severity = ctx.get("severity", "info")
    source = ctx.get("source", "unknown")
    notify(f"[{severity.upper()}] {source}: {item['content']}")
    return {"action": "forwarded_to_telegram", "severity": severity}


async def _handle_config_change(pool, item):
    """Log a config change into brain_knowledge for audit trail."""
    ctx = json.loads(item["context"]) if isinstance(item["context"], str) else (item["context"] or {})
    key = ctx.get("key", "unknown_key")
    await pool.execute("""
        INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, tags)
        VALUES ($1, 'config_change', $2, 1.0, 'brain_queue', $3)
        ON CONFLICT (entity, attribute) DO UPDATE SET
            value = EXCLUDED.value, updated_at = NOW()
    """, f"config.{key}", item["content"][:500], ["audit", "config"])
    logger.info("[BRAIN] Config change logged: %s", key)
    return {"action": "logged_to_knowledge", "key": key}


async def _handle_observation(pool, item):
    """Store an observation as a brain_knowledge fact."""
    ctx = json.loads(item["context"]) if isinstance(item["context"], str) else (item["context"] or {})
    entity = ctx.get("entity", "general")
    await pool.execute("""
        INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, tags)
        VALUES ($1, 'observation', $2, $3, 'brain_queue', $4)
        ON CONFLICT (entity, attribute) DO UPDATE SET
            value = EXCLUDED.value, updated_at = NOW()
    """, entity, item["content"][:1000], ctx.get("confidence", 0.7), ctx.get("tags", ["observation"]))
    logger.info("[BRAIN] Observation stored for entity '%s'", entity)
    return {"action": "stored_as_knowledge", "entity": entity}


_QUEUE_HANDLERS = {
    "topic_suggestion": _handle_topic_suggestion,
    "alert": _handle_alert,
    "config_change": _handle_config_change,
    "observation": _handle_observation,
}


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
                handler = _QUEUE_HANDLERS.get(item["item_type"])
                if handler:
                    result = await handler(pool, item)
                else:
                    logger.info("[BRAIN] Unknown item_type '%s' — marking processed", item["item_type"])
                    result = {"processed_by": "brain_daemon", "note": "unknown_item_type"}

                await pool.execute(
                    "UPDATE brain_queue SET status = 'processed', processed_at = NOW(), result = $1 WHERE id = $2",
                    json.dumps(result), item["id"],
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


async def _bump_auto_cancelled_metric(pool, count: int) -> None:
    """Record sweeper-auto-cancel events so Prometheus can track the rate.

    GH-90 AC #4: expose ``pipeline_auto_cancelled_total`` as an operator
    metric. The brain daemon is standalone (no prometheus_client import),
    so it writes events to ``pipeline_events`` and the worker's
    ``metrics_exporter`` counts them on scrape. This keeps the signal
    persistent across process restarts — a scrape-only counter in
    brain-daemon memory would reset to zero on every restart.
    """
    if count <= 0:
        return
    for _ in range(count):
        await pool.execute(
            """
            INSERT INTO pipeline_events (event_type, payload)
            VALUES ('task.auto_cancelled', '{"reason": "stale_task_sweeper"}'::jsonb)
            """
        )


async def auto_remediate(pool):
    """Detect and fix pipeline problems automatically. Runs every cycle."""
    try:
        actions_taken = []

        # 1. Auto-cancel tasks stuck in_progress beyond stale_task_timeout_minutes
        #    (default 180m + brain_auto_cancel_grace_minutes extra safety).
        #
        #    GH-90: the sweeper MUST guard on updated_at < NOW() - interval, not
        #    just started_at, or we race the worker. The worker heartbeats
        #    updated_at every worker_heartbeat_interval_seconds during long
        #    stages, so a fresh updated_at is proof the worker is actively
        #    processing the row and the sweeper must back off.
        stale_minutes = await _setting_int(pool, "stale_task_timeout_minutes", 180)
        grace_minutes = await _setting_int(pool, "brain_auto_cancel_grace_minutes", 10)
        cutoff_minutes = stale_minutes + grace_minutes
        stuck = await pool.fetch(f"""
            UPDATE pipeline_tasks SET status = 'failed',
                error_message = 'Auto-cancelled: stuck in_progress > {stale_minutes}m',
                updated_at = NOW()
            WHERE status = 'in_progress'
              AND updated_at < NOW() - INTERVAL '{cutoff_minutes} minutes'
              AND COALESCE(started_at, updated_at) < NOW() - INTERVAL '{cutoff_minutes} minutes'
            RETURNING task_id, topic
        """)
        if stuck:
            topics = [r["topic"][:40] for r in stuck]
            task_ids = [r["task_id"] for r in stuck]
            actions_taken.append(f"cancelled {len(stuck)} stuck task(s): {', '.join(topics)}")
            # GH-90 AC #4: warn-level log with task_id + reason, one row per task,
            # so operators can grep/alert on individual IDs instead of a single
            # summary line. Also bump the Prometheus metric so the dashboard
            # surfaces the rate of sweeper cancellations over time.
            for _tid, _topic in zip(task_ids, topics):
                logger.warning(
                    "[BRAIN][auto-cancel] task_id=%s topic=%r reason='stuck in_progress > %dm'",
                    _tid, _topic, stale_minutes,
                )
            try:
                await _bump_auto_cancelled_metric(pool, len(stuck))
            except Exception as _metric_err:
                logger.debug("[BRAIN] auto_cancelled metric bump failed: %s", _metric_err)

        # 2. Auto-expire awaiting_approval tasks older than 7 days
        # #198: auto-reject stale approval window tunable via app_settings.
        _approval_days = await _setting_int(
            pool, "brain_stale_approval_auto_reject_days", 7
        )
        expired = await pool.fetch(f"""
            UPDATE pipeline_tasks SET status = 'rejected',
                error_message = 'Auto-rejected: awaiting_approval > {_approval_days} days',
                updated_at = NOW()
            WHERE status = 'awaiting_approval'
              AND updated_at < NOW() - INTERVAL '{_approval_days} days'
            RETURNING task_id, topic
        """)
        if expired:
            topics = [r["topic"][:40] for r in expired]
            actions_taken.append(f"auto-rejected {len(expired)} stale approval(s): {', '.join(topics)}")

        # 3. Detect and alert on pipeline stall (no new tasks in 48h + no pending tasks)
        row = await pool.fetchrow("""
            SELECT
                (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'pending') as pending,
                (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'in_progress') as active,
                (SELECT MAX(created_at) FROM pipeline_tasks_view) as last_task
        """)
        if row:
            pending = row["pending"] or 0
            active = row["active"] or 0
            last_task = row["last_task"]
            if pending == 0 and active == 0 and last_task:
                from datetime import datetime, timezone
                if last_task.tzinfo is None:
                    last_task = last_task.replace(tzinfo=timezone.utc)
                hours_idle = (datetime.now(timezone.utc) - last_task).total_seconds() / 3600
                if hours_idle > 48:
                    actions_taken.append(f"pipeline idle {hours_idle:.0f}h — no pending/active tasks")
                    # Store in knowledge graph for trend tracking
                    await pool.execute("""
                        INSERT INTO brain_knowledge (entity, attribute, value, confidence, source, tags)
                        VALUES ('pipeline', 'idle_alert', $1, 0.8, 'auto_remediate', $2)
                        ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
                    """, f"{hours_idle:.0f}h idle", ["pipeline", "alert"])

        # 4. Detect failed task ratio spike and alert (#198 tunable window)
        _fail_win_h = await _setting_int(pool, "brain_failure_rate_window_hours", 24)
        row = await pool.fetchrow(f"""
            SELECT
                (SELECT COUNT(*) FROM pipeline_tasks_view
                 WHERE status = 'failed' AND updated_at > NOW() - INTERVAL '{_fail_win_h} hours') as recent_fails,
                (SELECT COUNT(*) FROM pipeline_tasks_view
                 WHERE updated_at > NOW() - INTERVAL '{_fail_win_h} hours') as recent_total
        """)
        if row and row["recent_total"] and row["recent_total"] > 0:
            fail_rate = row["recent_fails"] / row["recent_total"]
            if fail_rate > 0.5 and row["recent_fails"] >= 3:
                actions_taken.append(
                    f"high failure rate: {row['recent_fails']}/{row['recent_total']} "
                    f"({fail_rate:.0%}) in {_fail_win_h}h"
                )

        if actions_taken:
            logger.info("[BRAIN] Auto-remediation: %s", "; ".join(actions_taken))
            # Alert on significant actions
            for action in actions_taken:
                if "cancelled" in action or "high failure" in action or "idle" in action:
                    notify(f"🔧 Auto-remediation: {action}")

    except Exception as e:
        logger.debug("[BRAIN] Auto-remediation failed: %s", e)


async def generate_daily_digest(pool):
    """Send a daily summary to Telegram at ~9 AM (runs every cycle, fires once/day)."""
    try:
        # Check if we already sent today
        row = await pool.fetchrow("""
            SELECT value FROM brain_knowledge
            WHERE entity = 'digest' AND attribute = 'last_sent'
        """)
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        if row:
            last_sent = row["value"]
            # Parse date and skip if same day
            try:
                last_date = last_sent[:10]
                today = now.strftime("%Y-%m-%d")
                if last_date == today:
                    return  # Already sent today
            except Exception:
                pass

        # Only send between 13:00-14:00 UTC (~9 AM ET)
        if not (13 <= now.hour < 14):
            return

        # Build digest — window is tunable for weekly / daily / hourly
        # digest cadence per operator preference (#198).
        _digest_h = await _setting_int(pool, "brain_digest_window_hours", 24)
        stats = await pool.fetchrow(f"""
            SELECT
                (SELECT COUNT(*) FROM posts WHERE status = 'published') as total_posts,
                (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'awaiting_approval') as approval_queue,
                (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'pending') as pending,
                (SELECT COUNT(*) FROM pipeline_tasks_view WHERE status = 'failed'
                    AND updated_at > NOW() - INTERVAL '{_digest_h} hours') as failed_24h,
                (SELECT COUNT(*) FROM posts WHERE published_at > NOW() - INTERVAL '{_digest_h} hours') as published_24h,
                (SELECT COUNT(*) FROM page_views WHERE created_at >= date_trunc('day', NOW())) as views_today,
                (SELECT COALESCE(SUM(cost_usd), 0) FROM cost_logs
                    WHERE created_at >= date_trunc('month', NOW())) as month_spend
        """)
        if not stats:
            return

        msg = (
            f"📊 Daily Digest ({now.strftime('%b %d')})\n"
            f"Posts: {stats['total_posts']} published, {stats['published_24h']} new today\n"
            f"Pipeline: {stats['pending']} pending, {stats['approval_queue']} awaiting approval, {stats['failed_24h']} failed\n"
            f"Traffic: {stats['views_today']} views today\n"
            f"Spend: ${float(stats['month_spend']):.2f} MTD"
        )
        send_telegram(msg)
        send_discord(msg)  # #lab-logs channel

        # Mark sent
        await pool.execute("""
            INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
            VALUES ('digest', 'last_sent', $1, 1.0, 'brain_daemon')
            ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """, now.isoformat())

        logger.info("[BRAIN] Daily digest sent")

    except Exception as e:
        logger.debug("[BRAIN] Daily digest failed: %s", e)


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
        rows = await pool.fetch("SELECT status, COUNT(*) as c FROM pipeline_tasks_view GROUP BY status")
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
            # Fallback estimate: local PC idles around 150W
            watts = 150.0

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
            "SELECT COUNT(*) as c FROM pipeline_tasks_view WHERE status = 'in_progress'"
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

        # PSU sensor watchdog — alert if real PSU data isn't available
        try:
            prev = await pool.fetchrow(
                "SELECT value FROM brain_knowledge WHERE entity = 'psu_watchdog' AND attribute = 'last_source'"
            )
            prev_source = prev["value"] if prev else None

            if power_source == "hx1500i" and prev_source != "hx1500i":
                # PSU sensors recovered
                notify("PSU sensors recovered — using real HX1500i wall power data")
                send_discord("✅ PSU sensors recovered — using real HX1500i wall power data")
            elif power_source != "hx1500i" and prev_source == "hx1500i":
                # PSU sensors dropped
                notify(f"⚠️ PSU sensors dropped — falling back to {power_source} ({watts:.0f}W). iCUE may have lost the HX1500i connection.")
                send_discord(f"⚠️ PSU sensors dropped — falling back to {power_source} ({watts:.0f}W). iCUE may have lost the HX1500i connection.")

            await pool.execute("""
                INSERT INTO brain_knowledge (entity, attribute, value, confidence, source)
                VALUES ('psu_watchdog', 'last_source', $1, 1.0, 'brain_daemon')
                ON CONFLICT (entity, attribute) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """, power_source)
        except Exception:
            pass  # watchdog is best-effort

    except Exception as e:
        logger.debug("[BRAIN] Electricity cost logging failed: %s", e)


async def run_cycle(pool):
    """One full brain cycle: monitor → process → maintain → update."""
    logger.info("[BRAIN] === Cycle start ===")

    issues = await monitor_services(pool)
    ext_issues = await monitor_external_services(pool)
    await process_queue(pool)
    await auto_remediate(pool)
    await self_maintain(pool)
    await update_system_metrics(pool)
    await log_electricity_cost(pool)
    await generate_daily_digest(pool)

    # Health probes — exercise services with real inputs (each on its own schedule)
    probe_results = await run_health_probes(pool, notify_fn=notify)
    probe_failures = [name for name, r in probe_results.items() if not r.get("ok")]

    # Business probes — operator-level monitoring (Glad Labs private, #215)
    if _HAS_BUSINESS_PROBES:
        try:
            biz_results = await run_business_probes(pool, notify_fn=notify)
            probe_results.update(biz_results)
        except Exception as e:
            logger.warning("[BRAIN] Business probes failed: %s", e)

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

    # Boot-controller phase 1: seed app_settings from the embedded core seed
    # if the table is empty or missing required keys. Idempotent — safe to
    # call every boot. See GitHub #63 (brain-as-boot-controller) and
    # brain/seed_app_settings.json for the core-seed inventory.
    try:
        async with pool.acquire() as conn:
            seed_result = await seed_app_settings(conn)
        logger.info(
            "[BRAIN] Seed phase complete: %d inserted, %d already present, %d total in seed",
            seed_result["inserted"],
            seed_result["skipped_existing"],
            seed_result["total_seed"],
        )
    except Exception as e:
        logger.error("[BRAIN] Seed phase FAILED: %s — continuing with existing app_settings", e, exc_info=True)

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

    # Heartbeat file — Layer 1 of the redundancy model.
    # An OS-level watchdog monitors this file's freshness and restarts the
    # brain if it goes stale (>15 min). Works on any OS, zero dependencies.
    _heartbeat_dir = os.path.join(os.path.expanduser("~"), ".poindexter")
    os.makedirs(_heartbeat_dir, exist_ok=True)
    _heartbeat_path = os.path.join(_heartbeat_dir, "heartbeat")
    # Also keep Docker path for container healthcheck compatibility
    _docker_heartbeat = "/tmp/brain_heartbeat" if IS_DOCKER else None

    def _touch_heartbeat(cycle_issues=0, probe_failures=0):
        """Write structured heartbeat — timestamp + cycle stats."""
        data = json.dumps({
            "ts": time.time(),
            "iso": datetime.now(timezone.utc).isoformat(),
            "pid": os.getpid(),
            "cycle_ok": cycle_issues == 0 and probe_failures == 0,
            "issues": cycle_issues,
            "probe_failures": probe_failures,
        })
        try:
            with open(_heartbeat_path, "w") as hb:
                hb.write(data)
            if _docker_heartbeat:
                with open(_docker_heartbeat, "w") as hb:
                    hb.write(data)
        except OSError as e:
            logger.warning("[BRAIN] Failed to write heartbeat: %s", e)

    # Touch heartbeat on startup so watchdog knows we're alive immediately
    _touch_heartbeat()

    while not shutdown.is_set():
        try:
            await run_cycle(pool)
            # Update heartbeat after successful cycle
            _touch_heartbeat()
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
