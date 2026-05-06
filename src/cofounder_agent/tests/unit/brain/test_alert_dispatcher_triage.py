"""Unit tests for brain/alert_dispatcher.py triage path (#347 step 4).

Covers the user-prompt checklist:

1. ``_dispatch_one`` schedules ``_triage_one`` even when notify fails
   (parallel, not blocked).
2. ``_triage_one`` retries on 5xx with the configured backoff (mock
   sleep, never actually wait).
3. ``_triage_one`` does NOT retry on 503 / 402 (one attempt, then logs
   and gives up).
4. Telegram follow-up uses ``reply_to_message_id`` from the original
   notify response.
5. When ``ops_triage_enabled=false`` the parallel task is not even
   scheduled.

Mocks asyncpg pool, ``urllib.request.urlopen``, the OAuth client mint,
and the brain_daemon ``send_followup`` helper. No network, no sleeps.
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from brain import alert_dispatcher as ad


# ---------------------------------------------------------------------------
# Pool builder + canned settings
# ---------------------------------------------------------------------------


def _make_pool(
    *,
    triage_enabled: str | None = "true",
    api_base_url: str | None = "http://worker:8002",
    retry_max: str = "3",
    retry_backoff: str = "[10, 30, 90]",
    rows_to_poll: list[dict[str, Any]] | None = None,
):
    """Build a mock pool that answers the dispatcher's reads sensibly."""
    pool = MagicMock()
    settings = {
        "ops_triage_enabled": triage_enabled,
        "api_base_url": api_base_url,
        "ops_triage_retry_max": retry_max,
        "ops_triage_retry_backoff_seconds": retry_backoff,
    }

    async def _fetchval(query, *args):
        if "app_settings" in query and args:
            return settings.get(args[0])
        return None

    async def _fetch(query, *args):
        if rows_to_poll is not None and "FROM alert_events" in query:
            return rows_to_poll
        return []

    pool.fetchval = AsyncMock(side_effect=_fetchval)
    pool.fetch = AsyncMock(side_effect=_fetch)
    pool.execute = AsyncMock(return_value="OK")
    return pool


def _row(alert_id: int = 1, alertname: str = "test_alert"):
    return {
        "id": alert_id,
        "alertname": alertname,
        "status": "firing",
        "severity": "critical",
        "category": "test",
        "labels": json.dumps({"severity": "critical", "alertname": alertname}),
        "annotations": json.dumps({"summary": "test summary"}),
    }


@pytest.fixture(autouse=True)
def _patch_oauth_mint():
    """Replace the OAuth mint path so tests don't hit httpx."""
    async def _stub_mint(_pool, _base_url):
        return "test.jwt.token"
    with patch.object(ad, "_mint_oauth_token", new=_stub_mint):
        yield


@pytest.fixture
def no_sleep():
    """Drop-in for asyncio.sleep — never actually waits."""
    async def _no_sleep(_seconds):
        return None
    return _no_sleep


# ---------------------------------------------------------------------------
# 1. _dispatch_one schedules _triage_one even when notify fails
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParallelScheduling:
    @pytest.mark.asyncio
    async def test_triage_scheduled_when_notify_fails(self, no_sleep):
        # Notify raises -> _dispatch_one returns None. Triage should
        # still be scheduled (the spec says enrichment is independent
        # of paging — a flaky Telegram doesn't block diagnosis).
        pool = _make_pool(rows_to_poll=[_row(alert_id=11)])

        notify_fn = AsyncMock(side_effect=ad.NotifyFailed("telegram down"))

        scheduled: list[Any] = []
        original_create_task = __import__("asyncio").create_task

        def _spy_create_task(coro, *, name=None):
            scheduled.append(name)
            # Close the coroutine so we don't actually run it.
            coro.close()
            # Return a real (already-done) task to satisfy the caller.
            return original_create_task(_no_op_coro(), name=name)

        with patch("brain.alert_dispatcher.asyncio.create_task", side_effect=_spy_create_task):
            await ad.poll_and_dispatch(pool, notify_fn=notify_fn)

        # The notify attempt failed AND a triage task was scheduled.
        assert any(name and name.startswith("triage_one_11") for name in scheduled), (
            f"expected triage_one_11 in scheduled tasks, got {scheduled}"
        )

    @pytest.mark.asyncio
    async def test_triage_scheduled_after_successful_notify(self):
        pool = _make_pool(rows_to_poll=[_row(alert_id=22)])
        notify_fn = AsyncMock(return_value={
            "telegram_message_id": 5000,
            "discord_message_id": "999",
            "ok": True,
        })

        scheduled: list[Any] = []
        original_create_task = __import__("asyncio").create_task

        def _spy(coro, *, name=None):
            scheduled.append(name)
            coro.close()
            return original_create_task(_no_op_coro(), name=name)

        with patch("brain.alert_dispatcher.asyncio.create_task", side_effect=_spy):
            await ad.poll_and_dispatch(pool, notify_fn=notify_fn)

        assert any(name and name.startswith("triage_one_22") for name in scheduled)


