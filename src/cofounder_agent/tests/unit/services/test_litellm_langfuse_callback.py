"""Unit tests for ``configure_langfuse_callback`` (poindexter#373).

Exercises the four acceptance criteria from the issue:

1. ``langfuse_tracing_enabled=false`` → no callback registered, env vars
   not stamped, no error raised. Lets the operator kill tracing without
   nuking prompt management.
2. ``langfuse_tracing_enabled=true`` + all credentials present → env
   vars stamped + ``litellm.success_callback`` / ``failure_callback``
   set to ``["langfuse"]``.
3. ``langfuse_tracing_enabled=true`` + missing credential → raises
   :class:`LangfuseConfigError`. Per ``feedback_no_silent_defaults``,
   no quiet skip.
4. Idempotent — calling twice doesn't re-register or raise.

The test stubs ``litellm`` via ``sys.modules`` so it runs without the
real package + isolates ``litellm.success_callback`` mutations between
tests via the ``_reset_module_state`` fixture.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Stub ``litellm`` before importing the provider so the lazy import
# resolves to our controllable mock regardless of whether the real
# package is installed in the test env.
_litellm_stub = MagicMock(name="litellm")
_litellm_stub.success_callback = []
_litellm_stub.failure_callback = []
sys.modules.setdefault("litellm", _litellm_stub)


from services.llm_providers import litellm_provider  # noqa: E402
from services.llm_providers.litellm_provider import (  # noqa: E402
    LangfuseConfigError,
    configure_langfuse_callback,
)


@pytest.fixture(autouse=True)
def _reset_module_state(monkeypatch):
    """Wipe global registration flag + env vars between tests so each
    test exercises a fresh start. Patches the ``litellm`` stub's
    callback lists so no test sees state leaked from another.
    """
    monkeypatch.setattr(
        litellm_provider, "_LANGFUSE_CALLBACK_REGISTERED", False,
    )
    for var in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    _litellm_stub.success_callback = []
    _litellm_stub.failure_callback = []
    yield


def _fake_site_config(
    *,
    enabled: bool = True,
    host: str = "http://localhost:3010",
    public_key: str = "pk-lf-test",
    secret_key: str = "sk-lf-test",
):
    """Build a mock SiteConfig with the four settings the function reads.

    ``get_secret`` is async, so it's an AsyncMock.
    """
    sc = MagicMock()
    sc.get_bool.return_value = enabled
    sc.get.side_effect = lambda key, default="": {
        "langfuse_host": host,
        "langfuse_public_key": public_key,
    }.get(key, default)
    sc.get_secret = AsyncMock(return_value=secret_key)
    return sc


@pytest.mark.asyncio
async def test_disabled_skips_registration_cleanly():
    """When ``langfuse_tracing_enabled=false``, the function returns
    False without touching ``litellm.success_callback`` or stamping
    env vars. This is the operator's kill switch.
    """
    sc = _fake_site_config(enabled=False)
    result = await configure_langfuse_callback(sc)
    assert result is False
    assert _litellm_stub.success_callback == []
    assert _litellm_stub.failure_callback == []
    assert "LANGFUSE_HOST" not in os.environ
    # Secret should not even be fetched when disabled.
    sc.get_secret.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_with_all_credentials_registers_callback():
    """Happy path — all three credentials present + tracing enabled.

    Verifies env vars get stamped (LiteLLM's Langfuse integration reads
    them on first callback fire) AND the success/failure callback lists
    get set to ``["langfuse"]``.
    """
    sc = _fake_site_config()
    result = await configure_langfuse_callback(sc)
    assert result is True
    assert _litellm_stub.success_callback == ["langfuse_otel"]
    assert _litellm_stub.failure_callback == ["langfuse_otel"]
    assert os.environ["LANGFUSE_HOST"] == "http://localhost:3010"
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-lf-test"
    assert os.environ["LANGFUSE_SECRET_KEY"] == "sk-lf-test"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_field,kwargs",
    [
        ("langfuse_host", {"host": ""}),
        ("langfuse_public_key", {"public_key": ""}),
        ("langfuse_secret_key", {"secret_key": ""}),
    ],
)
async def test_missing_credential_raises_loud_error(missing_field, kwargs):
    """Per ``feedback_no_silent_defaults``: a missing credential with
    tracing enabled MUST raise — never quietly skip registration.
    """
    sc = _fake_site_config(**kwargs)
    with pytest.raises(LangfuseConfigError, match=missing_field):
        await configure_langfuse_callback(sc)
    # Callback lists must remain untouched on failure.
    assert _litellm_stub.success_callback == []
    assert _litellm_stub.failure_callback == []


@pytest.mark.asyncio
async def test_idempotent_double_call():
    """Calling twice doesn't re-register, doesn't raise, returns True
    both times. Refreshes env vars on the second call so credential
    rotation propagates without a worker restart.
    """
    sc = _fake_site_config()
    first = await configure_langfuse_callback(sc)
    second = await configure_langfuse_callback(sc)
    assert first is True
    assert second is True
    assert _litellm_stub.success_callback == ["langfuse_otel"]
    # Should have been fetched twice (env-var refresh path).
    assert sc.get_secret.await_count == 2


@pytest.mark.asyncio
async def test_none_site_config_skips_silently():
    """When called outside the worker (CLI scripts, test harnesses
    that don't construct a SiteConfig), the function logs + returns
    False without raising. Keeps unit-test paths green.
    """
    result = await configure_langfuse_callback(None)
    assert result is False
    assert _litellm_stub.success_callback == []


@pytest.mark.asyncio
async def test_secret_fetch_exception_wrapped():
    """If ``site_config.get_secret`` itself raises (e.g. DB down), the
    error is wrapped in :class:`LangfuseConfigError` with the original
    chained — operator gets one clean error type to catch.
    """
    sc = _fake_site_config()
    sc.get_secret = AsyncMock(side_effect=RuntimeError("db down"))
    with pytest.raises(LangfuseConfigError, match="langfuse_secret_key"):
        await configure_langfuse_callback(sc)
