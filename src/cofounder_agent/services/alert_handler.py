"""
Alert Handler — auto-triage and resolve alerts before escalating to humans.

Design principle: every alert has a resolution path. The system tries to fix
the problem first. Only unknown or repeatedly-failing issues reach Telegram.

Action types:
- db_update: Run a SQL query to fix the issue (e.g., reset stale tasks)
- wait_and_retry: Wait and check again (e.g., API temporarily down)
- run_script: Execute a maintenance script (e.g., re-embed content)
- log_for_review: Log the issue for human review, optionally create Gitea issue
- notify_only: Send Telegram notification (can't auto-fix)

Flow:
    Alert detected → match pattern → check cooldown → execute action
    → if resolved: log success, reset failure counter
    → if failed N times: escalate to Telegram

Usage:
    handler = AlertHandler(pool)
    resolved = await handler.handle("stale_tasks", detail="3 tasks stuck")
    # resolved = True if auto-fixed, False if escalated
"""

import json
import time
from datetime import datetime, timezone
from typing import Optional

from services.logger_config import get_logger

logger = get_logger(__name__)


class AlertHandler:
    """Auto-triage and resolve system alerts."""

    def __init__(self, pool, send_telegram_fn=None):
        self.pool = pool
        self.send_telegram = send_telegram_fn

    async def handle(self, pattern: str, detail: str = "") -> bool:
        """Handle an alert by pattern. Returns True if resolved, False if escalated."""
        # Look up the action for this pattern
        action = await self.pool.fetchrow(
            "SELECT * FROM alert_actions WHERE pattern = $1 AND enabled = true",
            pattern,
        )

        if not action:
            # Unknown pattern — escalate immediately
            logger.warning("[ALERT] Unknown pattern '%s': %s — escalating", pattern, detail)
            await self._escalate(pattern, detail, "No matching alert action configured")
            return False

        # Check cooldown
        if action["last_triggered_at"]:
            elapsed_min = (datetime.now(timezone.utc) - action["last_triggered_at"]).total_seconds() / 60
            if elapsed_min < action["cooldown_minutes"]:
                logger.debug("[ALERT] Pattern '%s' in cooldown (%d/%d min)", pattern, int(elapsed_min), action["cooldown_minutes"])
                return True  # Suppress during cooldown

        # Update trigger timestamp
        await self.pool.execute(
            "UPDATE alert_actions SET last_triggered_at = NOW(), total_triggers = total_triggers + 1 WHERE id = $1",
            action["id"],
        )

        # Execute the action
        action_type = action["action_type"]
        config = action["action_config"] if isinstance(action["action_config"], dict) else json.loads(action["action_config"] or "{}")

        resolved = False
        resolution_detail = ""

        try:
            if action_type == "db_update":
                resolved, resolution_detail = await self._action_db_update(config, detail)
            elif action_type == "wait_and_retry":
                resolved, resolution_detail = await self._action_wait_and_retry(config, detail)
            elif action_type == "run_script":
                resolved, resolution_detail = await self._action_run_script(config, detail)
            elif action_type == "log_for_review":
                resolved, resolution_detail = await self._action_log_for_review(config, pattern, detail)
            elif action_type == "notify_only":
                resolved = False
                resolution_detail = config.get("message", detail)
            else:
                resolution_detail = f"Unknown action type: {action_type}"
        except Exception as e:
            resolution_detail = f"Action failed: {e}"
            logger.warning("[ALERT] Action '%s' for '%s' failed: %s", action_type, pattern, e)

        # Log the result
        await self.pool.execute("""
            INSERT INTO alert_log (alert_action_id, pattern, trigger_detail, action_taken, result, resolution_detail, escalated)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
        """, action["id"], pattern, detail[:500], action_type,
            "resolved" if resolved else "failed", resolution_detail[:500], not resolved)

        if resolved:
            # Reset failure counter
            await self.pool.execute(
                "UPDATE alert_actions SET consecutive_failures = 0, last_resolved_at = NOW(), total_auto_resolved = total_auto_resolved + 1 WHERE id = $1",
                action["id"],
            )
            logger.info("[ALERT] Auto-resolved '%s': %s", pattern, resolution_detail[:100])
        else:
            # Increment failure counter
            await self.pool.execute(
                "UPDATE alert_actions SET consecutive_failures = consecutive_failures + 1 WHERE id = $1",
                action["id"],
            )

            # Check if we should escalate
            failures = (action["consecutive_failures"] or 0) + 1
            if failures >= action["escalate_after_failures"] and action["escalate_after_failures"] > 0:
                await self._escalate(pattern, detail, f"Failed {failures}x — {resolution_detail}")

        return resolved

    async def _action_db_update(self, config: dict, detail: str) -> tuple:
        """Execute a DB update to fix the issue."""
        query = config.get("query", "")
        if not query:
            return False, "No query configured"

        try:
            result = await self.pool.execute(query)
            return True, f"DB update executed: {result}"
        except Exception as e:
            return False, f"DB update failed: {e}"

    async def _action_wait_and_retry(self, config: dict, detail: str) -> tuple:
        """Wait and check if the issue resolved itself."""
        import asyncio
        import httpx

        retry_interval = config.get("retry_interval_seconds", 60)
        max_retries = config.get("max_retries", 3)

        for attempt in range(max_retries):
            await asyncio.sleep(min(retry_interval, 30))  # Cap at 30s per attempt in handler
            # Quick health check
            try:
                from services.site_config import site_config
                api_url = site_config.get("api_base_url", "http://localhost:8000")
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get(f"{api_url}/api/health")
                    if resp.status_code == 200:
                        return True, f"Recovered after {attempt + 1} retries"
            except Exception:
                continue

        return False, f"Still down after {max_retries} retries"

    async def _action_run_script(self, config: dict, detail: str) -> tuple:
        """Run a maintenance script."""
        import asyncio

        script = config.get("script", "")
        args = config.get("args", [])
        if not script:
            return False, "No script configured"

        try:
            proc = await asyncio.create_subprocess_exec(
                "python", script, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                return True, f"Script completed: {stdout.decode()[:200]}"
            return False, f"Script failed (exit {proc.returncode}): {stderr.decode()[:200]}"
        except asyncio.TimeoutError:
            return False, "Script timed out after 120s"
        except Exception as e:
            return False, f"Script error: {e}"

    async def _action_log_for_review(self, config: dict, pattern: str, detail: str) -> tuple:
        """Log the issue for human review."""
        severity = config.get("severity", "warning")
        # Write to audit log
        try:
            await self.pool.execute("""
                INSERT INTO audit_log (event_type, source, severity, details)
                VALUES ($1, 'alert_handler', $2, $3)
            """, f"alert_{pattern}", severity, json.dumps({"detail": detail[:500]}))
        except Exception:
            pass
        return True, f"Logged for review (severity: {severity})"

    async def _escalate(self, pattern: str, detail: str, resolution_info: str):
        """Escalate to Telegram — last resort."""
        message = (
            f"🚨 Alert: {pattern}\n"
            f"Detail: {detail[:200]}\n"
            f"Auto-fix: {resolution_info[:200]}\n"
            f"Action needed."
        )
        if self.send_telegram:
            self.send_telegram(message)
        logger.critical("[ALERT] Escalated to human: %s — %s", pattern, detail[:100])
