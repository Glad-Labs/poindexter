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

# Install the Pipecat stubs at MODULE import time. Autouse fixtures
# alone are insufficient because the sibling ``_clear_creds_cache``
# autouse fixture also imports ``services.voice_agent_claude_code``,
# and pytest orders same-scope autouse fixtures alphabetically — so
# ``_clear_creds_cache`` would otherwise run before ``_pipecat_stubs``
# and explode with ModuleNotFoundError: pipecat.
_sc_stub._ensure_pipecat_stubs()


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


@pytest.mark.unit
class TestTelegramCredsEdgeCases:
    """Edge cases + defensive contracts around ``_telegram_creds`` —
    pins behaviour the original GH cleanup did not explicitly cover.

    These are not duplicates of the happy/missing-key tests above;
    they pin coercion, pool lifecycle on failure, cache hygiene on
    partial reads, and the deliberate pool-sizing kwargs."""

    @pytest.mark.asyncio
    async def test_returns_none_when_both_creds_missing(self):
        """Neither token nor chat_id present — both ``not <val>``
        branches collapse to the same disabled-transcript outcome,
        no cache write, pool still closed cleanly."""
        import services.voice_agent_claude_code as vac

        conn_mock = MagicMock()
        pool = _FakePool(conn_mock)
        create_pool = AsyncMock(return_value=pool)

        async def _fake_plugin_get_secret(conn, key):
            return None

        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url",
            return_value="postgresql://stub",
        ), patch(
            "plugins.secrets.get_secret", side_effect=_fake_plugin_get_secret,
        ):
            creds = await vac._telegram_creds()

        assert creds is None
        assert pool.closed is True
        # Cache must remain unset so a later config-fix can repopulate it.
        assert vac._TG_BOT_TOKEN_CACHE is None
        assert vac._TG_CHAT_ID_CACHE is None

    @pytest.mark.asyncio
    async def test_empty_string_creds_treated_as_missing(self):
        """``get_secret`` returns ``""`` for both keys (the
        ``SiteConfig.get_secret(key, default="")`` default). The
        ``if not <val>`` guard must treat empty strings the same as
        ``None`` — otherwise the bot would push transcripts to
        chat_id="" with token="" and 401-loop forever."""
        import services.voice_agent_claude_code as vac

        conn_mock = MagicMock()
        pool = _FakePool(conn_mock)
        create_pool = AsyncMock(return_value=pool)

        async def _fake_plugin_get_secret(conn, key):
            return ""

        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url",
            return_value="postgresql://stub",
        ), patch(
            "plugins.secrets.get_secret", side_effect=_fake_plugin_get_secret,
        ):
            creds = await vac._telegram_creds()

        assert creds is None

    @pytest.mark.asyncio
    async def test_chat_id_coerced_to_str_when_returned_as_int(self):
        """``app_settings.value`` is TEXT, but a misconfigured row /
        future numeric column would surface chat_id as an int. The
        ``str(...)`` cast on line 509-510 normalises both to strings
        so the downstream f-string URL builder doesn't blow up."""
        import services.voice_agent_claude_code as vac

        conn_mock = MagicMock()
        pool = _FakePool(conn_mock)
        create_pool = AsyncMock(return_value=pool)

        async def _fake_plugin_get_secret(conn, key):
            return {
                "telegram_bot_token": "bot-token-plaintext",
                # Deliberately an int — the str() cast must normalise.
                "telegram_chat_id": 12345,
            }.get(key)

        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url",
            return_value="postgresql://stub",
        ), patch(
            "plugins.secrets.get_secret", side_effect=_fake_plugin_get_secret,
        ):
            creds = await vac._telegram_creds()

        assert creds is not None
        assert creds == ("bot-token-plaintext", "12345")
        assert isinstance(creds[1], str)

    @pytest.mark.asyncio
    async def test_partial_read_does_not_poison_cache(self):
        """First call sees a missing token → returns None. Second
        call (after the operator fixes the config) sees both creds
        → must succeed. If the cache were populated on a partial
        read, the second call would short-circuit with stale data
        or refuse to retry."""
        import services.voice_agent_claude_code as vac

        conn_mock = MagicMock()
        pool = _FakePool(conn_mock)
        create_pool = AsyncMock(return_value=pool)

        call_state = {"chat_id_only": True}

        async def _fake_plugin_get_secret(conn, key):
            if call_state["chat_id_only"]:
                return {"telegram_chat_id": "999"}.get(key)
            return {
                "telegram_bot_token": "fixed-token",
                "telegram_chat_id": "999",
            }.get(key)

        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url",
            return_value="postgresql://stub",
        ), patch(
            "plugins.secrets.get_secret", side_effect=_fake_plugin_get_secret,
        ):
            first = await vac._telegram_creds()
            assert first is None
            # Operator fixes the missing token — flip the fixture.
            call_state["chat_id_only"] = False
            second = await vac._telegram_creds()

        assert second == ("fixed-token", "999")
        # Pool was opened twice — once per call, no cache short-circuit.
        assert create_pool.await_count == 2

    @pytest.mark.asyncio
    async def test_internal_get_secret_failure_degrades_to_disabled(self):
        """``SiteConfig.get_secret`` swallows per-key DB exceptions and
        logs a warning — it does NOT re-raise. Confirm that contract
        is inherited by the voice agent: a transient decrypt / DB
        hiccup makes the transcript go dark (return ``None``) rather
        than crashing the bot mid-call. The finally block must still
        close the pool, and the cache must NOT be populated so a
        later healthy call can repopulate it."""
        import services.voice_agent_claude_code as vac

        conn_mock = MagicMock()
        pool = _FakePool(conn_mock)
        create_pool = AsyncMock(return_value=pool)

        async def _fake_plugin_get_secret(conn, key):
            raise RuntimeError("simulated decrypt failure")

        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url",
            return_value="postgresql://stub",
        ), patch(
            "plugins.secrets.get_secret", side_effect=_fake_plugin_get_secret,
        ):
            creds = await vac._telegram_creds()

        assert creds is None
        # Pool always closed via finally even when SiteConfig logs+swallows.
        assert pool.closed is True
        # Cache must remain unset so a later healthy call retries.
        assert vac._TG_BOT_TOKEN_CACHE is None
        assert vac._TG_CHAT_ID_CACHE is None

    @pytest.mark.asyncio
    async def test_create_pool_called_with_tight_sizing(self):
        """The pool is intentionally sized at min=1/max=1 with short
        timeouts because get_secret runs at most twice on this path
        and the bot never reuses the pool. A future PR that widens
        these defaults (e.g. min_size=5) would leak DB connections
        for every voice-agent restart — pin the contract."""
        import services.voice_agent_claude_code as vac

        conn_mock = MagicMock()
        pool = _FakePool(conn_mock)
        create_pool = AsyncMock(return_value=pool)

        async def _fake_plugin_get_secret(conn, key):
            return {
                "telegram_bot_token": "t",
                "telegram_chat_id": "1",
            }.get(key)

        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url",
            return_value="postgresql://stub",
        ), patch(
            "plugins.secrets.get_secret", side_effect=_fake_plugin_get_secret,
        ):
            await vac._telegram_creds()

        assert create_pool.await_count == 1
        call = create_pool.await_args
        assert call is not None
        # Positional DSN.
        assert call.args[0] == "postgresql://stub"
        # Deliberate sizing — keep it tight.
        assert call.kwargs.get("min_size") == 1
        assert call.kwargs.get("max_size") == 1
        assert call.kwargs.get("timeout") == 2.0
        assert call.kwargs.get("command_timeout") == 5.0

    @pytest.mark.asyncio
    async def test_get_secret_requested_for_exact_keys(self):
        """Both ``telegram_bot_token`` and ``telegram_chat_id`` are
        the canonical app_settings rows. A typo in either key name
        would silently disable the transcript push — pin the names."""
        import services.voice_agent_claude_code as vac

        conn_mock = MagicMock()
        pool = _FakePool(conn_mock)
        create_pool = AsyncMock(return_value=pool)

        requested_keys: list[str] = []

        async def _fake_plugin_get_secret(conn, key):
            requested_keys.append(key)
            return {
                "telegram_bot_token": "t",
                "telegram_chat_id": "1",
            }.get(key)

        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url",
            return_value="postgresql://stub",
        ), patch(
            "plugins.secrets.get_secret", side_effect=_fake_plugin_get_secret,
        ):
            await vac._telegram_creds()

        assert set(requested_keys) == {
            "telegram_bot_token",
            "telegram_chat_id",
        }

    @pytest.mark.asyncio
    async def test_resolved_db_url_passed_through_to_create_pool(self):
        """``resolve_database_url`` is the single source of truth for
        the DSN (bootstrap.toml → DATABASE_URL → LOCAL_DATABASE_URL
        → POINDEXTER_MEMORY_DSN). Confirm whatever it returns is the
        URL handed to ``asyncpg.create_pool`` verbatim, so this code
        path inherits bootstrap's resolution policy."""
        import services.voice_agent_claude_code as vac

        conn_mock = MagicMock()
        pool = _FakePool(conn_mock)
        create_pool = AsyncMock(return_value=pool)

        async def _fake_plugin_get_secret(conn, key):
            return {"telegram_bot_token": "t", "telegram_chat_id": "1"}.get(key)

        custom_dsn = "postgresql://custom-user@host:5433/custom_db?sslmode=require"
        with patch("asyncpg.create_pool", create_pool), patch(
            "brain.bootstrap.resolve_database_url", return_value=custom_dsn,
        ), patch(
            "plugins.secrets.get_secret", side_effect=_fake_plugin_get_secret,
        ):
            await vac._telegram_creds()

        assert create_pool.await_args is not None
        assert create_pool.await_args.args[0] == custom_dsn
