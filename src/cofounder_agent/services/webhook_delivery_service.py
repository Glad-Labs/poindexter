"""
Webhook Delivery Service - delivers pipeline events to OpenClaw.

Reads from webhook_events table and POSTs to the configured
OPENCLAW_WEBHOOK_URL. Events are marked as delivered on success
or retried up to 3 times.
"""

import asyncio
from datetime import datetime, timezone

import httpx

from services.logger_config import get_logger
import services.site_config as _site_config_mod
site_config = _site_config_mod.site_config

logger = get_logger(__name__)

MAX_RETRIES = 3
POLL_INTERVAL = 5  # seconds


class WebhookDeliveryService:
    def __init__(self, pool):
        self.pool = pool
        # webhook_url is plaintext, captured at init is fine.
        self.webhook_url = site_config.get("openclaw_webhook_url", "")
        # webhook_token is is_secret=true (#325 bug class) — capturing the
        # sync .get() value at __init__ would store the ciphertext. Read
        # via the async get_secret accessor at delivery time instead.
        self._running = False
        self._client = None
        # Strong ref to the delivery loop task so asyncio doesn't GC it.
        self._delivery_task: asyncio.Task | None = None

    async def _get_webhook_token(self) -> str:
        """Read the bearer token at call time (is_secret=true row)."""
        return await site_config.get_secret("openclaw_webhook_token", "")

    async def start(self):
        """Start the delivery loop."""
        if not self.webhook_url:
            logger.info("[WEBHOOK] No OPENCLAW_WEBHOOK_URL configured, webhook delivery disabled")
            return
        self._running = True
        # Explicit connect sub-cap so a stuck DNS or SYN can't stall the
        # delivery loop beyond its own retry budget.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=3.0)
        )
        logger.info("[WEBHOOK] Delivery service started, polling every %ds", POLL_INTERVAL)
        self._delivery_task = asyncio.create_task(
            self._delivery_loop(), name="webhook_delivery_loop"
        )

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
                logger.warning("[WEBHOOK] Delivery loop error", exc_info=True)
            await asyncio.sleep(POLL_INTERVAL)

    async def _deliver_pending(self):
        """Fetch and deliver all pending webhook events."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, event_type, payload, delivery_attempts
                FROM webhook_events
                WHERE delivered = FALSE
                  AND delivery_attempts < $1
                  AND (
                    last_attempt_at IS NULL
                    OR last_attempt_at < NOW() - INTERVAL '1 second'
                        * POWER(2, delivery_attempts) * 15
                  )
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
            webhook_token = await self._get_webhook_token()
            headers = {}
            if webhook_token:
                headers["Authorization"] = f"Bearer {webhook_token}"

            response = await self._client.post(
                f"{self.webhook_url}/hooks/agent",
                json={"message": message, "sessionKey": "hook:pipeline"},
                headers=headers,
                timeout=10,
            )
            response.raise_for_status()

            # Mark as delivered
            async with self.pool.acquire() as conn:
                await conn.execute(
                    "UPDATE webhook_events SET delivered = TRUE, last_attempt_at = $1 WHERE id = $2",
                    datetime.now(timezone.utc),
                    event_id,
                )
            logger.debug("[WEBHOOK] Delivered event %s (%s)", event_id, event_type)

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
            logger.warning("[WEBHOOK] Failed to deliver event %s", event_id, exc_info=True)

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
        logger.warning("[WEBHOOK] Failed to emit %s event", event_type, exc_info=True)