async def _no_op_coro():
    return None


# ---------------------------------------------------------------------------
# 2. _triage_one retries on 5xx with the configured backoff
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRetries:
    @pytest.mark.asyncio
    async def test_retries_on_5xx_then_succeeds(self, no_sleep):
        pool = _make_pool(retry_max="3", retry_backoff="[0.001, 0.001, 0.001]")
        row = _row(alert_id=33)
        notify_result = {"telegram_message_id": 100, "discord_message_id": None}

        # Two 503s then a 200 with diagnosis.
        responses = iter([
            (502, b"bad gateway"),
            (502, b"bad gateway"),
            (200, json.dumps({
                "diagnosis": "diagnosis text",
                "model": "ollama/glm-4.7-5090",
                "tokens": 10,
                "ms": 50,
            }).encode()),
        ])

        def _fake_post(url, payload, token, timeout):
            return next(responses)

        sleep_calls: list[float] = []

        async def _spy_sleep(seconds):
            sleep_calls.append(seconds)

        send_followup_calls: list[Any] = []

        async def _spy_followup(*args, **kwargs):
            send_followup_calls.append((args, kwargs))

        # Inject a fake brain_daemon module exposing send_followup.
        fake_mod = MagicMock()
        fake_mod.send_followup = _spy_followup

        with patch.object(ad, "_post_triage_sync", side_effect=_fake_post), \
             patch.dict(sys.modules, {"brain_daemon": fake_mod, "brain.brain_daemon": fake_mod}):
            result = await ad._triage_one(
                pool, row, notify_result, sleep_fn=_spy_sleep,
            )

        assert result is not None
        assert result["diagnosis"] == "diagnosis text"
        # Two backoffs (after first two failures).
        assert len(sleep_calls) == 2
        # Follow-up was sent once.
        assert len(send_followup_calls) == 1

    @pytest.mark.asyncio
    async def test_exhausts_retries_on_persistent_5xx(self, no_sleep):
        pool = _make_pool(retry_max="3", retry_backoff="[0.001, 0.001, 0.001]")
        row = _row(alert_id=34)
        notify_result = {"telegram_message_id": 100}

        def _always_500(url, payload, token, timeout):
            return 500, b"server error"

        sleep_calls: list[float] = []

        async def _spy_sleep(seconds):
            sleep_calls.append(seconds)

        with patch.object(ad, "_post_triage_sync", side_effect=_always_500):
            result = await ad._triage_one(
                pool, row, notify_result, sleep_fn=_spy_sleep,
            )

        assert result is None
        # Three attempts -> two backoffs (no sleep after the final attempt).
        assert len(sleep_calls) == 2


# ---------------------------------------------------------------------------
# 3. Does NOT retry on 503 / 402
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNoRetryStatuses:
    @pytest.mark.asyncio
    async def test_no_retry_on_503(self, no_sleep):
        pool = _make_pool(retry_max="3", retry_backoff="[0.001, 0.001, 0.001]")
        row = _row(alert_id=44)
        notify_result = {"telegram_message_id": 100}

        attempts = {"n": 0}

        def _503(url, payload, token, timeout):
            attempts["n"] += 1
            return 503, b'{"detail": {"code": "no_provider"}}'

        sleep_calls: list[float] = []

        async def _spy_sleep(seconds):
            sleep_calls.append(seconds)

        with patch.object(ad, "_post_triage_sync", side_effect=_503):
            result = await ad._triage_one(
                pool, row, notify_result, sleep_fn=_spy_sleep,
            )

        assert result is None
        assert attempts["n"] == 1
        assert sleep_calls == []  # no backoff used

    @pytest.mark.asyncio
    async def test_no_retry_on_402_sends_skipped_followup(self, no_sleep):
        pool = _make_pool()
        row = _row(alert_id=45)
        notify_result = {"telegram_message_id": 100}

        attempts = {"n": 0}

        def _402(url, payload, token, timeout):
            attempts["n"] += 1
            return 402, b'{"detail": {"code": "cost_guarded"}}'

        followup_messages: list[str] = []

        async def _spy_followup(text, **kwargs):
            followup_messages.append(text)

        fake_mod = MagicMock()
        fake_mod.send_followup = _spy_followup

        async def _spy_sleep(seconds):
            return None

        with patch.object(ad, "_post_triage_sync", side_effect=_402), \
             patch.dict(sys.modules, {"brain_daemon": fake_mod, "brain.brain_daemon": fake_mod}):
            result = await ad._triage_one(
                pool, row, notify_result, sleep_fn=_spy_sleep,
            )

        assert result is None
        assert attempts["n"] == 1
        # Spec'd one-line follow-up so the operator knows enrichment was
        # skipped by the budget cap, not silently lost.
        assert any("cost_guard" in msg for msg in followup_messages)


