"""Unit tests pinning the centralized telegram-creds path
(settings-discipline cleanup, 2026-05-16).

The voice agent's ``_telegram_creds()`` helper used to hand-roll the
``SELECT value FROM app_settings`` + ``enc:v1:``-prefix Fernet
decryption inline. That bypassed ``SiteConfig.get_secret`` — the
single owner of the encryption sentinel + Fernet handling — so any
future change to the on-disk format would silently break the voice
transcript push.

These tests verify the new path:

  - ``SiteConfig.get_secret`` is the only entry into the bot-token /
    chat-id read
  - Both creds return → they're cached on the module and reused
  - Missing token OR chat_id → return ``None`` (transcript disabled)
  - No DATABASE_URL resolvable → return ``None`` without raising

The Pipecat stubs are piggy-backed from the existing
``test_voice_agent_claude_code_session_collision`` module so this file
loads without the Pipecat install.
"""

from __future__ import annotations

import sys
import types
from typing import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Pipecat stubs — piggy-back off the sibling test module.
# ---------------------------------------------------------------------------

# isort: off
from tests.unit.services import test_voice_agent_claude_code_session_collision as _sc_stub  # noqa: E402
# isort: on


@pytest.fixture(autouse=True)
def _pipecat_stubs() -> Iterator[None]:
    _sc_stub._ensure_pipecat_stubs()
    yield


@pytest.fixture(autouse=True)
def _clear_creds_cache() -> Iterator[None]:
    """Reset the module-level cred cache between tests so each test
    sees a clean fetch path. Without this the first test pollutes
    every subsequent test with the cached token/chat_id."""
    import services.voice_agent_claude_code as vac

    vac._TG_BOT_TOKEN_CACHE = None
    vac._TG_CHAT_ID_CACHE = None
    yield
    vac._TG_BOT_TOKEN_CACHE = None
    vac._TG_CHAT_ID_CACHE = None


# ---------------------------------------------------------------------------
# Helpers — fake asyncpg.create_pool that returns a context-manager pool
# ---------------------------------------------------------------------------


class _FakePool:
    """Minimal asyncpg.Pool stand-in. close() is async, and we only
    need to satisfy ``async with pool.acquire() as conn`` for the
    SiteConfig.get_secret path that the new helper calls."""

    def __init__(self, conn_mock):
        self._conn = conn_mock
        self.closed = False

    def acquire(self):
        conn = self._conn

        class _Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return _Ctx()

    async def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTelegramCredsCentralizedPath:
    @pytest.mark.asyncio
    async def test_uses_site_config_get_secret_for_both_keys(self):
        """The new path must call ``SiteConfig.get_secret`` for token
        and chat_id — not raw SELECT + inline Fernet."""
        import services.voice_agent_claude_code as vac

        # Fake pool + connection — get_secret routes through
        # plugins.secrets.get_secret which we'll stub at module level.
        conn_mock = MagicMock()
        pool = _FakePool(conn_mock)

        create_pool = AsyncMock(return_value=pool)

        async def _fake_plugin_get_secret(conn, key):
            return {
                "telegram_bot_token": "bot-token-plaintext",
                "telegram_chat_id": "12345",
            }.get(key)

        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url",
            return_value="postgresql://stub",
        ), patch(
            "plugins.secrets.get_secret", side_effect=_fake_plugin_get_secret,
        ):
            creds = await vac._telegram_creds()

        assert creds == ("bot-token-plaintext", "12345")
        # Pool was closed in the finally block.
        assert pool.closed is True

    @pytest.mark.asyncio
    async def test_cached_after_first_load(self):
        """Second call must NOT touch the pool — credentials live in
        module-level cache."""
        import services.voice_agent_claude_code as vac

        conn_mock = MagicMock()
        pool = _FakePool(conn_mock)
        create_pool = AsyncMock(return_value=pool)

        async def _fake_plugin_get_secret(conn, key):
            return {
                "telegram_bot_token": "bot-token",
                "telegram_chat_id": "999",
            }.get(key)

        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url",
            return_value="postgresql://stub",
        ), patch(
            "plugins.secrets.get_secret", side_effect=_fake_plugin_get_secret,
        ):
            first = await vac._telegram_creds()
            second = await vac._telegram_creds()

        assert first == second == ("bot-token", "999")
        # create_pool called exactly once across both invocations.
        assert create_pool.await_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_when_token_missing(self):
        import services.voice_agent_claude_code as vac

        conn_mock = MagicMock()
        pool = _FakePool(conn_mock)
        create_pool = AsyncMock(return_value=pool)

        async def _fake_plugin_get_secret(conn, key):
            # Token row missing; chat_id present.
            return {"telegram_chat_id": "999"}.get(key)

        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url",
            return_value="postgresql://stub",
        ), patch(
            "plugins.secrets.get_secret", side_effect=_fake_plugin_get_secret,
        ):
            creds = await vac._telegram_creds()

        assert creds is None

    @pytest.mark.asyncio
    async def test_returns_none_when_chat_id_missing(self):
        import services.voice_agent_claude_code as vac

        conn_mock = MagicMock()
        pool = _FakePool(conn_mock)
        create_pool = AsyncMock(return_value=pool)

        async def _fake_plugin_get_secret(conn, key):
            return {"telegram_bot_token": "bot-token"}.get(key)

        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url",
            return_value="postgresql://stub",
        ), patch(
            "plugins.secrets.get_secret", side_effect=_fake_plugin_get_secret,
        ):
            creds = await vac._telegram_creds()

        assert creds is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_database_url(self):
        """No bootstrap.toml + no DATABASE_URL = transcript-push
        disabled. Must NOT raise — the bot keeps running, the
        transcript mirror just goes dark."""
        import services.voice_agent_claude_code as vac

        create_pool = AsyncMock()

        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url", return_value=None,
        ):
            creds = await vac._telegram_creds()

        assert creds is None
        # Should never even attempt to open the pool.
        assert create_pool.await_count == 0
