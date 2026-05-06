"""Unit tests for the brain daemon's lazy-fetch notification path.

Glad-Labs/poindexter#344 fix: ``send_telegram``, ``send_discord``, and
``notify`` no longer cache Telegram/Discord secrets at module level.
Instead they re-read from ``app_settings`` (via the brain's
``read_app_setting`` helper) on every call. This sidesteps the
module-instance landmine where the brain image's dual ``/app`` +
``/app/brain/`` import paths produced two copies of every global —
``_load_config_from_db`` populated one, the dispatcher read the other,
and the operator's pager went silent.

These tests exercise:

1. ``send_telegram`` reads token + chat_id from app_settings on every
   call and POSTs the decrypted plaintext into the Telegram URL.
2. ``send_discord`` honours an explicit ``webhook_url=`` arg without
   touching app_settings.
3. ``send_discord`` falls back to ``discord_lab_logs_webhook_url``
   from app_settings when no webhook is provided.
4. ``notify`` resolves the ops-channel URL from app_settings.
5. The cross-instance pool registry (``_set_brain_pool``) lets a
   caller without an explicit pool still reach the database.
6. Missing token / chat_id → return False, no POST attempted.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Path-prelude: brain/ is a standalone package outside cofounder_agent.
# parents[5] = repo root that contains brain/.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_BRAIN_DIR = _REPO_ROOT / "brain"
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
if str(_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(_BRAIN_DIR))

from brain import brain_daemon as bd  # noqa: E402


def _row(value, is_secret=False):
    return {"value": value, "is_secret": is_secret}


@pytest.fixture(autouse=True)
def _clear_pool_registry(monkeypatch):
    """Wipe the cross-instance pool registry between tests so they
    don't bleed pools into each other."""
    monkeypatch.delitem(sys.modules, bd._POOL_REGISTRY_KEY, raising=False)
    # And clear bootstrap env vars so tests aren't accidentally satisfied
    # by a developer's shell.
    for var in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "DISCORD_OPS_WEBHOOK_URL",
        "DISCORD_LAB_LOGS_WEBHOOK_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.mark.unit
@pytest.mark.asyncio
class TestSendTelegramLazyFetch:
    """``send_telegram`` reads token + chat_id from app_settings on
    every call (no module-level cache, no #344 landmine)."""

    async def test_reads_token_and_chat_id_from_app_settings_on_each_call(
        self, monkeypatch,
    ):
        pool = MagicMock()
        # Two fetchrow calls per send_telegram: token then chat_id.
        # is_secret=False for both so the decrypt path is bypassed.
        pool.fetchrow = AsyncMock(side_effect=[
            _row("PLAINTEXT_TOKEN", is_secret=False),
            _row("987654321", is_secret=False),
        ])
        pool.fetchval = AsyncMock()

        captured = []

        class _R:
            status = 200

        def _fake_urlopen(req, timeout=10):
            captured.append(req)
            return _R()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        result = await bd.send_telegram("hello operator", pool=pool)

        # send_telegram now returns int message_id (or sentinel 1) on
        # success / None on failure (#347 step 5). Truthy on success
        # preserves the dispatcher contract.
        assert result  # truthy: 1 sentinel or actual message_id
        assert isinstance(result, int)
        assert len(captured) == 1
        url = captured[0].full_url
        # The plaintext token landed in the URL (no env: prefix).
        assert "botPLAINTEXT_TOKEN" in url
        assert url.startswith(
            "https://api.telegram.org/botPLAINTEXT_TOKEN/sendMessage"
        )

    async def test_returns_false_when_token_missing(self, monkeypatch):
        """Empty token row → return False, never hit the network."""
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=[
            _row("", is_secret=False),     # telegram_bot_token (empty)
            _row("123", is_secret=False),  # telegram_chat_id (set)
        ])
        pool.fetchval = AsyncMock()

        captured = []

        def _fake_urlopen(req, timeout=10):
            captured.append(req)

            class _R:
                status = 200

            return _R()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        result = await bd.send_telegram("test", pool=pool)
        # Missing token -> None (was False pre-#347 step 5; both falsy).
        assert not result
        assert captured == []

    async def test_returns_false_when_chat_id_missing(self, monkeypatch):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=[
            _row("token-here", is_secret=False),
            _row("", is_secret=False),  # empty chat id
        ])
        pool.fetchval = AsyncMock()

        captured = []

        def _fake_urlopen(req, timeout=10):
            captured.append(req)

            class _R:
                status = 200

            return _R()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        result = await bd.send_telegram("test", pool=pool)
        # Missing chat_id -> None (was False pre-#347 step 5; both falsy).
        assert not result
        assert captured == []

    async def test_returns_false_with_no_pool_and_no_registry(self):
        """No pool, no env, no registry → log + return None (no crash)."""
        result = await bd.send_telegram("test")
        # Returns None (was False pre-#347 step 5; both falsy).
        assert not result


