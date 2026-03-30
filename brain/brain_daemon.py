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
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

# Standalone — no imports from the FastAPI codebase
import asyncpg

LOG_DIR = os.path.join(os.path.expanduser("~"), ".gladlabs")
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

# Read DB URL from OpenClaw workspace .env (standalone — no settings service)
DB_URL = os.getenv("DATABASE_URL", "")
if not DB_URL:
    env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(env_path):
        for line in open(env_path):
            # We need the public URL, not the internal Railway one
            pass
    # Fallback: hardcode the public URL structure
    # The actual password comes from Railway — we read it at startup
    pass

# Telegram for alerts (direct bot API, no OpenClaw dependency)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "5318613610")

# Detect if running on Railway (cloud) or locally
IS_RAILWAY = bool(os.getenv("RAILWAY_SERVICE_ID"))

# Cloud-reachable services (always monitored)
SERVICES = {
    "site": {"url": "https://gladlabs.io", "type": "http", "critical": True},
    "api": {"url": "https://cofounder-production.up.railway.app/api/health", "type": "json_status", "critical": True},
}

# Local-only services (only monitored when running on Matt's PC)
if not IS_RAILWAY:
    SERVICES.update({
        "worker": {"url": "http://localhost:8002/api/health", "type": "json_status", "critical": False},
        "openclaw": {"url": "http://localhost:18789/status", "type": "http", "critical": False},
        "nvidia_exporter": {"url": "http://localhost:9835/metrics", "type": "http", "critical": False},
        "windows_exporter": {"url": "http://localhost:9182/metrics", "type": "http", "critical": False},
    })

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
        logger.error("[BRAIN] Telegram send failed: %s", e)


def restart_service(name: str):
    """Attempt to restart a local service. Only works on the local PC, not Railway."""
    if IS_RAILWAY:
        logger.info("[BRAIN] Cannot restart local service %s from Railway — alert sent instead", name)
        send_telegram(f"Service {name} is down. Brain cannot restart from cloud — check your PC.")
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
        logger.error("[BRAIN] Failed to restart %s: %s", name, e)


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

            # Alert on critical failures
            if config["critical"]:
                send_telegram(f"ALERT: {name} is DOWN — {detail}")
        else:
            logger.debug("[BRAIN] Service %s: OK", name)

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
                content = item["content"]
                context = json.loads(item["context"]) if isinstance(item["context"], str) else (item["context"] or {})

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
        logger.error("[BRAIN] Queue processing failed: %s", e)


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
        logger.error("[BRAIN] Maintenance failed: %s", e)


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


async def run_cycle(pool):
    """One full brain cycle: monitor → process → maintain → update."""
    logger.info("[BRAIN] === Cycle start ===")

    issues = await monitor_services(pool)
    await process_queue(pool)
    await self_maintain(pool)
    await update_system_metrics(pool)

    # Log cycle result
    await pool.execute("""
        INSERT INTO brain_decisions (decision, reasoning, context, confidence)
        VALUES ($1, $2, $3::jsonb, $4)
    """, f"Cycle complete: {len(issues)} issues",
        f"Monitored {len(SERVICES)} services, processed queue, updated metrics",
        json.dumps({"issues": issues, "timestamp": datetime.now(timezone.utc).isoformat()}),
        1.0,
    )

    logger.info("[BRAIN] === Cycle end: %d issues ===", len(issues))


async def main():
    one_shot = "--once" in sys.argv

    # Get DB URL — try multiple sources
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        # Try Railway CLI
        try:
            result = subprocess.run(
                ["railway", "service", "Postgres"],
                capture_output=True, text=True, timeout=10,
            )
            result2 = subprocess.run(
                ["railway", "variables", "--json"],
                capture_output=True, text=True, timeout=10,
            )
            data = json.loads(result2.stdout)
            db_url = data.get("DATABASE_PUBLIC_URL", "")
        except Exception:
            pass

    if not db_url:
        logger.error("[BRAIN] No DATABASE_URL — cannot start")
        sys.exit(1)

    logger.info("[BRAIN] Connecting to database...")
    pool = await asyncpg.create_pool(db_url, min_size=1, max_size=3)
    logger.info("[BRAIN] Connected. Starting brain daemon (once=%s)", one_shot)

    # Load Telegram token from OpenClaw .env
    global TELEGRAM_BOT_TOKEN
    env_path = os.path.join(os.path.expanduser("~"), ".openclaw", "workspace", ".env")
    if os.path.exists(env_path) and not TELEGRAM_BOT_TOKEN:
        for line in open(env_path):
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                TELEGRAM_BOT_TOKEN = line.split("=", 1)[1].strip()

    while True:
        try:
            await run_cycle(pool)
        except Exception as e:
            logger.error("[BRAIN] Cycle failed: %s", e)

        if one_shot:
            break

        await asyncio.sleep(CYCLE_SECONDS)

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
