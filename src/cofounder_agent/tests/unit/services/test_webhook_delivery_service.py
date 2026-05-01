"""
Tests for WebhookDeliveryService — event emission and HTTP delivery to OpenClaw.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from services.webhook_delivery_service import (
    MAX_RETRIES,
    WebhookDeliveryService,
    emit_webhook_event,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool():
    """Create a mock asyncpg pool with acquire() -> async context manager.

    asyncpg's pool.acquire() returns an async context manager directly
    (not a coroutine that returns one), so we use a MagicMock for pool
    and wire acquire() to return an object with __aenter__/__aexit__.
    """
    conn = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool = MagicMock()
    pool.acquire.return_value = ctx
    return pool, conn


def _make_row(
    event_id=1,
    event_type="task.completed",
    payload=None,
    delivery_attempts=0,
):
    """Build a dict-like row matching the webhook_events schema."""
    if payload is None:
        payload = {"topic": "AI News Roundup", "task_id": "abcdef1234567890", "quality_score": 85}
    return {
        "id": event_id,
        "event_type": event_type,
        "payload": payload,
        "delivery_attempts": delivery_attempts,
    }


# ---------------------------------------------------------------------------
# emit_webhook_event (module-level helper)
# ---------------------------------------------------------------------------


class TestEmitWebhookEvent:
    """Inserting events into the webhook_events table."""

    @pytest.mark.asyncio
    async def test_inserts_event(self):
        pool, conn = _make_pool()
        await emit_webhook_event(pool, "task.completed", {"topic": "Hello"})

        conn.execute.assert_awaited_once()
        args = conn.execute.call_args
        assert "INSERT INTO webhook_events" in args[0][0]
        assert args[0][1] == "task.completed"
        assert json.loads(args[0][2]) == {"topic": "Hello"}

    @pytest.mark.asyncio
    async def test_swallows_db_error(self):
        """Emission failures are logged, not raised."""
        pool, conn = _make_pool()
        conn.execute.side_effect = RuntimeError("connection lost")

        # Should NOT raise
        await emit_webhook_event(pool, "task.failed", {"topic": "Oops"})

    @pytest.mark.asyncio
    async def test_payload_serialized_as_json(self):
        pool, conn = _make_pool()
        payload = {"topic": "Test", "nested": {"a": 1}}
        await emit_webhook_event(pool, "post.published", payload)

        raw_json = conn.execute.call_args[0][2]
        assert json.loads(raw_json) == payload


# ---------------------------------------------------------------------------
# WebhookDeliveryService — construction and start/stop
# ---------------------------------------------------------------------------


class TestServiceLifecycle:
    """Startup, shutdown, and URL configuration."""

    def test_defaults_when_env_unset(self):
        pool, _ = _make_pool()
        with patch.dict("os.environ", {}, clear=True):
            svc = WebhookDeliveryService(pool)
        assert svc.webhook_url == ""
        # `webhook_token` is no longer an attribute — it's read at delivery
        # time via `_get_webhook_token()` so the encrypted is_secret value
        # stays decrypted-on-demand (#325 bug class).
        assert not hasattr(svc, "webhook_token")
        assert svc._running is False

    @pytest.mark.asyncio
    async def test_reads_url_from_env_and_token_via_get_secret(self):
        pool, _ = _make_pool()
        env = {"OPENCLAW_WEBHOOK_URL": "https://openclaw.example.com"}
        with patch.dict("os.environ", env, clear=True):
            svc = WebhookDeliveryService(pool)
        assert svc.webhook_url == "https://openclaw.example.com"
        # Token is fetched async via get_secret (which decrypts is_secret rows).
        # Mock site_config.get_secret to return our test value.
        with patch(
            "services.webhook_delivery_service.site_config.get_secret",
            new_callable=AsyncMock,
            return_value="secret123",
        ):
            assert await svc._get_webhook_token() == "secret123"

    @pytest.mark.asyncio
    async def test_start_without_url_does_nothing(self):
        """If no webhook URL is configured, start() returns immediately."""
        pool, _ = _make_pool()
        with patch.dict("os.environ", {}, clear=True):
            svc = WebhookDeliveryService(pool)
        await svc.start()
        assert svc._running is False
        assert svc._client is None

    @pytest.mark.asyncio
    async def test_start_with_url_sets_running(self):
        pool, _ = _make_pool()
        env = {"OPENCLAW_WEBHOOK_URL": "https://hook.test"}
        with patch.dict("os.environ", env, clear=True):
            svc = WebhookDeliveryService(pool)

        with patch("asyncio.create_task"):
            await svc.start()
        assert svc._running is True
        assert svc._client is not None
        await svc.stop()

    @pytest.mark.asyncio
    async def test_stop_closes_client(self):
        pool, _ = _make_pool()
        mock_client = AsyncMock()
        svc = WebhookDeliveryService(pool)
        svc._running = True
        svc._client = mock_client

        await svc.stop()
        assert svc._running is False
        mock_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# _format_message
# ---------------------------------------------------------------------------


class TestFormatMessage:
    """Human-readable message formatting for each event type."""

    def setup_method(self):
        pool, _ = _make_pool()
        self.svc = WebhookDeliveryService(pool)

    def test_task_completed(self):
        msg = self.svc._format_message(
            "task.completed",
            {"topic": "AI Trends", "task_id": "abcdef1234567890", "quality_score": 92},
        )
        assert "abcdef12" in msg
        assert "AI Trends" in msg
        assert "92" in msg

    def test_task_auto_published(self):
        msg = self.svc._format_message(
            "task.auto_published",
            {"topic": "GPU News", "task_id": "xyz", "quality_score": 88},
        )
        assert "GPU News" in msg
        assert "88" in msg

    def test_task_failed(self):
        msg = self.svc._format_message(
            "task.failed",
            {"topic": "Bad Task", "task_id": "deadbeef99", "error": "Timeout waiting for model"},
        )
        assert "Bad Task" in msg
        assert "Timeout" in msg

    def test_task_needs_review(self):
        msg = self.svc._format_message(
            "task.needs_review",
            {"topic": "Edge Case", "task_id": "reviewme123456789", "quality_score": 60},
        )
        assert "reviewme" in msg
        assert "Edge Case" in msg

    def test_post_published(self):
        msg = self.svc._format_message(
            "post.published",
            {"topic": "Launch Day", "task_id": "pub123", "title": "Big Launch", "site": "gladlabs.io"},
        )
        assert "Big Launch" in msg
        assert "gladlabs.io" in msg

    def test_cost_budget_warning(self):
        msg = self.svc._format_message(
            "cost.budget_warning",
            {"topic": "Budget", "task_id": "cost1", "spent": 4.56, "budget": 5.00, "percent": 91.2},
        )
        assert "$4.56" in msg
        assert "$5.00" in msg
        assert "91%" in msg

    def test_unknown_event_type(self):
        msg = self.svc._format_message(
            "some.unknown",
            {"topic": "Mystery", "task_id": "u123456789"},
        )
        assert "some.unknown" in msg
        assert "Mystery" in msg
        assert "u1234567" in msg

    def test_short_task_id_not_truncated(self):
        msg = self.svc._format_message(
            "task.completed",
            {"topic": "Short", "task_id": "abc", "quality_score": 70},
        )
        assert "abc" in msg

    def test_missing_payload_keys(self):
        """Handles missing optional keys gracefully."""
        msg = self.svc._format_message("task.completed", {})
        assert "Unknown" in msg
        assert "N/A" in msg


# ---------------------------------------------------------------------------
# _deliver_pending — database query
# ---------------------------------------------------------------------------


class TestDeliverPending:
    """Fetching pending events from the database."""

    @pytest.mark.asyncio
    async def test_queries_undelivered_events(self):
        pool, conn = _make_pool()
        conn.fetch = AsyncMock(return_value=[])

        env = {"OPENCLAW_WEBHOOK_URL": "https://hook.test"}
        with patch.dict("os.environ", env, clear=True):
            svc = WebhookDeliveryService(pool)

        await svc._deliver_pending()

        conn.fetch.assert_awaited_once()
        query = conn.fetch.call_args[0][0]
        assert "delivered = FALSE" in query
        assert "delivery_attempts < $1" in query
        # MAX_RETRIES is passed as the parameter
        assert conn.fetch.call_args[0][1] == MAX_RETRIES

    @pytest.mark.asyncio
    async def test_delivers_each_fetched_row(self):
        pool, conn = _make_pool()
        rows = [_make_row(event_id=1), _make_row(event_id=2)]
        conn.fetch = AsyncMock(return_value=rows)

        env = {"OPENCLAW_WEBHOOK_URL": "https://hook.test"}
        with patch.dict("os.environ", env, clear=True):
            svc = WebhookDeliveryService(pool)
        svc._deliver_event = AsyncMock()

        await svc._deliver_pending()

        assert svc._deliver_event.await_count == 2
        svc._deliver_event.assert_any_await(rows[0])
        svc._deliver_event.assert_any_await(rows[1])


# ---------------------------------------------------------------------------
# _deliver_event — HTTP POST and outcome handling
# ---------------------------------------------------------------------------


class TestDeliverEvent:
    """Delivery of a single event row via HTTP."""

    def setup_method(self):
        self.pool, self.conn = _make_pool()
        env = {
            "OPENCLAW_WEBHOOK_URL": "https://hook.test",
            "OPENCLAW_WEBHOOK_TOKEN": "tok_secret",
        }
        with patch.dict("os.environ", env, clear=True):
            self.svc = WebhookDeliveryService(self.pool)
        self.svc._client = AsyncMock()

    @pytest.mark.asyncio
    async def test_success_marks_delivered(self):
        response = MagicMock()
        response.raise_for_status = MagicMock()
        self.svc._client.post = AsyncMock(return_value=response)

        row = _make_row(event_id=42)
        # Token is now read at delivery time via get_secret (#325 sweep);
        # mock the call so the headers test still asserts the bearer.
        with patch(
            "services.webhook_delivery_service.site_config.get_secret",
            new_callable=AsyncMock,
            return_value="tok_secret",
        ):
            await self.svc._deliver_event(row)

        # Verify the HTTP call
        self.svc._client.post.assert_awaited_once()
        call_kwargs = self.svc._client.post.call_args
        assert call_kwargs[0][0] == "https://hook.test/hooks/agent"
        assert "message" in call_kwargs[1]["json"]
        assert call_kwargs[1]["json"]["sessionKey"] == "hook:pipeline"
        assert call_kwargs[1]["headers"]["Authorization"] == "Bearer tok_secret"

        # Verify DB update marks delivered
        self.conn.execute.assert_awaited_once()
        update_sql = self.conn.execute.call_args[0][0]
        assert "delivered = TRUE" in update_sql

    @pytest.mark.asyncio
    async def test_failure_increments_retry_count(self):
        self.svc._client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "500 Server Error", request=MagicMock(), response=MagicMock()
        ))

        row = _make_row(event_id=7, delivery_attempts=1)
        await self.svc._deliver_event(row)

        # Verify DB update increments attempts
        self.conn.execute.assert_awaited_once()
        update_sql = self.conn.execute.call_args[0][0]
        assert "delivery_attempts = delivery_attempts + 1" in update_sql

    @pytest.mark.asyncio
    async def test_network_error_increments_retry(self):
        self.svc._client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))

        row = _make_row(event_id=8)
        await self.svc._deliver_event(row)

        self.conn.execute.assert_awaited_once()
        update_sql = self.conn.execute.call_args[0][0]
        assert "delivery_attempts = delivery_attempts + 1" in update_sql

    @pytest.mark.asyncio
    async def test_no_token_sends_no_auth_header(self):
        env = {"OPENCLAW_WEBHOOK_URL": "https://hook.test"}
        with patch.dict("os.environ", env, clear=True):
            svc = WebhookDeliveryService(self.pool)
        svc._client = AsyncMock()
        response = MagicMock()
        response.raise_for_status = MagicMock()
        svc._client.post = AsyncMock(return_value=response)

        row = _make_row()
        await svc._deliver_event(row)

        headers = svc._client.post.call_args[1]["headers"]
        assert "Authorization" not in headers

    @pytest.mark.asyncio
    async def test_formats_message_in_payload(self):
        """The JSON body sent to OpenClaw contains the formatted message."""
        response = MagicMock()
        response.raise_for_status = MagicMock()
        self.svc._client.post = AsyncMock(return_value=response)

        row = _make_row(
            event_type="task.failed",
            payload={"topic": "Broken", "task_id": "fail12345678", "error": "OOM"},
        )
        await self.svc._deliver_event(row)

        body = self.svc._client.post.call_args[1]["json"]
        assert "Broken" in body["message"]
        assert "OOM" in body["message"]


# ---------------------------------------------------------------------------
# _delivery_loop — polling behavior
# ---------------------------------------------------------------------------


class TestDeliveryLoop:
    """The async polling loop."""

    @pytest.mark.asyncio
    async def test_loop_calls_deliver_pending(self):
        pool, _ = _make_pool()
        svc = WebhookDeliveryService(pool)
        svc._running = True

        call_count = 0

        async def fake_deliver():
            nonlocal call_count
            call_count += 1
            svc._running = False  # stop after one iteration

        svc._deliver_pending = fake_deliver

        with patch("services.webhook_delivery_service.asyncio.sleep", new_callable=AsyncMock):
            await svc._delivery_loop()

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_loop_survives_exception(self):
        """Errors in _deliver_pending don't kill the loop."""
        pool, _ = _make_pool()
        svc = WebhookDeliveryService(pool)
        svc._running = True

        call_count = 0

        async def exploding_deliver():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient DB error")
            svc._running = False

        svc._deliver_pending = exploding_deliver

        with patch("services.webhook_delivery_service.asyncio.sleep", new_callable=AsyncMock):
            await svc._delivery_loop()

        assert call_count == 2  # survived the first error and ran again
