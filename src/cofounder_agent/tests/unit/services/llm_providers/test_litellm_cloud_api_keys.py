"""Pin ``configure_cloud_api_keys`` — the DB→env bridge for cloud LLM creds.

LiteLLM auto-discovers cloud credentials from process env
(``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` / ``GEMINI_API_KEY``). The
canonical store is the ``is_secret=true`` app_settings rows, so the worker
and the Prefect subprocess call ``configure_cloud_api_keys`` at startup to
stamp the decrypted values into env — mirroring the Langfuse credential
stamping in ``configure_langfuse_callback``.

Contracts pinned here:

- secrets are read via the async ``get_secret`` accessor (is_secret rows
  are filtered out of SiteConfig's sync cache — reading them via ``get``
  silently misses, the exact bug the Langfuse block once had)
- empty/missing rows are the NORMAL local-only state: skipped, no raise
- a non-empty DB value overwrites a stale env var (DB-first)
- key VALUES never appear in log output (names-only logging)
"""

from __future__ import annotations

import logging
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.llm_providers.litellm_provider import (
    _CLOUD_API_KEY_ENV_MAP,
    configure_cloud_api_keys,
)

_ALL_ENV_VARS = tuple(_CLOUD_API_KEY_ENV_MAP.values())


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts with no cloud key env vars set."""
    for var in _ALL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


def _fake_site_config(secrets: dict[str, str] | None = None):
    """Mock SiteConfig whose async ``get_secret`` routes by key.

    The sync ``get`` raises on the secret keys to pin that the function
    NEVER reads credentials through the non-secret cache path.
    """
    secrets = secrets or {}

    def _sync_get(key, default=""):
        if key in _CLOUD_API_KEY_ENV_MAP:
            raise AssertionError(
                f"configure_cloud_api_keys read {key!r} via sync get() — "
                "is_secret rows are absent from the sync cache and MUST "
                "go through get_secret"
            )
        return default

    sc = MagicMock()
    sc.get.side_effect = _sync_get
    sc.get_secret = AsyncMock(
        side_effect=lambda key, default="": secrets.get(key, default),
    )
    return sc


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stamps_anthropic_key_into_env():
    sc = _fake_site_config({"anthropic_api_key": "sk-ant-test-123"})
    stamped = await configure_cloud_api_keys(sc)
    assert stamped == ["ANTHROPIC_API_KEY"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-test-123"
    # Rows that were empty stamp nothing.
    assert "OPENAI_API_KEY" not in os.environ
    assert "GEMINI_API_KEY" not in os.environ


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stamps_multiple_providers():
    sc = _fake_site_config(
        {
            "anthropic_api_key": "sk-ant-a",
            "openai_api_key": "sk-oai-b",
            "gemini_api_key": "gm-c",
        }
    )
    stamped = await configure_cloud_api_keys(sc)
    assert sorted(stamped) == [
        "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY",
    ]
    assert os.environ["OPENAI_API_KEY"] == "sk-oai-b"
    assert os.environ["GEMINI_API_KEY"] == "gm-c"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_rows_are_the_normal_local_only_state():
    """A default local-only install has no cloud keys configured — the
    function must not raise, not stamp, and not warn-spam."""
    sc = _fake_site_config({})
    stamped = await configure_cloud_api_keys(sc)
    assert stamped == []
    for var in _ALL_ENV_VARS:
        assert var not in os.environ


@pytest.mark.unit
@pytest.mark.asyncio
async def test_whitespace_only_secret_is_skipped():
    sc = _fake_site_config({"anthropic_api_key": "   \n"})
    stamped = await configure_cloud_api_keys(sc)
    assert stamped == []
    assert "ANTHROPIC_API_KEY" not in os.environ


@pytest.mark.unit
@pytest.mark.asyncio
async def test_db_value_overwrites_stale_env(monkeypatch):
    """DB-first: a rotated key in app_settings beats whatever the
    container was started with — same contract as the Langfuse block."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-stale")
    sc = _fake_site_config({"anthropic_api_key": "sk-ant-fresh"})
    stamped = await configure_cloud_api_keys(sc)
    assert stamped == ["ANTHROPIC_API_KEY"]
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-fresh"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_empty_db_row_leaves_existing_env_alone(monkeypatch):
    """An operator exporting the env var by hand (no DB row) keeps
    working — the bridge only ADDS the DB value, it never clears env."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-manual")
    sc = _fake_site_config({})
    stamped = await configure_cloud_api_keys(sc)
    assert stamped == []
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-manual"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_none_site_config_is_a_noop():
    assert await configure_cloud_api_keys(None) == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_reads_via_get_secret_accessor():
    sc = _fake_site_config({"anthropic_api_key": "sk-ant-x"})
    await configure_cloud_api_keys(sc)
    called_keys = {c.args[0] for c in sc.get_secret.await_args_list}
    assert called_keys == set(_CLOUD_API_KEY_ENV_MAP)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_key_values_never_reach_logs(caplog):
    """Security contract: the log line names the env VARS that were set,
    never the secret values themselves."""
    secret = "sk-ant-super-secret-value-987"
    sc = _fake_site_config({"anthropic_api_key": secret})
    with caplog.at_level(logging.DEBUG):
        await configure_cloud_api_keys(sc)
    assert secret not in caplog.text
    assert "ANTHROPIC_API_KEY" in caplog.text


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_secret_failure_propagates():
    """A DB read failure is the caller's to handle (both call sites wrap
    in warn-and-continue) — the function itself must not swallow it."""
    sc = MagicMock()
    sc.get_secret = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(RuntimeError, match="db down"):
        await configure_cloud_api_keys(sc)
