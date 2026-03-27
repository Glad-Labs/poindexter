"""
Webhook Delivery Service - delivers pipeline events to OpenClaw.

Reads from webhook_events table and POSTs to the configured
OPENCLAW_WEBHOOK_URL. Events are marked as delivered on success
or retried up to 3 times.
"""

import asyncio
import os
from datetime import datetime, timezone

import httpx

from services.logger_config import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3
POLL_INTERVAL = 5  # seconds


class WebhookDeliveryService:
    def __init__(self, pool):
        self.pool = pool
        self.webhook_url = os.getenv("OPENCLAW_WEBHOOK_URL", "")
        self.webhook_token = os.getenv("OPENCLAW_WEBHOOK_TOKEN", "")
        self._running = False
        self._client = None

    async def start(self):
        """Start the delivery loop."""
        if not self.webhook_url:
            logger.info("[WEBHOOK] No OPENCLAW_WEBHOOK_URL configured, webhook delivery disabled")
            return
        self._running = True
        self._client = httpx.AsyncClient(timeout=10.0)
        logger.info(f"[WEBHOOK] Delivery service started, polling every {POLL_INTERVAL}s")
        asyncio.create_task(self._delivery_loop())

    async def stop(self):
        """Stop the delivery loop."""
        self._running = False
        if self._client:
            await self._client.aclose()

    async def _delivery_loop(self):
        """Poll for undelivered events and send them."""
        while self._running:
            try:
                await self._deliver_pending()
            except Exception:
                logger.debug("[WEBHOOK] Delivery loop error", exc_info=True)
            await asyncio.sleep(POLL_INTERVAL)

    async def _deliver_pending(self):
        """Fetch and deliver all pending webhook events."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, event_type, payload, delivery_attempts
                FROM webhook_events
                WHERE delivered = FALSE AND delivery_attempts < $1
                ORDER BY created_at ASC
                LIMIT 50
                """,
                MAX_RETRIES,
            )

        for row in rows:
            await self._deliver_event(row)

    async def _deliver_event(self, row):
        """Deliver a single event to the OpenClaw webhook."""
        event_id = row["id"]
        event_type = row["event_type"]
        payload = row["payload"]

        # Format message for OpenClaw
        message = self._format_message(event_type, payload)

        try:
            headers = {}
            if self.webhook_token:
                headers["Authorization"] = f"Bearer {self.webhook_token}"

            response = await self._client.post(
                f"{self.webhook_url}/hooks/agent",
                json={"message": message, "sessionKey": "hook:pipeline"},
                headers=headers,
            )
            response.raise_for_status()

            # Mark as delivered
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE webhook_events SET delivered = TRUE, last_attempt_at = $1 WHERE id = $2",
                    datetime.now(timezone.utc),
                    event_id,
                )
            logger.debug(f"[WEBHOOK] Delivered event {event_id} ({event_type})")

        except Exception:
            # Increment retry count
            async with self.pool.acquire() as conn:
                await conn.execute(
                    """
                    UPDATE webhook_events
                    SET delivery_attempts = delivery_attempts + 1, last_attempt_at = $1
                    WHERE id = $2
                    """,
                    datetime.now(timezone.utc),
                    event_id,
                )
            logger.debug(f"[WEBHOOK] Failed to deliver event {event_id}", exc_info=True)

    def _format_message(self, event_type: str, payload: dict) -> str:
        """Format a webhook event into a human-readable message for OpenClaw."""
        topic = payload.get("topic", "Unknown")
        task_id = payload.get("task_id", "unknown")
        short_id = task_id[:8] if len(task_id) > 8 else task_id

        formatters = {
            "task.completed": lambda: f"✅ Task {short_id} completed — '{topic}' (score: {payload.get('quality_score', 'N/A')})",
            "task.auto_published": lambda: f"🚀 Auto-published '{topic}' (score: {payload.get('quality_score', 'N/A')})",
            "task.failed": lambda: f"❌ Task {short_id} failed — '{topic}': {payload.get('error', 'Unknown error')[:100]}",
            "task.needs_review": lambda: f"👀 Task {short_id} needs review — '{topic}' (score: {payload.get('quality_score', 'N/A')})",
            "post.published": lambda: f"📰 Published '{payload.get('title', topic)}' to {payload.get('site', 'default')}",
            "cost.budget_warning": lambda: f"💰 Budget alert: spent ${payload.get('spent', 0):.2f} of ${payload.get('budget', 0):.2f} daily budget ({payload.get('percent', 0):.0f}%)",
        }

        formatter = formatters.get(event_type)
        if formatter:
            return formatter()
        return f"[{event_type}] {topic} (task: {short_id})"


async def emit_webhook_event(pool, event_type: str, payload: dict):
    """Helper to insert a webhook event from anywhere in the codebase."""
    try:
        import json

        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO webhook_events (event_type, payload)
                VALUES ($1, $2::jsonb)
                """,
                event_type,
                json.dumps(payload),
            )
    except Exception:
        logger.debug(f"[WEBHOOK] Failed to emit {event_type} event", exc_info=True)
