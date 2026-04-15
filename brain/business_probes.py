"""
Business Probes — operator-level monitoring that runs on the brain daemon cycle.

These are Glad Labs private probes, NOT part of Poindexter open source.
They implement the Probe interface from probe_interface.py.

Probes:
  - status_digest: 6-hour system health summary to Telegram
  - (future) email_triage: Gmail inbox scan, flag actionable items
  - (future) revenue_monitor: Lemon Squeezy sales + Mercury balance
"""

import json
import logging
import time
import urllib.request

logger = logging.getLogger("brain.business_probes")

# Schedule tracking — brain runs every 5 min, probes run on their own intervals
_last_run: dict[str, float] = {}


def _is_due(probe_name: str, interval_minutes: int) -> bool:
    """Check if a probe is due to run based on its interval."""
    last = _last_run.get(probe_name, 0)
    return (time.time() - last) >= interval_minutes * 60


def _mark_run(probe_name: str):
    """Record that a probe just ran."""
    _last_run[probe_name] = time.time()


# ============================================================================
# STATUS DIGEST — 6-hour system summary to Telegram
# ============================================================================

async def probe_status_digest(pool, notify_fn) -> dict:
    """Send a comprehensive system status digest to Telegram.

    Runs every 6 hours. Shows all services, all alert states,
    spend, embeddings — the full picture of what IS working.
    """
    if not _is_due("status_digest", 360):  # 6 hours
        return {"ok": True, "detail": "not due yet"}

    try:
        # --- Gather system health ---
        health = {}

        # API health
        try:
            req = urllib.request.Request("http://localhost:8002/api/health", headers={"User-Agent": "brain-probe"})
            resp = urllib.request.urlopen(req, timeout=10)
            api_data = json.loads(resp.read())
            health["api"] = "OK" if api_data.get("status") == "healthy" else "DOWN"
            worker = api_data.get("components", {}).get("task_executor", {})
            health["worker_running"] = worker.get("running", False)
            health["worker_processed"] = worker.get("total_processed", 0)
            gpu = api_data.get("components", {}).get("gpu", {})
            health["gpu_busy"] = gpu.get("busy", False)
        except Exception:
            health["api"] = "DOWN"

        # Site health
        try:
            req = urllib.request.Request("https://www.gladlabs.io", headers={"User-Agent": "brain-probe"})
            resp = urllib.request.urlopen(req, timeout=10)
            health["site"] = "OK" if resp.status == 200 else f"HTTP {resp.status}"
        except Exception:
            health["site"] = "DOWN"

        # OpenClaw
        try:
            req = urllib.request.Request("http://127.0.0.1:18789/", headers={"User-Agent": "brain-probe"})
            resp = urllib.request.urlopen(req, timeout=5)
            health["openclaw"] = "OK"
        except Exception:
            health["openclaw"] = "DOWN"

        # --- DB queries ---
        # Budget
        budget_row = await pool.fetchrow("""
            SELECT
                COALESCE(SUM(CASE WHEN created_at > NOW() - INTERVAL '24 hours' THEN cost_usd ELSE 0 END), 0) as daily,
                COALESCE(SUM(cost_usd), 0) as monthly
            FROM cost_logs
            WHERE created_at > date_trunc('month', NOW())
        """)
        daily_spend = float(budget_row["daily"]) if budget_row else 0
        monthly_spend = float(budget_row["monthly"]) if budget_row else 0

        # Post count
        post_count = await pool.fetchval("SELECT count(*) FROM posts WHERE status = 'published'")

        # Embeddings
        embed_row = await pool.fetchrow("""
            SELECT count(*) as total, MAX(created_at) as newest FROM embeddings
        """)
        embed_total = embed_row["total"] if embed_row else 0
        embed_newest = embed_row["newest"].strftime("%Y-%m-%d") if embed_row and embed_row["newest"] else "?"

        # GPU metrics
        gpu_row = await pool.fetchrow("""
            SELECT utilization, temperature, memory_used, memory_total
            FROM gpu_metrics ORDER BY timestamp DESC LIMIT 1
        """)
        gpu_util = gpu_row["utilization"] if gpu_row else "?"
        gpu_temp = gpu_row["temperature"] if gpu_row else "?"
        gpu_vram_used = round(gpu_row["memory_used"] / 1024, 1) if gpu_row and gpu_row["memory_used"] else "?"
        gpu_vram_total = round(gpu_row["memory_total"] / 1024, 1) if gpu_row and gpu_row["memory_total"] else "?"

        # --- Grafana alert states ---
        alert_lines = []
        try:
            grafana_token = await pool.fetchval("SELECT value FROM app_settings WHERE key = 'grafana_api_key'")
            if grafana_token:
                req = urllib.request.Request(
                    "http://localhost:3000/api/v1/provisioning/alert-rules",
                    headers={"Authorization": f"Bearer {grafana_token}", "User-Agent": "brain-probe"},
                )
                resp = urllib.request.urlopen(req, timeout=10)
                rules = json.loads(resp.read())
                # Get alert instances for state
                req2 = urllib.request.Request(
                    "http://localhost:3000/api/alertmanager/grafana/api/v2/alerts",
                    headers={"Authorization": f"Bearer {grafana_token}", "User-Agent": "brain-probe"},
                )
                resp2 = urllib.request.urlopen(req2, timeout=10)
                alerts = json.loads(resp2.read())

                # Build firing set
                firing = set()
                for a in alerts:
                    if a.get("status", {}).get("state") == "active":
                        name = a.get("labels", {}).get("alertname", "?")
                        firing.add(name)

                for rule in rules:
                    title = rule.get("title", "?")
                    state = "FIRING" if title in firing else "NORMAL"
                    alert_lines.append(f"- {title}: {state}")
        except Exception as e:
            alert_lines.append(f"- (Could not fetch alert states: {e})")

        if not alert_lines:
            # Fallback: query brain_decisions for recent probe results
            alert_lines.append("- (Alert states not available — Grafana API)")

        # --- Build message ---
        lines = [
            "SYSTEM DIGEST (6h):",
            "",
            f"Services: Site {health.get('site','?')}, API {health.get('api','?')}, OpenClaw {health.get('openclaw','?')}, Worker {'running' if health.get('worker_running') else 'DOWN'}",
            f"GPU: {gpu_util}% util, {gpu_temp}C, {gpu_vram_used}/{gpu_vram_total} GB VRAM",
            f"Pipeline: {post_count} published, {health.get('worker_processed',0)} processed",
            f"AI Spend: ${daily_spend:.2f} today / ${monthly_spend:.2f} month",
            f"Memory: {embed_total} embeddings (newest: {embed_newest})",
            "",
            "ALERT STATUS:",
        ]
        lines.extend(alert_lines)

        message = "\n".join(lines)
        notify_fn(message)
        _mark_run("status_digest")

        logger.info("[BUSINESS_PROBE] Status digest sent to Telegram")
        return {"ok": True, "detail": f"digest sent, {len(alert_lines)} alert rules"}

    except Exception as e:
        logger.error("[BUSINESS_PROBE] Status digest failed: %s", e, exc_info=True)
        return {"ok": False, "detail": str(e)}


# ============================================================================
# RUNNER — called from brain daemon's run_cycle
# ============================================================================

async def run_business_probes(pool, notify_fn) -> dict:
    """Run all business probes. Called every brain cycle (5 min).

    Each probe manages its own schedule internally.
    """
    results = {}

    results["status_digest"] = await probe_status_digest(pool, notify_fn)

    # Future probes:
    # results["email_triage"] = await probe_email_triage(pool, notify_fn)
    # results["revenue_monitor"] = await probe_revenue_monitor(pool, notify_fn)

    return results
