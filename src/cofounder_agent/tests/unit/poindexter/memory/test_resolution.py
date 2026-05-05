"""Resolution-chain tests for ``MemoryClient`` (Glad-Labs/poindexter#368).

The integration suite in ``tests/unit/services/test_memory_client.py`` runs
against a live pgvector + Ollama; this module covers just the URL/DSN
resolution branches so they don't need a live stack.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from poindexter.memory import MemoryClient


_FAKE_DSN = "postgresql://test:test@localhost/memory_resolution_test"


def _clear_ollama_env(monkeypatch):
    monkeypatch.delenv("OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)


def _patched_pool(fetchrow_result):
    """Return a mocked asyncpg pool whose ``acquire()`` yields a conn
    with ``fetchrow`` returning the supplied row.
    """
    conn = MagicMock()
    conn.fetchrow = AsyncMock(return_value=fetchrow_result)
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire = MagicMock(return_value=acquire_cm)
    pool.close = AsyncMock()
    return pool


def test_explicit_kwarg_wins_no_db_lookup(monkeypatch):
    """When ollama_url is passed explicitly, app_settings is never queried."""
    _clear_ollama_env(monkeypatch)
    client = MemoryClient(dsn=_FAKE_DSN, ollama_url="http://explicit:11434/")
    assert client.ollama_url == "http://explicit:11434"


def test_env_var_used_when_no_kwarg(monkeypatch):
    _clear_ollama_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://env-host:11434")
    client = MemoryClient(dsn=_FAKE_DSN)
    assert client.ollama_url == "http://env-host:11434"


def test_ollama_url_env_preferred_over_base_url(monkeypatch):
    _clear_ollama_env(monkeypatch)
    monkeypatch.setenv("OLLAMA_URL", "http://primary:11434")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://fallback:11434")
    client = MemoryClient(dsn=_FAKE_DSN)
    assert client.ollama_url == "http://primary:11434"


def test_init_does_not_raise_when_no_url_anywhere(monkeypatch):
    """Failure is deferred to connect() so the DB lookup gets a chance."""
    _clear_ollama_env(monkeypatch)
    client = MemoryClient(dsn=_FAKE_DSN)
    assert client.ollama_url is None


@pytest.mark.asyncio
async def test_app_settings_fallback_resolves_ollama_base_url(monkeypatch):
    """connect() reads ollama_base_url from app_settings when env is empty."""
    _clear_ollama_env(monkeypatch)
    pool = _patched_pool({"value": "http://from-db:11434/"})
    with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
        client = MemoryClient(dsn=_FAKE_DSN)
        await client.connect()
        try:
            assert client.ollama_url == "http://from-db:11434"
        finally:
            await client.close()


@pytest.mark.asyncio
async def test_app_settings_fallback_raises_with_actionable_message(monkeypatch):
    """When neither env nor app_settings has a value, raise pointing at the CLI."""
    _clear_ollama_env(monkeypatch)
    pool = _patched_pool(None)  # no row
    with patch("asyncpg.create_pool", AsyncMock(return_value=pool)):
        client = MemoryClient(dsn=_FAKE_DSN)
        with pytest.raises(RuntimeError) as exc_info:
            await client.connect()
    msg = str(exc_info.value)
    assert "poindexter settings set ollama_base_url" in msg
    assert "app_settings.ollama_base_url" in msg
