"""
Business Probes — operator-level monitoring that runs on the brain daemon cycle.

These are Glad Labs private probes, NOT part of Poindexter open source.
They implement the Probe interface from probe_interface.py.

Probes:
  - status_digest: 6-hour system health summary to Telegram
  - webhook_freshness: alert when revenue/subscriber webhooks go quiet
    (Glad-Labs/poindexter#27 follow-up — webhooks were wired with test
    rows on Apr 25 but no real provider deliveries since)
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

        # Resolve service URLs via the shared DB-first helper. This is the
        # same pattern health_probes uses — kept in docker_utils so the
        # "brain reports everything DOWN because localhost loops back to
        # itself" bug class can't recur as new services get added.
        from docker_utils import resolve_url
        site_url = await resolve_url(
            pool, "public_site_url", "site_url",
            default="https://www.gladlabs.io",
        )
        api_url = await resolve_url(
            pool, "internal_api_base_url", "api_url",
            default="http://localhost:8002",
        )
        openclaw_url = await resolve_url(
            pool, "openclaw_gateway_url",
            default="http://localhost:18789",
        )

        # API health
        try:
            req = urllib.request.Request(f"{api_url.rstrip('/')}/api/health", headers={"User-Agent": "brain-probe"})
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
            req = urllib.request.Request(site_url, headers={"User-Agent": "brain-probe"})
            resp = urllib.request.urlopen(req, timeout=10)
            health["site"] = "OK" if resp.status == 200 else f"HTTP {resp.status}"
        except Exception:
            health["site"] = "DOWN"

        # OpenClaw
        try:
            req = urllib.request.Request(openclaw_url.rstrip("/") + "/", headers={"User-Agent": "brain-probe"})
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
            grafana_url = (await resolve_url(
                pool, "grafana_url", default="http://localhost:3000",
            )).rstrip("/")
            if grafana_token:
                req = urllib.request.Request(
                    f"{grafana_url}/api/v1/provisioning/alert-rules",
                    headers={"Authorization": f"Bearer {grafana_token}", "User-Agent": "brain-probe"},
                )
                resp = urllib.request.urlopen(req, timeout=10)
                rules = json.loads(resp.read())
                # Get alert instances for state
                req2 = urllib.request.Request(
                    f"{grafana_url}/api/alertmanager/grafana/api/v2/alerts",
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
# WEBHOOK FRESHNESS — alert when revenue / subscriber tables go quiet
# ============================================================================
#
# Glad-Labs/poindexter#27 follow-up. Both Lemon Squeezy and Resend webhook
# handlers shipped (signature-verified, registered, idempotent). But each
# table has exactly one row from 2026-04-25 — the test fires at
# handler-wire time. No real provider deliveries since.
#
# Two ways that can be the system's fault:
#
#   1. Provider isn't pointed at our public webhook URL (config drift,
#      URL change, secret mismatch).
#   2. Inbound dispatch is silently dropping verified events somewhere.
#
# And one way it's not the system's fault: there genuinely have been no
# sales / sends. This probe can't distinguish those — only the operator
# can — but it surfaces "your webhook table has been quiet for N days,
# go check the provider config" so the symptom is visible early.
#
# Defaults are deliberately loose: 30d for revenue (a quiet store
# alerting daily on day-1 of zero sales is noise) and 7d for subscribers
# (Resend should see at least one digest send weekly once newsletters
# are running). Operators tune via app_settings:
#
#   webhook_freshness_revenue_threshold_days     (default 30)
#   webhook_freshness_subscriber_threshold_days  (default 7)
#   probe_webhook_freshness_enabled              (default true)
#   probe_webhook_freshness_interval_minutes     (default 1440)


async def _read_setting(pool, key: str, default: str) -> str:
    """Read an app_settings value with a typed default. Never raises."""
    try:
        value = await pool.fetchval(
            "SELECT value FROM app_settings "
            "WHERE key = $1 AND is_active = TRUE",
            key,
        )
    except Exception:
        return default
    if value is None:
        return default
    return str(value).strip() or default


async def _row_age_days(pool, table: str, column: str = "created_at") -> float | None:
    """Return age (in days) of newest row, or None if table is empty / errored."""
    try:
        last = await pool.fetchval(
            # Identifier interpolation OK — `table` and `column` are
            # caller-supplied literals, not user input. Bandit B608 is
            # satisfied because no $ params are involved.
            f"SELECT MAX({column}) FROM {table}",  # nosec B608
        )
    except Exception as e:
        logger.debug("[BUSINESS_PROBE] _row_age_days(%s.%s) failed: %s", table, column, e)
        return None
    if last is None:
        # Empty table — return very-large sentinel so threshold comparison
        # treats it as "long since last delivery".
        return float("inf")
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=_dt.timezone.utc)
    delta = now - last
    return delta.total_seconds() / 86400.0


async def probe_webhook_freshness(pool, notify_fn) -> dict:
    """Check that revenue_events + subscriber_events tables are seeing fresh rows.

    Every ``probe_webhook_freshness_interval_minutes`` (default 24h) the
    probe queries the newest row in each table. If either is older than
    its configured threshold, send an operator notification with a
    pointer to the provider admin URL the human should verify.

    Best-effort: never raises, returns ``{"ok": False, "detail": ...}``
    on internal error so the brain cycle can keep going.
    """
    enabled = (await _read_setting(pool, "probe_webhook_freshness_enabled", "true")).lower()
    if enabled in ("false", "0", "no", "off"):
        return {"ok": True, "detail": "disabled via app_settings"}

    interval = int(await _read_setting(
        pool, "probe_webhook_freshness_interval_minutes", "1440",
    ) or 1440)
    if not _is_due("webhook_freshness", interval):
        return {"ok": True, "detail": "not due yet"}

    revenue_threshold_days = float(await _read_setting(
        pool, "webhook_freshness_revenue_threshold_days", "30",
    ) or 30)
    subscriber_threshold_days = float(await _read_setting(
        pool, "webhook_freshness_subscriber_threshold_days", "7",
    ) or 7)

    revenue_age = await _row_age_days(pool, "revenue_events")
    subscriber_age = await _row_age_days(pool, "subscriber_events")

    alerts: list[str] = []
    if revenue_age is None:
        # Table missing / query error. Don't spam — debug log only.
        logger.debug("[BUSINESS_PROBE] revenue_events not queryable — skipping")
    elif revenue_age >= revenue_threshold_days:
        if revenue_age == float("inf"):
            age_str = "ever (table empty)"
        else:
            age_str = f"{revenue_age:.1f}d"
        alerts.append(
            f"revenue_events: no row in {age_str} (threshold "
            f"{revenue_threshold_days:.0f}d). Verify Lemon Squeezy webhook "
            "config: https://app.lemonsqueezy.com/settings/webhooks"
        )

    if subscriber_age is None:
        logger.debug("[BUSINESS_PROBE] subscriber_events not queryable — skipping")
    elif subscriber_age >= subscriber_threshold_days:
        if subscriber_age == float("inf"):
            age_str = "ever (table empty)"
        else:
            age_str = f"{subscriber_age:.1f}d"
        alerts.append(
            f"subscriber_events: no row in {age_str} (threshold "
            f"{subscriber_threshold_days:.0f}d). Verify Resend webhook "
            "config: https://resend.com/webhooks"
        )

    _mark_run("webhook_freshness")

    if not alerts:
        logger.info(
            "[BUSINESS_PROBE] webhook_freshness OK — revenue_age=%s "
            "subscriber_age=%s",
            "—" if revenue_age is None else f"{revenue_age:.1f}d",
            "—" if subscriber_age is None else f"{subscriber_age:.1f}d",
        )
        return {"ok": True, "detail": "all webhook tables fresh"}

    body = (
        "WEBHOOK QUIET — provider deliveries appear to have stopped.\n\n"
        + "\n\n".join(alerts)
        + "\n\nOperator action: verify the provider admin pages above. "
        "If config is correct and the absence is real (no sales / no "
        "sends), tighten or loosen the thresholds via "
        "`poindexter settings set webhook_freshness_*_threshold_days`."
    )
    try:
        notify_fn(body)
    except Exception as e:
        logger.warning("[BUSINESS_PROBE] notify_fn failed: %s", e)
    logger.warning("[BUSINESS_PROBE] webhook_freshness fired %d alert(s)", len(alerts))
    return {"ok": True, "detail": f"fired {len(alerts)} alert(s)", "alerts": alerts}


# ============================================================================
# RUNNER — called from brain daemon's run_cycle
# ============================================================================

async def run_business_probes(pool, notify_fn) -> dict:
    """Run all business probes. Called every brain cycle (5 min).

    Each probe manages its own schedule internally.
    """
    results = {}

    results["status_digest"] = await probe_status_digest(pool, notify_fn)
    results["webhook_freshness"] = await probe_webhook_freshness(pool, notify_fn)

    # Future probes:
    # results["email_triage"] = await probe_email_triage(pool, notify_fn)
    # results["revenue_monitor"] = await probe_revenue_monitor(pool, notify_fn)

    return results