# ---------------------------------------------------------------------------
# 4. Telegram follow-up uses reply_to_message_id from notify response
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFollowupThreading:
    @pytest.mark.asyncio
    async def test_followup_passes_telegram_message_id(self, no_sleep):
        pool = _make_pool()
        row = _row(alert_id=55)
        notify_result = {
            "telegram_message_id": 7777,
            "discord_message_id": "abc-123",
        }

        def _ok(url, payload, token, timeout):
            return 200, json.dumps({
                "diagnosis": "the diagnosis",
                "model": "ollama/glm-4.7-5090",
                "tokens": 10,
                "ms": 50,
            }).encode()

        captured_followup_kwargs: dict[str, Any] = {}

        async def _spy_followup(text, **kwargs):
            captured_followup_kwargs.update(kwargs)
            captured_followup_kwargs["text"] = text

        fake_mod = MagicMock()
        fake_mod.send_followup = _spy_followup

        async def _spy_sleep(seconds):
            return None

        with patch.object(ad, "_post_triage_sync", side_effect=_ok), \
             patch.dict(sys.modules, {"brain_daemon": fake_mod, "brain.brain_daemon": fake_mod}):
            await ad._triage_one(
                pool, row, notify_result, sleep_fn=_spy_sleep,
            )

        assert captured_followup_kwargs["parent_telegram_message_id"] == 7777
        assert captured_followup_kwargs["parent_discord_message_id"] == "abc-123"
        assert captured_followup_kwargs["text"] == "the diagnosis"

    @pytest.mark.asyncio
    async def test_empty_diagnosis_skips_followup(self, no_sleep):
        pool = _make_pool()
        row = _row(alert_id=56)
        notify_result = {"telegram_message_id": 100}

        def _empty(url, payload, token, timeout):
            return 200, json.dumps({
                "diagnosis": "",
                "model": "ollama/glm-4.7-5090",
                "tokens": 0,
                "ms": 5,
            }).encode()

        followup_calls: list[Any] = []

        async def _spy_followup(*args, **kwargs):
            followup_calls.append((args, kwargs))

        fake_mod = MagicMock()
        fake_mod.send_followup = _spy_followup

        async def _spy_sleep(seconds):
            return None

        with patch.object(ad, "_post_triage_sync", side_effect=_empty), \
             patch.dict(sys.modules, {"brain_daemon": fake_mod, "brain.brain_daemon": fake_mod}):
            await ad._triage_one(
                pool, row, notify_result, sleep_fn=_spy_sleep,
            )

        # Empty diagnosis -> no follow-up per spec.
        assert followup_calls == []


# ---------------------------------------------------------------------------
# 5. ops_triage_enabled=false -> no triage task scheduled at all
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_disabled_does_not_schedule_triage(self):
        pool = _make_pool(triage_enabled="false", rows_to_poll=[_row(alert_id=77)])
        notify_fn = AsyncMock(return_value={
            "telegram_message_id": 1, "discord_message_id": None, "ok": True,
        })

        scheduled: list[Any] = []
        original_create_task = __import__("asyncio").create_task

        def _spy(coro, *, name=None):
            scheduled.append(name)
            coro.close()
            return original_create_task(_no_op_coro(), name=name)

        with patch("brain.alert_dispatcher.asyncio.create_task", side_effect=_spy):
            await ad.poll_and_dispatch(pool, notify_fn=notify_fn)

        # No triage task should have been scheduled when the kill-switch
        # is flipped off.
        assert all(not (name and name.startswith("triage_one_")) for name in scheduled), (
            f"unexpected triage tasks scheduled: {scheduled}"
        )

    @pytest.mark.asyncio
    async def test_enabled_default_true_when_setting_missing(self):
        # Setting key absent (returns None from fetchval) -> enabled True.
        pool = _make_pool(triage_enabled=None, rows_to_poll=[_row(alert_id=78)])
        notify_fn = AsyncMock(return_value={
            "telegram_message_id": 2, "discord_message_id": None, "ok": True,
        })

        scheduled: list[Any] = []
        original_create_task = __import__("asyncio").create_task

        def _spy(coro, *, name=None):
            scheduled.append(name)
            coro.close()
            return original_create_task(_no_op_coro(), name=name)

        with patch("brain.alert_dispatcher.asyncio.create_task", side_effect=_spy):
            await ad.poll_and_dispatch(pool, notify_fn=notify_fn)

        assert any(name and name.startswith("triage_one_78") for name in scheduled)