@pytest.mark.unit
@pytest.mark.asyncio
class TestSendDiscordLazyFetch:
    """``send_discord`` either takes an explicit webhook URL or
    lazy-fetches ``discord_lab_logs_webhook_url`` from app_settings."""

    async def test_explicit_webhook_url_skips_db_lookup(self, monkeypatch):
        pool = MagicMock()
        pool.fetchrow = AsyncMock()
        pool.fetchval = AsyncMock()

        captured = []

        def _fake_urlopen(req, timeout=10):
            captured.append(req)

            class _R:
                status = 204

            return _R()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        ok = await bd.send_discord(
            "hello", webhook_url="https://discord.com/webhook/explicit",
            pool=pool,
        )
        # send_discord now returns str (message id or sentinel "1") on
        # success / None on failure (#347 step 5).
        assert ok  # truthy
        assert isinstance(ok, str)
        # Pool was never queried — explicit URL short-circuits.
        pool.fetchrow.assert_not_awaited()
        assert captured[0].full_url == "https://discord.com/webhook/explicit"

    async def test_falls_back_to_lab_logs_when_no_url_provided(
        self, monkeypatch,
    ):
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=_row(
            "https://discord.com/webhook/lab-logs", is_secret=False,
        ))
        pool.fetchval = AsyncMock()

        captured = []

        def _fake_urlopen(req, timeout=10):
            captured.append(req)

            class _R:
                status = 204

            return _R()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        ok = await bd.send_discord("hello", pool=pool)
        assert ok  # truthy str sentinel / message id
        assert captured[0].full_url == "https://discord.com/webhook/lab-logs"


@pytest.mark.unit
@pytest.mark.asyncio
class TestNotifyLazyFetch:
    """``notify`` lazy-fetches ``discord_ops_webhook_url`` and threads
    the pool through to both send_* helpers."""

    async def test_uses_ops_webhook_when_app_settings_has_one(
        self, monkeypatch,
    ):
        pool = MagicMock()
        # Lookup order:
        #   send_telegram → telegram_bot_token, telegram_chat_id
        #   notify        → discord_ops_webhook_url
        #   send_discord  → uses the explicit webhook_url, no extra lookup
        pool.fetchrow = AsyncMock(side_effect=[
            _row("BOT_TOK", is_secret=False),       # telegram_bot_token
            _row("42", is_secret=False),            # telegram_chat_id
            _row("https://discord.com/ops", is_secret=False),  # ops webhook
        ])
        pool.fetchval = AsyncMock()

        captured = []

        class _R:
            status = 200

        def _fake_urlopen(req, timeout=10):
            captured.append(req)
            return _R()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        result = await bd.notify("ALERT", pool=pool)
        # notify now returns a dict carrying per-channel message ids
        # (#347 step 5). Truthy + ok=True preserves the dispatcher
        # contract.
        assert isinstance(result, dict)
        assert result["ok"] is True
        # First POST is Telegram, second is Discord ops.
        assert len(captured) == 2
        assert "botBOT_TOK" in captured[0].full_url
        assert captured[1].full_url == "https://discord.com/ops"


@pytest.mark.unit
@pytest.mark.asyncio
class TestPoolRegistry:
    """``_set_brain_pool`` stashes the daemon's pool on a sentinel
    sys.modules entry so callers without an explicit pool still reach
    the right database. This is what closes the #344 module-instance
    landmine — both module instances see the same registry entry."""

    async def test_set_brain_pool_makes_pool_available_to_resolver(self):
        sentinel = MagicMock(name="brain-pool")
        bd._set_brain_pool(sentinel)

        resolved = await bd._resolve_pool(None)
        assert resolved is sentinel

    async def test_explicit_pool_overrides_registry(self):
        registry_pool = MagicMock(name="registry-pool")
        explicit_pool = MagicMock(name="explicit-pool")
        bd._set_brain_pool(registry_pool)

        resolved = await bd._resolve_pool(explicit_pool)
        assert resolved is explicit_pool

    async def test_resolve_returns_none_when_registry_empty(self):
        # Autouse fixture cleared the registry already.
        resolved = await bd._resolve_pool(None)
        assert resolved is None

    async def test_send_telegram_uses_registry_pool_when_no_explicit_pool(
        self, monkeypatch,
    ):
        """Smoke test: a caller that doesn't pass pool= still reads
        secrets from the registry pool. This is the exact situation
        ``alert_dispatcher._adapter`` was failing in before #344."""
        registry_pool = MagicMock()
        registry_pool.fetchrow = AsyncMock(side_effect=[
            _row("REG_TOK", is_secret=False),
            _row("99", is_secret=False),
        ])
        registry_pool.fetchval = AsyncMock()
        bd._set_brain_pool(registry_pool)

        captured = []

        class _R:
            status = 200

        def _fake_urlopen(req, timeout=10):
            captured.append(req)
            return _R()

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

        # No pool kwarg — must still send because registry has one.
        result = await bd.send_telegram("hello")
        assert result  # truthy int (message_id or sentinel 1)
        assert "botREG_TOK" in captured[0].full_url
